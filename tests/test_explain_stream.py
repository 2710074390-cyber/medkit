"""WP-8：讲解 SSE 流式端点（meta → delta* → done/error；完成才落盘）。"""

import json

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import db as dbs
from medkit.core import explain as expl
from medkit.core import library as lib


class FakeStreamClient:
    def __init__(self, error=False):
        self.error = error

    def chat_stream(self, messages, temperature=0.5, max_tokens=None):
        if self.error:
            raise RuntimeError("boom")
        for t in ["第一段…", "第二段…"]:
            yield {"delta": t, "usage": None, "canceled": False}

    def chat(self, messages, temperature=0.7):
        return "非流式回退"


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(expl, "LIBRARY_DIR", libd)
    monkeypatch.setattr(expl, "EXPLAINS_FILE", libd / "explains.json")
    monkeypatch.setattr(expl, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", libd)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    dbs.reset_conn()
    return libd


def _client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_explain_stream_events_and_save(iso, monkeypatch):
    import medkit.routers.library as rl

    monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: FakeStreamClient())
    c = _client()
    body = {"subject": "儿科学", "kp_name": "生长发育", "use_web": False}
    r = c.post("/api/library/explain/stream", json=body)
    assert r.status_code == 200, r.text
    text = r.text
    assert "event: meta" in text and "event: done" in text
    assert text.count("event: delta") >= 2
    data = json.loads(text.split("event: done")[1].strip().split("data:", 1)[1])
    rec = data["explain"]
    assert rec["content"] == "第一段…第二段…"
    saved = expl.list_explains()
    assert len(saved) == 1 and saved[0]["id"] == rec["id"]


def test_chat_stream_yields_delta(monkeypatch):
    from types import SimpleNamespace

    from medkit.core.llm import LLMClient

    class Delta:
        content = "x"

    class Choice:
        delta = Delta()

    class Chunk:
        choices = [Choice()]
        usage = None

    class FakeCompletions:
        def create(self, **kwargs):
            return iter([Chunk(), Chunk()])

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client = LLMClient("https://x.example.com", "sk", "m")
    client._client = fake
    events = list(client.chat_stream([{"role": "user", "content": "hi"}], temperature=0.2))
    assert len(events) == 2
    assert all(e["delta"] == "x" and e["canceled"] is False for e in events)


def test_explain_stream_error_no_save(iso, monkeypatch):
    import medkit.routers.library as rl

    monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: FakeStreamClient(error=True))
    c = _client()
    r = c.post("/api/library/explain/stream",
               json={"subject": "儿科学", "kp_name": "生长发育", "use_web": False})
    assert "event: error" in r.text
    assert r.text.count("event: done") == 0
    assert expl.list_explains() == []


def test_explain_stream_canceled_no_save(iso, monkeypatch):
    """R4-02：服务端 cancel 事件 → 流式提前 cancel，不落盘产物。"""
    import medkit.routers.library as rl

    class CancelClient:
        def chat_stream(self, messages, temperature=0.5, max_tokens=None):
            yield {"delta": "", "usage": None, "canceled": True}

    monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: CancelClient())
    r = _client().post("/api/library/explain/stream",
                       json={"subject": "儿科学", "kp_name": "生长发育", "use_web": False})
    assert "event: canceled" in r.text
    assert "event: done" not in r.text
    assert expl.list_explains() == []
