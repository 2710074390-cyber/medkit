"""U-12（R6-07）「防丢断言」：断言**实现结构**而非行为。

**为什么需要这一层**：R5-02 的教训——`CHANGELOG` / `AGENT_HANDOFF` / R4 报告三处都记载
「R4-01 已落地」，而代码从未进入提交，**测试全程绿灯**。原因是既有用例用
`monkeypatch.setattr(rl, "_explain_client", lambda cancel=None: …)` 把缺陷接缝**整个 mock 掉**，
且 mock 签名还是按「修复版」写的——于是「真的修好了」与「测试写成了通过的样子」无法区分。

**本文件的纪律**：对关键接缝，断言「源码里存在该结构」而不是「调用后行为正确」。
一旦有人把 `dedupe.end` 从 `finally` 移出、或删掉 `cancel_ev.set()`，本文件必须变红。
"""

import inspect

import pytest

from medkit.routers import library as lib_router


def _finally_body(src: str) -> str:
    """取最后一段 `finally:` 块之后的文本（本文件的断言都基于它）。"""
    idx = src.rfind("finally:")
    assert idx != -1, "未找到 finally 块——资源释放必须放在 finally 里"
    return src[idx:]


@pytest.mark.parametrize("fn_name,key_fn", [
    ("explain_stream", "_explain_key"),
    ("tutor_start_stream", "_tutor_key"),
])
def test_stream_seam_wiring_is_present(fn_name, key_fn):
    """讲解/提问两条流式接缝：请求级去重 + 取消事件必须结构性在位。"""
    fn = getattr(lib_router, fn_name)
    src = inspect.getsource(fn)
    # ① 取消事件（R5-03）：断连/结束时置位 → LLMClient 截断 provider 流
    assert "cancel_ev = threading.Event()" in src, f"{fn_name} 缺少 cancel_ev 创建"
    # ② 请求级去重（R5-02）：锁在首帧之前获取，真正的持有者是生成器而非请求守卫
    assert "dedupe.begin(" in src, f"{fn_name} 缺少 dedupe.begin（流内去重锁）"
    assert "def gen(" in src, f"{fn_name} 应把锁获取放在生成器内（首帧之前）"
    # ③ 释放路径：dedupe.end 与 cancel_ev.set 都必须在 finally 中
    tail = _finally_body(src)
    assert "dedupe.end(" in tail, f"{fn_name} 的 dedupe.end 不在 finally 中（异常/断连会漏放锁）"
    assert "cancel_ev.set()" in tail, f"{fn_name} 的 cancel_ev.set() 不在 finally 中（断连不会真停）"
    # ④ 去重键按知识点（不同知识点并行不互相阻塞）
    assert key_fn in src, f"{fn_name} 的去重键应来自 {key_fn}（按知识点隔离）"


def test_stream_guard_rejects_duplicate_before_first_frame():
    """去重命中时应在**首帧之前**返回 error，而不是先吐 meta 再报错。"""
    src = inspect.getsource(lib_router.explain_stream)
    i_begin = src.find("dedupe.begin(")
    i_meta = src.find('yield _sse("meta"')
    assert i_begin != -1 and i_meta != -1
    assert i_begin < i_meta, "dedupe.begin 必须在首个 meta 帧之前（否则重复提交会看到半截流）"
