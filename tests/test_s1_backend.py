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


def test_fsutil_atomic_write(tmp_path):
    target = tmp_path / "sub" / "data.json"
    write_json_atomic(target, {"a": [1, 2], "b": {"c": "中"}})
    import json

    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [1, 2], "b": {"c": "中"}}
    # 覆盖写 + 无 tmp 残留
    write_json_atomic(target, {"a": [3]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"a": [3]}
    leftovers = [p.name for p in tmp_path.rglob("*.tmp*")]
    assert not leftovers, f"临时文件未清理：{leftovers}"
