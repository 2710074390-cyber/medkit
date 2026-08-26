"""v0.6 更新检查测试：版本比较纯逻辑 + check() 网络层 mock + 端点。

不发起任何真实网络调用。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.update as upd  # noqa: E402
import medkit.main as m  # noqa: E402
from medkit.core.update import _version_tuple, is_newer  # noqa: E402


def test_version_tuple():
    assert _version_tuple("0.6.0") == (0, 6, 0)
    assert _version_tuple("v1.2.3") == (1, 2, 3)
    assert _version_tuple("V0.5") == (0, 5)
    assert _version_tuple("") == (0,)
    assert _version_tuple("1.0.0-beta") == (1, 0, 0)


def test_is_newer():
    assert is_newer("0.7.0", "0.6.0")
    assert is_newer("v0.6.1", "0.6.0")
    assert is_newer("0.10.0", "0.9.0")      # 数值比较而非字典序
    assert is_newer("0.6.0.1", "0.6.0")
    assert not is_newer("0.6.0", "0.6.0")
    assert not is_newer("0.6", "0.6.0")     # 补零对齐后相等
    assert not is_newer("0.5.9", "0.6.0")
    assert not is_newer("", "0.6.0")


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_check_found_update(monkeypatch):
    monkeypatch.setattr(upd.httpx, "get", lambda *a, **k: _FakeResp(
        {"tag_name": "v9.9.9", "html_url": "https://github.com/x/y/releases/tag/v9.9.9",
         "body": "# 更新说明\n- 新功能", "published_at": "2026-08-26T00:00:00Z"}))
    r = upd.check()
    assert r["has_update"] is True
    assert r["latest"] == "9.9.9"
    assert r["current"] == upd.__version__
    assert r["html_url"].endswith("/v9.9.9")
    assert r["notes"].startswith("# 更新说明")


def test_check_up_to_date(monkeypatch):
    monkeypatch.setattr(upd.httpx, "get", lambda *a, **k: _FakeResp(
        {"tag_name": "v" + upd.__version__, "html_url": "u", "body": "", "published_at": None}))
    r = upd.check()
    assert r["has_update"] is False
    assert not r.get("error")


def test_check_network_error(monkeypatch):
    def boom(*a, **k):
        raise TimeoutError("no network")
    monkeypatch.setattr(upd.httpx, "get", boom)
    r = upd.check()
    assert r["has_update"] is False
    assert r["error"] == "network"
    assert r["html_url"] == upd.RELEASES_PAGE


def test_check_no_release_404(monkeypatch):
    monkeypatch.setattr(upd.httpx, "get", lambda *a, **k: _FakeResp({"message": "Not Found"}, status=404))
    r = upd.check()
    assert r["has_update"] is False
    assert r["error"] == "network"


def test_endpoint(monkeypatch):
    monkeypatch.setattr(upd, "check", lambda timeout=8.0: {
        "current": "0.6.0", "latest": "0.7.0", "has_update": True,
        "html_url": "https://github.com/x/y/releases/latest", "notes": "n", "published_at": None})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/update/check")
    assert r.status_code == 200
    j = r.json()
    assert j["has_update"] is True and j["latest"] == "0.7.0"


def test_endpoint_offline(monkeypatch):
    monkeypatch.setattr(upd, "check", lambda timeout=8.0: {
        "current": "0.6.0", "latest": None, "has_update": False,
        "html_url": upd.RELEASES_PAGE, "notes": None, "error": "network"})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/update/check")
    assert r.status_code == 200
    assert r.json()["error"] == "network"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
