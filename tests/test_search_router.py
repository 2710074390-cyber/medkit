"""WP-6：/api/search/test 四个后端 mock 全绿 + 失败原因中文化 + 可信配置往返。"""

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import websearch as ws


@pytest.fixture()
def iso(monkeypatch, tmp_path):
    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    saved["api_key"] = "sk-test"
    saved["provider"] = "deepseek"
    saved["base_url"] = "https://api.deepseek.com"
    saved["model_gen"] = "deepseek-v4-flash"
    saved["model_qc"] = "deepseek-v4-flash"
    monkeypatch.setattr(cfgmod, "load", lambda: dict(saved))
    monkeypatch.setattr(cfgmod, "save", lambda c: saved.update(c))
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    return {"saved": saved}


def _client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_search_test_four_backends_mock(iso, monkeypatch):
    monkeypatch.setattr(ws, "search_deepseek",
                        lambda q, k, model="": [{"title": "DeepSeek指南", "url": "https://who.int/d", "snippet": "s"}])
    monkeypatch.setattr(ws, "search_zhipu",
                        lambda q, k, model="": [{"title": "智谱指南", "url": "https://who.int/z", "snippet": "s"}])
    monkeypatch.setattr(ws, "search_qwen",
                        lambda q, k, model="": [{"title": "千问指南", "url": "https://who.int/q", "snippet": "s"}])
    monkeypatch.setattr(ws, "search_bocha",
                        lambda q, k: [{"title": "博查指南", "url": "https://who.int/b", "snippet": "s"}])
    c = _client()
    for backend in ("deepseek_tool", "zhipu_tool", "qwen_tool", "bocha"):
        r = c.post("/api/search/test", json={"backend": backend, "api_key": "bk-1"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True, data
        assert data["count"] == 1, data


def test_search_test_error_hint_chinese(iso, monkeypatch):
    monkeypatch.setattr(ws, "search_deepseek",
                        lambda q, k, model="": (_ for _ in ()).throw(TimeoutError("Request timed out after 30s")))
    c = _client()
    r = c.post("/api/search/test", json={"backend": "deepseek_tool"})
    data = r.json()
    assert data["ok"] is False
    assert "超时" in data["msg"], data


def test_search_test_error_hint_branches(iso, monkeypatch):
    """C-04：_search_error_hint 分支全覆盖（401/403/400/网络）——此前只测了 timeout。"""
    cases = [
        (PermissionError("401 Unauthorized: invalid api key"), "401"),
        (PermissionError("403 Forbidden: not authorized"), "403"),
        (ValueError("400 Bad Request: unsupported model"), "请求参数"),
        (ConnectionRefusedError("connection refused"), "网络"),
    ]
    c = _client()
    for exc, expect in cases:
        monkeypatch.setattr(ws, "search_deepseek", lambda q, k, model="", _e=exc: (_ for _ in ()).throw(_e))
        r = c.post("/api/search/test", json={"backend": "deepseek_tool"})
        data = r.json()
        assert data["ok"] is False, data
        assert expect in data["msg"], f"{expect} 应命中中文归因：{data['msg']}"


def test_search_test_zero_results_hint(iso, monkeypatch):
    """C-04：服务端连通但 0 结果 → 明确提示（「已连通但未提取」而非失败）。"""
    monkeypatch.setattr(ws, "search_deepseek", lambda q, k, model="": [])
    data = _client().post("/api/search/test", json={"backend": "deepseek_tool"}).json()
    assert data["ok"] is True and data["count"] == 0
    assert "已连通" in data["msg"] and "未提取到结果" in data["msg"], data


def test_config_trusted_fields_roundtrip(iso):
    c = _client()
    r = c.put("/api/config", json={
        "provider": "deepseek", "base_url": "https://api.deepseek.com",
        "api_key": "sk-x", "model_gen": "deepseek-v4-flash", "model_qc": "deepseek-v4-flash",
        "web_search_enabled": True, "web_search_api_key": "",
        "web_search_backend": "auto", "web_search_trusted_only": True,
        "web_search_trusted_domains": "who.int, gov.cn；nhc.gov.cn",
        "mineru_api_key": "", "mineru_auto_ocr": True,
    })
    assert r.status_code == 200, r.text
    got = c.get("/api/config").json()["web_search"]
    assert got["trusted_only"] is True
    assert "who.int" in got["trusted_domains"]
    assert "gov.cn" in got["trusted_domains"]
    assert "nhc.gov.cn" in got["trusted_domains"]
