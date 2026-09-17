"""WP-3：门禁 subagent 分步可视化事件模型（substeps.jsonl + _run_substep 超时重试）单元测试。

覆盖：事件追加/200 行裁剪、成功/超时重试/重试后成功/降级回调、路由读取与 status 返回。
RV2/RV3（2026-09-17 审查）：在飞登记按 pid 分桶不串项目；超时置位本次尝试专属取消事件；
项目级 cancel 置位后不再重试。
"""

import json
from pathlib import Path


def _mk_base(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    return base


def test_substep_appends_and_trims(tmp_path):
    from medkit.core.orchestrator import _substep

    base = _mk_base(tmp_path)
    for i in range(260):
        _substep(base, "gate1", f"step{i}", f"检查 {i}", "done", detail=str(i))
    lines = (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 200, f"应保留最近 200 行（实得 {len(lines)}）"
    events = [json.loads(line) for line in lines]
    assert events[0]["detail"] == "60"
    assert events[-1]["detail"] == "259"
    assert set(events[0]) >= {"stage", "step", "label", "status", "detail", "ts"}


def test_run_substep_success(tmp_path):
    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)
    res, err = _run_substep(base, "qc", "batch1", "质检批次 1/1", lambda ev: 42, ttl=2)
    assert res == 42 and err is None
    events = [json.loads(x) for x in (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["status"] for e in events] == ["running", "done"]


def test_run_substep_timeout_retry_then_fallback(tmp_path):
    import time

    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)

    def slow(ev):
        time.sleep(0.6)
        return "ok"

    res, err = _run_substep(base, "qc", "batch1", "质检批次 1/1", slow, ttl=0.05, retries=2)
    assert res is None
    assert err is not None and isinstance(err, TimeoutError)
    events = [json.loads(x) for x in (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = [e["status"] for e in events]
    assert statuses.count("running") == 3
    assert statuses.count("failed") >= 2
    assert "retry" in statuses
    assert events[-1]["status"] == "failed"


def test_run_substep_retry_then_success(tmp_path):
    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)
    state = {"n": 0}

    def flaky(ev):
        state["n"] += 1
        if state["n"] < 2:
            raise RuntimeError("boom")
        return "ok"

    res, err = _run_substep(base, "qc", "batch1", "质检批次 1/1", flaky, ttl=2, retries=2)
    assert res == "ok" and err is None
    events = [json.loads(x) for x in (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
    statuses = [e["status"] for e in events]
    assert statuses[:3] == ["running", "failed", "retry"]
    assert statuses[-2:] == ["running", "done"]


def test_run_substep_fallback_calls_on_fail(tmp_path):
    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)
    hits = []

    def bad(ev):
        raise ValueError("nope")

    res, err = _run_substep(base, "gate1", "options", "选项校验", bad, ttl=2, retries=1,
                            on_fail=lambda e: hits.append(str(e)))
    assert res is None and err is not None
    assert hits == ["nope"]


def test_substeps_inflight_isolated_per_project(tmp_path):
    """RV2：在飞登记按 pid 分桶——A 项目 terminate 不得清除/误写 B 项目的子步骤。"""
    import medkit.core.orchestrator as orch

    base_a = tmp_path / "a"          # 目录名即 pid——两个项目必须不同名
    base_b = tmp_path / "b"
    base_a.mkdir()
    base_b.mkdir()
    orch._SUBSTEP_INFLIGHT.clear()
    try:
        orch._substep(base_a, "gate1", "options", "选项校验", "running")
        orch._substep(base_b, "qc", "medqc", "质检", "running")
        assert "gate1:options" in orch._SUBSTEP_INFLIGHT["a"]
        assert "qc:medqc" in orch._SUBSTEP_INFLIGHT["b"]
        # A 项目异常出口 terminate：只清 A 的在飞登记
        orch._substeps_terminate(base_a, "failed", "管线异常退出")
        assert "a" not in orch._SUBSTEP_INFLIGHT          # A 桶整体回收
        assert "qc:medqc" in orch._SUBSTEP_INFLIGHT["b"]  # B 不受影响
        # 终态事件写进各自项目的 substeps.jsonl（A 的文件里有 failed，B 的没有）
        sa = [json.loads(x) for x in (base_a / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
        assert any(e["status"] == "failed" and e["step"] == "options" for e in sa)
        sb = [json.loads(x) for x in (base_b / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
        assert all(e["status"] != "failed" for e in sb)
    finally:
        orch._SUBSTEP_INFLIGHT.clear()


def test_run_substep_timeout_sets_attempt_cancel_event(tmp_path):
    """RV3：超时即置位本次尝试专属取消事件——fn（LLM 调用）可协作提前退出。"""
    import time

    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)
    seen = []

    def slow(ev):
        seen.append(ev)
        time.sleep(0.6)
        return "ok"

    res, err = _run_substep(base, "qc", "medqc", "质检", slow, ttl=0.05, retries=0)
    assert res is None and isinstance(err, TimeoutError)
    assert len(seen) == 1 and seen[0].is_set(), "超时后应置位该次尝试的取消事件"


def test_run_substep_cancel_stops_retries(tmp_path):
    """RV3：项目级 cancel 置位 → 立即按取消出口返回，不再发起新尝试。"""
    import threading

    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)
    calls = []
    cancel = threading.Event()
    cancel.set()

    def fn(ev):
        calls.append(1)
        return "ok"

    res, err = _run_substep(base, "qc", "medqc", "质检", fn, ttl=2, retries=2, cancel=cancel)
    assert res is None and err is None      # 取消不是失败
    assert calls == []                       # 一次都没执行
    events = [json.loads(x) for x in (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events and events[-1]["status"] == "cancelled"


def test_read_substeps_recent_limit(tmp_path):
    from medkit.routers.projects import _read_substeps

    base = _mk_base(tmp_path)
    lines = [json.dumps({"stage": "gate1", "step": str(i), "label": "检查",
                         "status": "done", "ts": "x"}) for i in range(60)]
    (base / "substeps.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    rows = _read_substeps(base, limit=50)
    assert len(rows) == 50
    assert rows[-1]["step"] == "59"


def test_project_status_includes_substeps(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import medkit.main as m
    from medkit.core import config as cfgmod

    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    monkeypatch.setattr(cfgmod, "load", lambda: dict(saved))
    monkeypatch.setattr(cfgmod, "save", lambda c: saved.update(c))
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    pid = "demo"
    base = Path(saved["projects_dir"]) / pid
    base.mkdir(parents=True)
    (base / "meta.json").write_text(json.dumps({
        "pid": pid, "subject": "儿科", "exam": "期末", "stage": "gate1",
        "toggles": {"qbank": True, "paper": True, "review": True},
    }), encoding="utf-8")
    (base / "substeps.jsonl").write_text(json.dumps({
        "stage": "gate1", "step": "options", "label": "选项校验",
        "status": "running", "detail": "第 1 轮", "ts": "2026-01-01T00:00:00",
    }) + "\n", encoding="utf-8")
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/projects/demo/status")
    assert r.status_code == 200
    data = r.json()
    assert data["substeps"][0]["label"] == "选项校验"
    assert data["substeps"][0]["status"] == "running"
