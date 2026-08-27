"""IMP-02 Feature Flag 机制测试（state.flag + routers 门禁 + config features 节）。

隔离：monkeypatch cfg.CONFIG_FILE/CONFIG_DIR 指向 tmp，不触碰真实 ~/.medkit；
state.FLAGS 为进程内覆盖，每例清空。无任何真实网络调用。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit import state
from medkit.core import config as cfgmod
from medkit.core.config import DEFAULTS


@pytest.fixture(autouse=True)
def _isolate_config(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    cfgmod.CONFIG_FILE.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
    # FLAGS 为进程内覆盖：先行清空，避免跨用例泄漏；结束后还原
    saved = dict(state.FLAGS)
    state.FLAGS.clear()
    yield
    state.FLAGS.clear()
    state.FLAGS.update(saved)


def _write_features(cfg: dict) -> None:
    cfgmod.CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- state.flag 单元
def test_flag_default_true_without_features():
    assert state.flag("syllabus") is True
    assert state.flag("realexams") is True
    assert state.flag("gap") is True
    assert state.flag("image_q") is True
    assert state.flag("not_a_flag") is True   # 未知名 = 放行（现状兼容）


def test_flag_reads_features_section():
    _write_features({"features": {"syllabus": False, "gap": True}})
    assert state.flag("syllabus") is False
    assert state.flag("gap") is True
    assert state.flag("realexams") is True    # 未出现在 features 节 = 缺省 True


def test_flag_flags_overrides_config():
    _write_features({"features": {"syllabus": False}})
    state.FLAGS["syllabus"] = True            # 进程内覆盖优先
    assert state.flag("syllabus") is True
    state.FLAGS["syllabus"] = False
    assert state.flag("syllabus") is False


def test_flag_corrupt_config_fails_open():
    cfgmod.CONFIG_FILE.write_text("{ 坏 json", encoding="utf-8")
    assert state.flag("syllabus") is True     # 配置损坏一律放行（关闭不该拖垮功能）


# ---------------------------------------------------------------- routers 门禁（404）
def test_api_gate_404_when_disabled():
    _write_features({"features": {"syllabus": False, "gap": False, "realexams": False}})
    client = TestClient(m.app, base_url="http://127.0.0.1")
    assert client.get("/api/syllabus/status").status_code == 404
    assert client.post("/api/syllabus/parse", json={"text": "一、呼吸系统\n1、肺通气"}).status_code == 404
    assert client.get("/api/syllabus/tree").status_code == 404
    assert client.post("/api/library/gap-paper", json={"subject": "内科学"}).status_code == 404
    assert client.get("/api/library/realexams/freq").status_code == 404
    # 无关端点不受影响
    assert client.get("/api/health").status_code == 200


def test_api_gate_restored_when_features_removed():
    _write_features({"features": {"syllabus": False}})
    client = TestClient(m.app, base_url="http://127.0.0.1")
    assert client.get("/api/syllabus/status").status_code == 404
    _write_features({})                        # 删除 features 节 → 恢复默认全开
    assert client.get("/api/syllabus/status").status_code == 200


def test_api_config_public_view_exposes_features():
    _write_features({"features": {"syllabus": False}})
    client = TestClient(m.app, base_url="http://127.0.0.1")
    j = client.get("/api/config").json()
    assert j["features"] == {"syllabus": False}


def test_put_config_preserves_features():
    _write_features({"features": {"syllabus": False, "gap": True}})
    client = TestClient(m.app, base_url="http://127.0.0.1")
    body = {k: DEFAULTS.get(k, "") for k in
            ("provider", "base_url", "api_key", "model_gen", "model_qc")}
    body.update({"provider": "deepseek", "base_url": "https://api.deepseek.com",
                 "model_gen": "deepseek-v4-flash", "model_qc": "deepseek-v4-flash",
                 "web_search_enabled": False, "web_search_backend": "auto",
                 "mineru_auto_ocr": True})
    r = client.put("/api/config", json=body)
    assert r.status_code == 200
    saved = json.loads(cfgmod.CONFIG_FILE.read_text(encoding="utf-8"))
    assert saved["features"] == {"syllabus": False, "gap": True}   # 不被 PUT 清空
