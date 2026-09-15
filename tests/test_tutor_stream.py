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


# ---------------------------------------------------------------- R5-02/03 tutor 流生命周期
def test_tutor_gen_close_sets_cancel_and_releases(iso, monkeypatch, run_coro):
    """R5-02/03（tutor 侧确定性验证）：gen() 关闭（断连 GeneratorExit）→
    cancel_ev 置位 + 流锁释放 + 未落定空会话兜底删除。

    R6-01：协程经 `run_coro`（新线程）驱动，免疫 browser 层占住的主线程事件循环。
    """
    import time

    import medkit.routers.library as rl

    body = rl.TutorStartBody(subject="儿科学", kp_name="生长发育")
    captured = {}

    class SlowClient:
        def __init__(self):
            self.cancel = None

        def chat_stream(self, messages, temperature=0.6, max_tokens=None):
            yield {"delta": "先想想：", "usage": None, "canceled": False}
            time.sleep(3)
            yield {"delta": "最可能的机制是？", "usage": None, "canceled": False}

    def make_client(cancel=None):
        cl = SlowClient()
        cl.cancel = cancel
        captured["cancel"] = cancel
        return cl

    monkeypatch.setattr(rl, "_tutor_client", make_client)
    resp = rl.tutor_start_stream(body, _guard=None)
    it = resp.body_iterator

    async def drive():
        first = await it.__anext__()
        assert "event: meta" in first, first
        assert captured["cancel"] is not None and not captured["cancel"].is_set()
        assert len(tut.list_sessions()) == 1, "流开始后会话应已创建（落定前由 finally 兜底）"
        await it.aclose()   # 模拟断连

    run_coro(drive())
    assert captured["cancel"].is_set(), "断连后 cancel_ev 应被置位（R5-03）"
    from medkit.core import dedupe

    key = rl._tutor_key(body)
    assert dedupe.begin(key) is False, "断连后流锁应已释放（R5-02）"
    dedupe.end(key)
    assert tut.list_sessions() == [], "未落定空会话应被兜底删除（R4-04）"


def test_tutor_gen_dup_error_frame_and_session_cleanup(iso, monkeypatch, run_coro):
    """R5-02（tutor 侧）：gen 首帧前发现同 key 已在飞 → error 帧 + 清理本请求空会话。

    R6-01：协程经 `run_coro`（新线程）驱动。
    """
    import medkit.routers.library as rl

    body = rl.TutorStartBody(subject="儿科学", kp_name="生长发育")

    class QuickClient:
        def chat_stream(self, messages, temperature=0.6, max_tokens=None):
            yield {"delta": "问题", "usage": None, "canceled": False}

    monkeypatch.setattr(rl, "_tutor_client", lambda cancel=None: QuickClient())
    from medkit.core import dedupe

    key = rl._tutor_key(body)
    assert dedupe.begin(key) is False   # 先持有锁 → 模拟另一请求正在流
    resp = rl.tutor_start_stream(body, _guard=None)
    it = resp.body_iterator

    async def drive():
        first = await it.__anext__()
        assert "event: error" in first and "正在创建" in first, first
        assert tut.list_sessions() == [], "重复请求创建的空会话应被清理"
        await it.aclose()

    run_coro(drive())
    dedupe.end(key)
