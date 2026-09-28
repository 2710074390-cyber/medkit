"""WP-8：讲解 SSE 流式端点（meta → delta* → done/error；完成才落盘）。

R5-02/03：流生命周期锁（gen 内 dedupe，防并发双扣费）+ 断连 cancel_ev 接线回归。
"""

import json
import threading
import time

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


# ---------------------------------------------------------------- R5-02 流生命周期去重
def test_concurrent_duplicate_stream_409(iso, monkeypatch):
    """R5-02：流进行期间的第二个同 key 请求必须 409（锁随流生命周期持有，不是响应返回即释放）。

    **2026-09-29 重写本用例**——原写法靠 TestClient + 双线程 Event 竞态，
    实测 **32 次里 9 次拿到 200**（假绿），且**根因在 TestClient 本身**：

        c.stream(...) 是**阻塞**的——它在返回响应对象之前就消费完了整个流。
        带时间戳的实测（`.workbuddy-ai/tmp/e2e/probe_testclient_stream.py`）：
            0.000 dedupe.begin
            0.312 chat_stream: yield#1
            3.312 chat_stream: sleep 结束     ← sleep(3) 在「进入 with 块」之前就跑完了
            3.312 dedupe.end                  ← 锁已释放
            3.328 with 块内：status=200       ← 此时才进入 with 块
        即**在 `with` 块内永远观察不到锁被持有**。原用例之所以偶尔过，
        只靠 `SlowClient` 内 `locked.set()` 与主线程调度的偶然交错。

    **新写法**（对齐 `tests/test_r8w_p1_remaining.py::test_tutor_guard_holds_lock_and_releases`
    的既有范式）：直接驱动守卫生成器，绕开 HTTP 层的阻塞语义 ⇒ **确定性**。
    被验证的语义不变：**锁在飞期间，第二个同 key 请求必须 409**。

    分工（三层都不重叠）：
      · 本用例：守卫在「锁被持有」时确实回 409（确定）
      · `test_gen_close_releases_dedupe_and_sets_cancel`：断连 → 释放锁（确定，经 aclose()）
      · `test_dedupe_released_after_stream_end`：流结束 → 释放锁、不永久误伤（确定，经 HTTP）
      · `tests/test_explain_guard_no_selflock.py`：守卫不得持锁（否则自锁）
    """
    from fastapi import HTTPException

    import medkit.routers.library as rl
    from medkit.core import dedupe

    body = rl.ExplainBody(subject="儿科学", kp_name="生长发育", use_web=False)
    key = rl._explain_key(body)
    dedupe.end(key)                       # 清场（进程级 set，可能被别的用例残留）

    # 模拟「A 的 gen() 已 begin，流仍在飞」⇒ 锁被持有
    assert dedupe.begin(key) is False, "清场后应能登记成功"
    try:
        # 并发的第二个同 key 请求：守卫必须直接 409（窥视到锁在飞）
        gen = rl._explain_start_guard(body)
        with pytest.raises(HTTPException) as ei:
            next(gen)
        assert ei.value.status_code == 409, (
            "锁在飞期间的第二个同 key 请求必须 409（实为 %d）" % ei.value.status_code
        )
        assert "正在生成" in ei.value.detail

        # 反向：锁释放后，同样的守卫必须放行（否则永久误伤=另一种故障）
        dedupe.end(key)
        gen2 = rl._explain_start_guard(body)
        next(gen2)                        # 不抛 = 放行
        gen2.close()
    finally:
        dedupe.end(key)


def test_gen_close_releases_dedupe_and_sets_cancel(iso, monkeypatch, run_coro):
    """R5-02/03 确定性验证（不经 HTTP）：gen() 关闭（断连 GeneratorExit）→
    cancel_ev 置位 + 流锁释放——「锁持有期 ≡ 流生命周期」。

    R6-01：协程一律经 `run_coro`（新线程）驱动——browser 层同进程收集时 Playwright 同步
    上下文会占住主线程事件循环，直接 `asyncio.run()` 必抛 RuntimeError。
    """
    import medkit.routers.library as rl

    body = rl.ExplainBody(subject="儿科学", kp_name="生长发育", use_web=False)
    captured = {}

    class SlowClient:
        def __init__(self):
            self.cancel = None

        def chat_stream(self, messages, temperature=0.5, max_tokens=None):
            yield {"delta": "第一段…", "usage": None, "canceled": False}
            time.sleep(3)   # 模拟 provider 慢流；gen 关闭时 GeneratorExit 直达此生成器
            yield {"delta": "第二段…", "usage": None, "canceled": False}

    def make_client(cancel=None):
        cl = SlowClient()
        cl.cancel = cancel
        captured["cancel"] = cancel
        return cl

    monkeypatch.setattr(rl, "_explain_client", make_client)
    resp = rl.explain_stream(body, _guard=None)
    it = resp.body_iterator

    async def drive():
        first = await it.__anext__()
        assert "event: meta" in first, first
        assert captured["cancel"] is not None and not captured["cancel"].is_set()
        await it.aclose()   # 模拟客户端断连 → GeneratorExit → gen finally

    run_coro(drive())
    assert captured["cancel"].is_set(), "断连后 cancel_ev 应在 gen() finally 中被置位（R5-03）"
    from medkit.core import dedupe

    key = rl._explain_key(body)
    assert dedupe.begin(key) is False, "断连后流锁应已释放（R5-02）"
    dedupe.end(key)
    assert expl.list_explains() == [], "断连流不应保存产物"


def test_dedupe_released_after_stream_end(iso, monkeypatch):
    """R5-02：流正常结束后锁释放——下一个请求不再 409（否则永久误伤）。"""
    import medkit.routers.library as rl

    class QuickClient:
        def chat_stream(self, messages, temperature=0.5, max_tokens=None):
            yield {"delta": "完", "usage": None, "canceled": False}

    monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: QuickClient())
    c = _client()
    body = {"subject": "儿科学", "kp_name": "生长发育", "use_web": False}
    r1 = c.post("/api/library/explain/stream", json=body)
    assert "event: done" in r1.text
    r2 = c.post("/api/library/explain/stream", json=body)
    assert r2.status_code == 200 and "event: done" in r2.text, r2.text


# ---------------------------------------------------------------- R5-03 断连取消接线
def test_stream_disconnect_sets_cancel(iso, monkeypatch):
    """R5-03：流结束/断开路径 cancel_ev 置位（TestClient 无法模拟真断连——
    TestClient 关响应会等待应用任务自然结束；「断连 → GeneratorExit → finally」的
    语义由 test_gen_close_releases_dedupe_and_sets_cancel 用 it.aclose() 确定性覆盖，
    aclose 正是 Starlette / uvicorn 断连时对生成器的处理方式）。"""
    import medkit.routers.library as rl

    captured = {}

    class SlowClient:
        def __init__(self):
            self.cancel = None

        def chat_stream(self, messages, temperature=0.5, max_tokens=None):
            yield {"delta": "第一段…", "usage": None, "canceled": False}
            time.sleep(3)
            yield {"delta": "第二段…", "usage": None, "canceled": False}

    def make_client(cancel=None):
        cl = SlowClient()
        cl.cancel = cancel
        captured["client"] = cl
        captured["cancel"] = cancel
        return cl

    monkeypatch.setattr(rl, "_explain_client", make_client)
    c = _client()
    body = {"subject": "儿科学", "kp_name": "生长发育", "use_web": False}
    with c.stream("POST", "/api/library/explain/stream", json=body) as r:
        assert r.status_code == 200
        first = next(iter(r.iter_lines()), "")
        assert first, "应收到首帧"
        # 退出 with → 关闭响应（模拟用户「停止生成/切页签」断开 fetch）
    assert captured.get("cancel") is not None, "路由应创建 cancel_ev 并传入 client"
    assert captured["cancel"].wait(5), "流结束后 cancel_ev 应被置位（R5-03 要求）"
    assert captured["client"].cancel is captured["cancel"]
    # 注：TestClient 不传播断连，应用按自然完成跑完（3s 慢流）→ 产物会保存；
    # 「断连不落盘」由 test_gen_close_releases_dedupe_and_sets_cancel（aclose 语义）覆盖


# ---------------------------------------------------------------- R5-C-02 usage 补记
def test_chat_stream_usage_snapshot_on_cancel(monkeypatch):
    """C-02：取消路径把「已见」usage 快照落账（此前取消即整段漏记）。"""
    from types import SimpleNamespace

    import medkit.core.usage as usage_mod
    from medkit.core.llm import LLMClient

    class Delta:
        content = "x"

    class Choice:
        delta = Delta()

    class Usage:
        def __init__(self, p, c):
            self.prompt_tokens = p
            self.completion_tokens = c

    class Chunk:
        def __init__(self, usage=None):
            self.choices = [Choice()]
            self.usage = usage

    ev = threading.Event()

    class CancelIter:
        """c1 带 usage；c2 的 __next__ 置位取消（模拟流中途用户停止）→ c2 不再入账。"""

        def __init__(self):
            self.n = 0

        def __iter__(self):
            return self

        def __next__(self):
            self.n += 1
            if self.n == 1:
                return Chunk(Usage(100, 40))
            if self.n == 2:
                ev.set()                 # 取消先于本块处理 → 本块 usage 不应入账
                return Chunk(Usage(999, 999))
            raise StopIteration

    class FakeCompletions:
        def create(self, **kwargs):
            return CancelIter()

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client = LLMClient("https://x.example.com", "sk", "m", cancel=ev)
    client._client = fake
    with usage_mod.context() as ctx:
        events = list(client.chat_stream([{"role": "user", "content": "hi"}], temperature=0.2))
    assert events and events[-1]["canceled"] is True, "取消应产出 canceled 事件"
    snap = ctx.snapshot()
    assert snap["prompt_tokens"] == 100 and snap["completion_tokens"] == 40, \
        f"取消路径应补记已见 usage（999 块不应入账）：{snap}"


def test_chat_stream_usage_snapshot_on_exception(monkeypatch):
    """C-02：流中途抛异常 → 已见 usage 落账，异常仍上抛。"""
    from types import SimpleNamespace

    import medkit.core.usage as usage_mod
    from medkit.core.llm import LLMClient, LLMError

    class Delta:
        content = "x"

    class Choice:
        delta = Delta()

    class Usage:
        prompt_tokens = 60
        completion_tokens = 20

    class Chunk:
        def __init__(self, usage=None):
            self.choices = [Choice()]
            self.usage = usage

    class BoomIter:
        """先出 1 个带 usage 的块，再在迭代中抛异常（模拟 provider 流中途断网）。"""

        def __init__(self):
            self.n = 0

        def __iter__(self):
            return self

        def __next__(self):
            if self.n == 0:
                self.n += 1
                return Chunk(Usage())
            raise RuntimeError("connection reset")

    class FakeCompletions:
        def create(self, **kwargs):
            return BoomIter()

    fake = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    client = LLMClient("https://x.example.com", "sk", "m")
    client._client = fake
    with usage_mod.context() as ctx:
        with pytest.raises(LLMError, match="流式调用失败"):
            for _ in client.chat_stream([{"role": "user", "content": "hi"}], temperature=0.2):
                pass
    snap = ctx.snapshot()
    assert snap["prompt_tokens"] == 60 and snap["completion_tokens"] == 20, \
        f"异常路径应补记已见 usage：{snap}"
