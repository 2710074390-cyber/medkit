# -*- coding: utf-8 -*-
"""守卫：`_explain_start_guard` 只能**窥视**，不得占锁。

## 为什么需要这条守卫

`_explain_start_guard`（`medkit/routers/library.py:544`）是 FastAPI 的
`Depends(yield)` 依赖，它**故意**只做 `dedupe.is_active()` 窥视，不调 `dedupe.begin()`。
注释给了理由，但**注释不是守卫**——有人"顺手优化"成 begin/end 时，
代码看起来更对称、更"严谨"，而故障是**每个流式请求都自己 409**，
症状离改动点很远，非常难查。

## 故障机理（已实测，2026-09-29，真 uvicorn + 真 HTTP）

fastapi 0.136.3 / starlette 1.6.0 下，`Depends(yield)` 的 teardown
**在响应流消费完成之后**才执行。实测事件顺序：

    guard:enter → guard:took_lock → resp:headers(200) → gen:start
    → ……流…… → gen:end → guard:exit → client:eof

即守卫的 `yield` 生命周期**完整包住**流的生命周期。若守卫 `begin(key)`，
则 `gen()` 内同 key 的 `begin(key)` 会撞上守卫已登记的 key
→ `explain_stream` 直接抛 409，**同一请求把自己锁死了**。

## 本文件的判据（两层，缺一不可）

1. **静态**：守卫函数体里不得出现 `dedupe.begin` / `dedupe.end`。
2. **动态（关键）**：用一个**会自锁的真实依赖**跑一遍，确认
   「守卫持锁 + gen 内 begin」确实 409——证明故障是真的，
   而不是我纸上推演出来的。

第 2 层是这条守卫的灵魂：**只写第 1 层等于复述注释**。
"""
from __future__ import annotations

import pathlib
import re
import threading
import time
from typing import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
GUARD_SRC = ROOT / "medkit" / "routers" / "library.py"


def _guard_body() -> str:
    """抽出 `_explain_start_guard` 的函数体（到下一个顶层 def 为止）。"""
    src = GUARD_SRC.read_text(encoding="utf-8")
    m = re.search(
        r"^def _explain_start_guard\(.*?\n(?=^def |^@|^class )",
        src, re.S | re.M,
    )
    assert m, "找不到 _explain_start_guard——改名了？本守卫需同步更新"
    body = m.group(0)
    # 剥掉注释与 docstring：注释里会出现 `dedupe.begin`（就是在解释"不用它"），
    # 不剥会把注释当代码 → 假红。（本项目已有"扫描类守卫必须先剥注释"的教训。）
    body = re.sub(r'""".*?"""', "", body, flags=re.S)
    body = re.sub(r"#.*", "", body)
    return body


# ---------------------------------------------------------------- 第 1 层：静态
def test_guard_does_not_call_begin():
    """守卫不得调 `dedupe.begin`——那会让每个流式请求自己 409。"""
    body = _guard_body()
    assert "dedupe.begin" not in body, (
        "_explain_start_guard 里出现了 dedupe.begin：守卫持锁会与 gen() 内同 key "
        "锁互斥，导致同一请求自锁 409（见文件头注释的实测时序）。"
    )


def test_guard_does_not_call_end():
    """守卫也不得调 `dedupe.end`——没有 begin 就 end 会误放别人的锁。"""
    body = _guard_body()
    assert "dedupe.end" not in body, (
        "_explain_start_guard 里出现了 dedupe.end：守卫没 begin 却 end，"
        "会误释放 gen() 持有的锁，破坏「锁持有期 ≡ 流生命周期」。"
    )


def test_guard_still_peeks():
    """守卫必须**保留**窥视——防的是"干脆检查都不做了"这种反向失守。"""
    body = _guard_body()
    assert "is_active" in body, (
        "_explain_start_guard 少了 dedupe.is_active 窥视："
        "请求级早拦截没了，重复提交要等 gen() 才被拦（期间可能已产生副作用）。"
    )
    assert "409" in body, "守卫不再回 409——去重语义丢了"


# ---------------------------------------------------------------- 第 2 层：动态
def _build_app(guard_takes_lock: bool) -> FastAPI:
    """构造一个最小 app，复刻「守卫 + gen 用同一把 dedupe 锁」的结构。

    `guard_takes_lock=True` 即模拟"被改成 begin/end"后的行为，用来证明故障真实。
    """
    from medkit.core import dedupe

    app = FastAPI()
    key = "probe::selflock"

    def guard() -> Iterator[None]:
        if guard_takes_lock:
            dedupe.begin(key)          # ← 被守的"错误改法"
        else:
            if dedupe.is_active(key):  # ← 现在的正确做法（窥视）
                from fastapi import HTTPException
                raise HTTPException(409, "dup")
        try:
            yield
        finally:
            if guard_takes_lock:
                dedupe.end(key)

    @app.get("/s")
    def s(_g: None = Depends(guard)) -> StreamingResponse:
        # 与真身同构：`begin` 在**端点函数体内**（StreamingResponse 创建之前）。
        # 若把它放进 gen()，异常会在"响应已开始"之后抛出 → Starlette 只会
        # 报 RuntimeError，而不是 409，测不出自锁语义（我第一版就这么写错了）。
        if dedupe.begin(key):
            from fastapi import HTTPException
            raise HTTPException(409, "self-lock")

        def gen():
            try:
                yield "x"
                time.sleep(0.05)
                yield "y"
            finally:
                dedupe.end(key)

        return StreamingResponse(gen(), media_type="text/event-stream")

    return app


@pytest.fixture(autouse=True)
def _clean_dedupe():
    """用例前后清干净全局 dedupe 状态（它是进程级 set，会跨用例残留）。"""
    from medkit.core import dedupe
    yield
    for k in ("probe::selflock",):
        dedupe.end(k)


def test_correct_guard_allows_single_request():
    """窥视版守卫：单个流式请求必须 200（不自己锁自己）。"""
    c = TestClient(_build_app(guard_takes_lock=False))
    r = c.get("/s")
    assert r.status_code == 200, "窥视版守卫竟然拦了自己：%s" % r.text
    # StreamingResponse 直接把 yield 的内容拼接，不插换行（两帧 → "xy"）。
    # 判据是「两帧都到了」，不是「有换行」——别把传输细节当契约。
    assert r.text.replace("\r\n", "\n") == "xy", "两帧没都送达：%r" % r.text


def test_begin_end_guard_self_locks():
    """**关键用例**：把守卫改成 begin/end → 单请求也会 409（自锁）。

    这条证明"故障是真的"，而不是注释里的推演。若哪天 FastAPI 改了
    teardown 时机使本用例变绿，说明注释已过期、设计前提需重新评估——
    **那时应该红的是这条用例**，它会提醒我们重新审。
    """
    c = TestClient(_build_app(guard_takes_lock=True))
    r = c.get("/s")
    assert r.status_code == 409, (
        "把守卫改成 begin/end 后竟然没自锁（实为 %d）——"
        "说明 FastAPI 的 Depends(yield) teardown 时机变了，"
        "`_explain_start_guard` 的设计前提需要重新评估。" % r.status_code
    )


def test_real_teardown_outlives_stream():
    """直接钉住设计前提本身：守卫的 finally 晚于 gen() 结束。

    这是那段注释的**唯一事实依据**。它红了就等于"前提已变"，
    必须重新读 `_explain_start_guard` 的注释。
    """
    events: list = []
    lock = threading.Lock()

    app = FastAPI()

    def guard() -> Iterator[None]:
        try:
            yield
        finally:
            with lock:
                events.append("guard:exit")

    @app.get("/t")
    def t(_g: None = Depends(guard)) -> StreamingResponse:
        def gen():
            yield "1"
            time.sleep(0.1)
            with lock:
                events.append("gen:end")
            yield "2"

        return StreamingResponse(gen(), media_type="text/event-stream")

    c = TestClient(app)
    with c.stream("GET", "/t") as r:
        for _ in r.iter_text():
            pass

    assert "gen:end" in events, "gen 没跑完？"
    assert events.index("guard:exit") > events.index("gen:end"), (
        "守卫的 finally 早于 gen() 结束——FastAPI 的 Depends(yield) teardown "
        "时机已变！`_explain_start_guard` 注释里的设计前提失效，必须重新评估。"
        "实测事件：%s" % events
    )
