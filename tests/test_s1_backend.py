"""S1-B 后端安全/并发回归测试：

IPv6 回环 Host/Origin 放行 / delete_preset 路径穿越拒绝 / config 深拷贝防污染 /
usage 按次上下文隔离（run·trial·regen 不串账）/ review·regen 运行中 409 / 原子写 helper。
"""

import sys
import threading
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.main as m  # noqa: E402
from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import usage
from medkit.core.fsutil import write_json_atomic  # noqa: E402


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


def _isolate_cfg(monkeypatch, tmp_path) -> dict:
    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    saved["api_key"] = "sk-test"
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(m.cfg, "load", lambda: dict(saved))
    monkeypatch.setattr(m.cfg, "save", lambda c: saved.update(c))
    return saved


def test_host_ipv6_loopback_allowed(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    c = _client()
    # [::1] 回环（带/不带端口）应放行（旧实现 hostname='[' 永远 403）
    for host in ("[::1]:4880", "[::1]"):
        r = c.get("/api/health", headers={"host": host})
        assert r.status_code == 200, (host, r.text)
    # 非回环 IPv6 → 403
    r = c.get("/api/health", headers={"host": "[::2]:4880"})
    assert r.status_code == 403, r.text
    # IPv6 同源 Origin 放行（项目不存在 → 404，说明守卫通过）
    r = c.post("/api/projects/nope/run",
               headers={"host": "[::1]:4880", "origin": "http://[::1]:4880"})
    assert r.status_code == 404, r.text


def test_delete_preset_rejects_traversal():
    from fastapi import HTTPException as HE

    for bad in ("..", "../x", "../../config", "a\\b", "..\\..\\config.json"):
        try:
            m.delete_preset(bad)
            raise AssertionError(f"delete_preset 应拒绝 {bad!r}")
        except HE as e:
            assert e.status_code == 400, (bad, e.status_code)


def test_config_load_corrupt_backs_up_before_defaults(monkeypatch, tmp_path):
    """配置损坏：load 回退默认值，但先把原始文件备份成 .corrupt-*.bak 以抢救 Key。"""
    conf = tmp_path / "config.json"
    conf.write_text("{ 这不是合法 JSON !!!", encoding="utf-8")
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", conf)
    c = cfgmod.load()
    assert c["provider"] in ("deepseek",), "损坏配置应回退默认值"
    baks = list(tmp_path.glob("config.json.corrupt-*.bak"))
    assert baks, "损坏的原始 config.json 应被备份保留"


def test_config_load_deepcopy_no_shared_nested(monkeypatch, tmp_path):
    """嵌套 dict 不应被 load 的 update 污染模块级 DEFAULTS。"""
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "nonexistent.json")
    c1 = cfgmod.load()
    c1["web_search"]["enabled"] = True
    c1["mineru"]["auto_ocr"] = False
    c2 = cfgmod.load()
    assert c2["web_search"]["enabled"] is False, "DEFAULTS 嵌套 dict 被污染（应 deepcopy）"
    assert c2["mineru"]["auto_ocr"] is True
    assert cfgmod.DEFAULTS["web_search"]["enabled"] is False


def test_usage_contexts_isolated():
    """run / trial / regen 各自独立账本：互不串账。"""
    with usage.context() as run_ctx:
        usage.add(100, 200)
        with usage.context() as trial_ctx:
            usage.add(1, 2)
            assert trial_ctx.snapshot() == {"prompt_tokens": 1, "completion_tokens": 2}, \
                "trial 不应看到 run 的 token"
        assert run_ctx.snapshot() == {"prompt_tokens": 100, "completion_tokens": 200}, \
            "run 账本不应被 trial 污染"
    # 退出上下文后：线程默认账本独立
    usage.add(5, 5)
    assert usage.snapshot() == {"prompt_tokens": 5, "completion_tokens": 5}


def test_review_regen_conflict_when_running(monkeypatch, tmp_path):
    _isolate_cfg(monkeypatch, tmp_path)
    pid = "_conflict_test"
    ev = threading.Event()
    m.RUNNING[pid] = ev
    try:
        c = _client()
        r = c.post(f"/api/projects/{pid}/questions/review", json={"keep": []})
        assert r.status_code == 409, r.text
        r = c.post(f"/api/projects/{pid}/regen", json={"id": "Q001"})
        assert r.status_code == 409, r.text
    finally:
        m.RUNNING.pop(pid, None)
    # 未运行 → 走正常流程（项目不存在 → 404/422，证明 409 已解除）
    c = _client()
    r = c.post("/api/projects/_conflict_test/questions/review", json={"keep": []})
    assert r.status_code in (404, 422), r.text


def test_review_rejects_answer_outside_option_range(monkeypatch, tmp_path):
    """R3-06：3 选项题答案 D 必须被后端拒绝（去掉 letters max(...,4) 地板）。"""
    import json as _json

    _isolate_cfg(monkeypatch, tmp_path)
    from medkit.routers._common import _write_meta_atomic, proj_dir
    import medkit.render.apkg as apkg_mod
    monkeypatch.setattr(apkg_mod, "export_apkg", lambda *a, **k: None)

    pid = "review_letters"
    base = proj_dir(pid)
    (base / "最终产物").mkdir(parents=True)
    _write_meta_atomic(base, {"subject": "儿科学",
                              "toggles": {"qbank": True, "paper": True, "review": False}})
    qs = [{"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "章",
           "question": "题？", "options": ["A", "B", "C"], "answer": "C",
           "analysis": "解析"}]
    (base / "最终产物" / "questions_final.json").write_text(_json.dumps(qs), encoding="utf-8")

    c = _client()
    r = c.post(f"/api/projects/{pid}/questions/review",
               json={"keep": ["Q1"], "edits": [{"id": "Q1", "answer": "D"}]})
    assert r.status_code == 400, r.text
    assert "答案键有误" in r.json()["detail"]

    r2 = c.post(f"/api/projects/{pid}/questions/review",
                json={"keep": ["Q1"], "edits": [{"id": "Q1", "answer": "C"}]})
    assert r2.status_code == 200, r2.text

    # C-10：keep=[] 明确剔除全部 → 400；未传 keep → 全保留
    r3 = c.post(f"/api/projects/{pid}/questions/review", json={"keep": []})
    assert r3.status_code == 400 and "保留题数为 0" in r3.json()["detail"]
    r4 = c.post(f"/api/projects/{pid}/questions/review", json={"edits": []})
    assert r4.status_code == 200, r4.text


def test_review_concurrent_edits_no_lost_update(monkeypatch, tmp_path):
    """R3-07：per-pid 锁——并发保存不同题的编辑不得互相覆盖（丢编辑）。"""
    import json as _json
    import threading as _th

    _isolate_cfg(monkeypatch, tmp_path)
    from medkit.routers._common import _write_meta_atomic, proj_dir
    import medkit.render.apkg as apkg_mod
    monkeypatch.setattr(apkg_mod, "export_apkg", lambda *a, **k: None)

    pid = "review_lock"
    base = proj_dir(pid)
    (base / "最终产物").mkdir(parents=True)
    _write_meta_atomic(base, {"subject": "儿科学",
                              "toggles": {"qbank": True, "paper": True, "review": False}})
    qs = [{"id": f"Q{i}", "type": "A1", "bloom": "理解", "subtopic": "章",
           "question": f"题{i}？", "options": ["A", "B", "C", "D", "E"],
           "answer": "A", "analysis": "解析"} for i in range(1, 6)]
    f = base / "最终产物" / "questions_final.json"
    f.write_text(_json.dumps(qs), encoding="utf-8")

    results: list[tuple[int, int]] = []

    def worker(i: int) -> None:
        cc = _client()
        r = cc.post(f"/api/projects/{pid}/questions/review",
                    json={"keep": [f"Q{j}" for j in range(1, 6)],
                          "edits": [{"id": f"Q{i}", "question": f"编辑后题{i}"}]})
        results.append((i, r.status_code))

    threads = [_th.Thread(target=worker, args=(i,)) for i in range(1, 6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(s == 200 for _, s in results), results
    final = _json.loads(f.read_text(encoding="utf-8"))
    for i in range(1, 6):
        q = next(x for x in final if x["id"] == f"Q{i}")
        assert q["question"] == f"编辑后题{i}", "并发保存不得丢编辑"


def test_create_project_double_click_dedupe(monkeypatch, tmp_path):
    """R3-08：同一 client_token 的重复提交（双击）→ 幂等去重，只建一个项目、只扣一次配额。"""
    _isolate_cfg(monkeypatch, tmp_path)
    c = _client()
    body = {
        "client_token": "ct-double-click-test",
        "subject": "儿科学", "exam": "期末", "target": 20,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "textbook_slices": [{"sid": "S001", "title": "第一章", "text": "教材内容" * 50}],
        "teacher_slices": [{"sid": "T001", "title": "教师重点", "text": "重点内容" * 20}],
        "teacher_text": "教师重点文本",
    }
    r1 = c.post("/api/projects", json=body)
    r2 = c.post("/api/projects", json=body)
    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text
    assert r1.json()["pid"] == r2.json()["pid"], "双击应复用同一项目"
    assert r2.json().get("reused") is True
    dirs = [d for d in (tmp_path / "projects").iterdir() if d.is_dir()]
    assert len(dirs) == 1, f"应只建一个项目目录：{[d.name for d in dirs]}"


def test_create_project_concurrent_double_submit_single_project(monkeypatch, tmp_path):
    """R3-08：同一 client_token 并发双提交 → per-subject 锁 + 幂等，仍只建一个项目。"""
    import threading as _th

    _isolate_cfg(monkeypatch, tmp_path)
    body = {
        "client_token": "ct-concurrent-test",
        "subject": "内科学", "exam": "期末", "target": 20,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "textbook_slices": [{"sid": "S001", "title": "第一章", "text": "教材内容" * 50}],
        "teacher_slices": [{"sid": "T001", "title": "教师重点", "text": "重点内容" * 20}],
        "teacher_text": "教师重点文本",
    }
    barrier = _th.Barrier(2)
    pids: list[str] = []
    statuses: list[int] = []

    def worker() -> None:
        cc = _client()
        barrier.wait()
        r = cc.post("/api/projects", json=body)
        statuses.append(r.status_code)
        if r.status_code == 200:
            pids.append(r.json()["pid"])

    threads = [_th.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200, 200], statuses
    assert pids[0] == pids[1], "并发双提交应落到同一项目"
    dirs = [d for d in (tmp_path / "projects").iterdir() if d.is_dir()]
    assert len(dirs) == 1, f"应只建一个项目目录：{[d.name for d in dirs]}"


def test_rerender_preserves_images(monkeypatch, tmp_path):
    """R3S-02：审核台重渲染必须重建 image_index——图题重渲染后图仍在（初跑管线传索引）。"""
    import base64 as _b64  # noqa: F401
    import json as _json

    _isolate_cfg(monkeypatch, tmp_path)
    from medkit.routers._common import _write_meta_atomic, proj_dir

    pid = "rerender_img"
    base = proj_dir(pid)
    (base / "最终产物").mkdir(parents=True)
    (base / "assets").mkdir(parents=True)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    (base / "assets" / "fig_1.png").write_bytes(png)
    _write_meta_atomic(base, {"subject": "儿科学",
                              "toggles": {"qbank": True, "paper": True, "review": True}})
    (base / "slices.json").write_text(_json.dumps(
        [{"sid": "IMG1", "role": "image", "text": "心电图",
          "image": {"path": "assets/fig_1.png"}}]), encoding="utf-8")
    qs = [{"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "章",
           "question": "如图所示，诊断是？", "options": ["A", "B", "C", "D", "E"],
           "answer": "A", "analysis": "解析 【源:切片S001】", "image_ref": "IMG1"}]
    out_dir = base / "最终产物"
    (out_dir / "questions_final.json").write_text(_json.dumps(qs), encoding="utf-8")

    c = _client()
    r = c.post(f"/api/projects/{pid}/rerender", json={"what": "qbank"})
    assert r.status_code == 200, r.text
    html = (out_dir / "qbank.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in html, "重渲染后图题图片应内嵌（R3S-02）"

    r2 = c.post(f"/api/projects/{pid}/rerender", json={"what": "paper"})
    assert r2.status_code == 200, r2.text
    paper = (out_dir / "押题卷.html").read_text(encoding="utf-8")
    assert "data:image/png;base64," in paper, "押题卷重渲染后图题图片应内嵌"


def test_fsutil_atomic_write(tmp_path):
    target = tmp_path / "sub" / "data.json"
    write_json_atomic(target, {"a": [1, 2], "b": {"c": "中"}})
    import json

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2], "b": {"c": "中"}}
    # 覆盖写 + 无 tmp 残留
    write_json_atomic(target, {"a": [3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [3]}


def test_rerender_single_artifact(monkeypatch, tmp_path):
    """B17：仅重渲染单个产物——只产出指定文件，不重跑其它渲染。"""
    import json

    _isolate_cfg(monkeypatch, tmp_path)
    from medkit.routers._common import _write_meta_atomic, proj_dir

    pid = "rerender_t1"
    base = proj_dir(pid)
    (base / "最终产物").mkdir(parents=True)
    _write_meta_atomic(base, {"subject": "儿科学",
                              "toggles": {"qbank": True, "paper": True, "review": True}})
    qs = [{"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "章",
           "question": "题1？", "options": ["A", "B", "C", "D", "E"],
           "answer": "A", "analysis": "解析 [源:切片S001]"}]
    out_dir = base / "最终产物"
    (out_dir / "questions_final.json").write_text(json.dumps(qs), encoding="utf-8")
    (out_dir / "复习手册.md").write_text("# 复习手册\n\n## 考点\n- 内容", encoding="utf-8")

    c = _client()
    r = c.post(f"/api/projects/{pid}/rerender", json={"what": "qbank"})
    assert r.status_code == 200, r.text
    assert "qbank.html" in r.json()["rendered"]
    assert (out_dir / "qbank.html").exists()
    assert not (out_dir / "押题卷.html").exists(), "仅重渲染题库，不应产出押题卷"

    r2 = c.post(f"/api/projects/{pid}/rerender", json={"what": "review"})
    assert r2.status_code == 200 and "复习手册.html" in r2.json()["rendered"]

    r3 = c.post(f"/api/projects/{pid}/rerender", json={"what": "paper"})
    assert r3.status_code == 200 and "押题卷.html" in r3.json()["rendered"]

    # 未生成题库 → 404
    r4 = c.post("/api/projects/rerender_none/rerender", json={"what": "qbank"})
    assert r4.status_code == 404
    leftovers = [p.name for p in tmp_path.rglob("*.tmp*")]
    assert not leftovers, f"临时文件未清理：{leftovers}"


def test_fsutil_read_json_list_contract(tmp_path):
    """P2#11：read_json_list 统一容错 —— 缺失/损坏/非列表均回退空，列表原样返回。"""
    import json as _json

    from medkit.core.fsutil import read_json_list

    missing = tmp_path / "missing.json"
    assert read_json_list(missing) == [], "缺失文件 → 空列表"
    bad = tmp_path / "bad.json"
    bad.write_text("{ 这不是 json", encoding="utf-8")
    assert read_json_list(bad) == [], "损坏内容 → 空列表"
    obj = tmp_path / "obj.json"
    obj.write_text('{"a":1}', encoding="utf-8")
    assert read_json_list(obj) == [], "非列表 → 空列表"
    ok = tmp_path / "ok.json"
    ok.write_text(_json.dumps([{"id": "Q1"}, {"id": "Q2"}], ensure_ascii=False), encoding="utf-8")
    assert read_json_list(ok) == [{"id": "Q1"}, {"id": "Q2"}], "合法数组 → 原样返回"


def test_ocr_cancel_race_keeps_cancelled(monkeypatch, tmp_path):
    """F1：识别完成时 cancel 已置位 → 不得覆写 cancelled 终态（worker 竞态）。"""

    class FakeMinerU:
        def __init__(self, key):
            pass

        def extract(self, path, progress=None, cancel=None):
            cancel.set()  # 模拟识别中途取消
            return "# 识别结果"

        def mode(self):
            return "agent"

    from medkit.core import mineru as mineru_mod

    monkeypatch.setattr(mineru_mod, "MinerUClient", lambda key: FakeMinerU(key))
    job = {"id": "ocr_race", "name": "x.pdf", "role": "textbook", "state": "queued",
           "msg": "", "result": None, "cancel": threading.Event()}
    m.OCR_JOBS[job["id"]] = job
    try:
        m._run_ocr_job(job, str(tmp_path / "x.pdf"), "x.pdf", ".pdf")
        st = m.OCR_JOBS[job["id"]]
        assert st["state"] == "cancelled", f"取消后终态被覆写：{st}"
        assert st["result"] is None, "取消结果不应被采用"
    finally:
        m.OCR_JOBS.pop(job["id"], None)


def test_create_project_same_second_not_merged(monkeypatch, tmp_path):
    """F3：同秒同名项目不得静默合并（pid 唯一化 + mkdir 不覆盖）。"""
    saved = _isolate_cfg(monkeypatch, tmp_path)
    body = {
        "subject": "儿科防合并", "exam": "期末", "target": 20,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "teacher_text": "生长发育 3.25kg",
        "textbook_slices": [
            {"sid": "S001", "title": "第一章", "text": "生长发育有三个高峰，出生体重3.25kg。" * 20}],
        "teacher_slices": [{"sid": "T001", "title": "重点", "text": "生长发育 3.25kg"}],
        "exam_slices": [],
    }
    c = _client()
    r1 = c.post("/api/projects", json=body)
    r2 = c.post("/api/projects", json=body)
    assert r1.status_code == 200 and r2.status_code == 200
    p1, p2 = r1.json()["pid"], r2.json()["pid"]
    assert p1 != p2, "同秒同名项目 pid 应唯一（不得静默合并）"
    assert (Path(saved["projects_dir"]) / p1).is_dir() and (Path(saved["projects_dir"]) / p2).is_dir()
