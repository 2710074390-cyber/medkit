"""WP-8：提问第一问 SSE 流式端点（meta → delta* → done/error；成功才建会话的问题）。"""

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import db as dbs
from medkit.core import library as lib
from medkit.core import tutor as tut


class FakeStreamClient:
    def __init__(self, error=False):
        self.error = error

    def chat_stream(self, messages, temperature=0.6, max_tokens=None):
        if self.error:
            raise RuntimeError("boom")
        for t in ["先想想：", "最可能的机制是？"]:
            yield {"delta": t, "usage": None, "canceled": False}


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(tut, "LIBRARY_DIR", libd)
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", libd / "tutor_sessions.json")
    monkeypatch.setattr(tut, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", libd)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    dbs.reset_conn()
    return libd


def _client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_tutor_start_stream_events_and_session(iso, monkeypatch):
    import medkit.routers.library as rl

    monkeypatch.setattr(rl, "_tutor_client", lambda cancel=None: FakeStreamClient())
    c = _client()
    r = c.post("/api/library/tutor/start/stream",
               json={"subject": "儿科学", "kp_name": "生长发育"})
    assert r.status_code == 200, r.text
    assert "event: meta" in r.text and "event: done" in r.text
    assert r.text.count("event: delta") >= 2
    sessions = tut.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["current"]["text"] == "先想想：最可能的机制是？"


def test_tutor_start_stream_error_no_session(iso, monkeypatch):
    import medkit.routers.library as rl

    monkeypatch.setattr(rl, "_tutor_client", lambda cancel=None: FakeStreamClient(error=True))
    c = _client()
    r = c.post("/api/library/tutor/start/stream",
               json={"subject": "儿科学", "kp_name": "生长发育"})
    assert "event: error" in r.text and "event: done" not in r.text
    assert tut.list_sessions() == []


def test_tutor_start_stream_canceled_session_cleaned(iso, monkeypatch):
    """R4-04：服务端 cancel（未 seed_first）→ 兜底删除空会话，避免残留。"""
    import medkit.routers.library as rl

    class CancelClient:
        def chat_stream(self, messages, temperature=0.6, max_tokens=None):
            yield {"delta": "", "usage": None, "canceled": True}

    monkeypatch.setattr(rl, "_tutor_client", lambda cancel=None: CancelClient())
    r = _client().post("/api/library/tutor/start/stream",
                       json={"subject": "儿科学", "kp_name": "生长发育"})
    assert "event: done" not in r.text
    assert tut.list_sessions() == []   # 未落定会话被清理
