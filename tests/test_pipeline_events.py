"""WP-3：门禁 subagent 分步可视化事件模型（substeps.jsonl + _run_substep 超时重试）单元测试。

覆盖：事件追加/200 行裁剪、成功/超时重试/重试后成功/降级回调、路由读取与 status 返回。
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
    res, err = _run_substep(base, "qc", "batch1", "质检批次 1/1", lambda: 42, ttl=2)
    assert res == 42 and err is None
    events = [json.loads(x) for x in (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["status"] for e in events] == ["running", "done"]


def test_run_substep_timeout_retry_then_fallback(tmp_path):
    import time

    from medkit.core.orchestrator import _run_substep

    base = _mk_base(tmp_path)

    def slow():
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

    def flaky():
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

    def bad():
        raise ValueError("nope")

    res, err = _run_substep(base, "gate1", "options", "选项校验", bad, ttl=2, retries=1,
                            on_fail=lambda e: hits.append(str(e)))
    assert res is None and err is not None
    assert hits == ["nope"]


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
