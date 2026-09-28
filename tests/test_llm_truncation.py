"""LLM 截断（max_tokens 额度耗尽）的回归守卫。

## 这个守卫防的是什么

**推理模型（reasoning model）的 `max_tokens` 是「思考 + 正文」的总预算。**
思考阶段会把预算吃光，`message.content` 返回空串，但 HTTP 是 200、SDK 不抛异常、
`finish_reason` 是 `"length"`——**静默失败**。

实测（2026-09-28，`deepseek-v4-flash`，EP-01 阶段 0 的 20 道真题）：
`max_tokens=2000` 时有 5/20 题 `finish_reason=length && content=''`；
把同一批题调到 6000 全部正常（`reasoning_tokens` 最高 3806）。

修复前的表现：`chat()` 把空串当结果返回 → `_extract_json` 抛
`LLMError("模型输出为空")` → 上层只能看到「模型输出为空」，
**无法区分「额度不够」与「模型真的返回空」**，也就无从重试。

修复后：`chat()` 识别 `finish_reason == "length"` → 记一条带
`reasoning_tokens` 的错误留痕 → **额度翻倍重试**。

## 守卫能证伪

- `test_truncated_retries_with_doubled_budget`：注入 `length` 的假响应 ⇒
  必须**重试且额度翻倍**。若把 `_truncated` 改成恒 `False`（回到旧行为），
  该用例立刻变红（断言 `calls == 2` 会得到 `calls == 1`）。
- `test_truncated_exhausted_raises_with_reasoning_hint`：重试耗尽后必须抛**带诊断信息**的
  错误，而不是静默返回空串。
- `test_llmerror_not_rewrapped_as_generic_failure`：本类自己抛的语义化错误不得被
  下面的宽 `except Exception` 重新包成 `调用失败(...)`——否则诊断信息丢失。
"""

import pytest

from medkit.core.llm import LLMClient, LLMError, _truncated


# ------------------------------------------------------------------ 假 SDK 层
class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content, finish_reason):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Usage:
    def __init__(self, reasoning_tokens=0, completion_tokens=0):
        self.prompt_tokens = 10
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = type(
            "D", (), {"reasoning_tokens": reasoning_tokens})()


class _Resp:
    def __init__(self, content, finish_reason, reasoning_tokens=0):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage(reasoning_tokens, reasoning_tokens + len(content))


class _FakeCompletions:
    """按脚本逐次返回响应，并记录每次调用的 max_tokens。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs.get("max_tokens"))
        if not self.script:
            raise AssertionError("调用次数超出脚本预设——说明重试次数不符预期")
        return self.script.pop(0)


class _FakeSDK:
    def __init__(self, script):
        self.chat = type("C", (), {"completions": _FakeCompletions(script)})()


def _client(script, max_retries=2):
    c = LLMClient.__new__(LLMClient)
    c.model = "fake-reasoning-model"
    c.max_retries = max_retries
    c._cancel = None
    c._client = _FakeSDK(script)
    return c


# ------------------------------------------------------------------ 账本隔离
#
# ⚠️ 必须 autouse：本文件的用例会反复走到 `chat()` 的记账路径
# （`usage.add(...)`），而 `usage` 在**没有显式上下文时落在线程局部默认账本**，
# 该账本**跨用例、跨测试文件持续存在**。若不隔离：
#   - 本文件会往主线程默认账本累积 token（实测 {110, 12326}）；
#   - 主线程默认账本是按文件名排序的公共资源，
#     `test_s1_backend.py::test_usage_contexts_isolated` 断言它「干净」→ 被本文件污染而失败。
# 这个 bug 是我自己引入的（本文件是我新加的），且只在**全量跑**时暴露、
# 单跑本文件或单跑 test_s1_backend 都看不见——典型的「顺序依赖型假绿」。
# 放在 `usage.context()` 里跑即可让记账落在一次性账本上，用完即弃。
@pytest.fixture(autouse=True)
def _isolate_usage_ledger():
    from medkit.core import usage as usage_mod

    with usage_mod.context():
        yield


# ------------------------------------------------------------------ _truncated 单元
@pytest.mark.parametrize("finish,expected", [
    ("length", True), ("stop", False), ("tool_calls", False), ("content_filter", False),
])
def test_truncated_detects_finish_reason(finish, expected):
    assert _truncated(_Resp("x", finish)) is expected


def test_truncated_tolerates_malformed_response():
    """响应结构异常（无 choices）时不得抛——`_truncated` 是判断，不是校验。"""
    assert _truncated(object()) is False


# ------------------------------------------------------------------ 重试行为
def test_truncated_retries_with_doubled_budget():
    """核心守卫：截断 → 额度翻倍重试 → 第二次成功。

    证伪方式：把 `_truncated` 改成 `return False`，本用例变红
    （`AssertionError: 调用次数超出脚本预设` 或 calls 长度 != 2）。
    """
    c = _client([
        _Resp("", "length", reasoning_tokens=2000),       # 第一次：额度被思考吃光
        _Resp('{"ok": true}', "stop", reasoning_tokens=1200),  # 第二次：成功
    ])
    out = c.chat([{"role": "user", "content": "hi"}], max_tokens=2000)

    assert out == '{"ok": true}'
    assert c._client.chat.completions.calls == [2000, 4000], (
        "截断后必须以翻倍额度重试（2000 → 4000）")


def test_truncated_exhausted_raises_with_reasoning_hint():
    """重试仍截断 → 抛错，且错误里必须含「截断」与 reasoning_tokens 诊断。

    旧行为是静默返回空串，让上层误以为「模型返回了空」。
    """
    c = _client([_Resp("", "length", reasoning_tokens=2000)] * 3, max_retries=2)
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "hi"}], max_tokens=2000)

    msg = str(ei.value)
    assert "截断" in msg, f"错误应点明截断，实际：{msg}"
    assert "reasoning_tokens" in msg, f"错误应带 reasoning_tokens 诊断，实际：{msg}"
    assert c._client.chat.completions.calls == [2000, 4000, 8000]


def test_llmerror_not_rewrapped_as_generic_failure():
    """语义化 LLMError 不得被宽 except 重包成 '调用失败(...)' 而丢掉诊断。"""
    c = _client([_Resp("", "length", reasoning_tokens=999)] * 3, max_retries=2)
    with pytest.raises(LLMError) as ei:
        c.chat([{"role": "user", "content": "hi"}], max_tokens=2000)
    assert not str(ei.value).startswith("调用失败"), (
        "截断错误被重包成通用失败——诊断信息丢失")


def test_normal_response_unaffected():
    """不截断时行为不变（不得引入多余调用）。"""
    c = _client([_Resp('{"n": 1}', "stop")])
    assert c.chat([{"role": "user", "content": "hi"}], max_tokens=2000) == '{"n": 1}'
    assert c._client.chat.completions.calls == [2000]


def test_no_max_tokens_still_guarded():
    """未显式传 max_tokens 时若仍被截断，重试不得把额度设成 0/None 之外的东西。"""
    c = _client([
        _Resp("", "length", reasoning_tokens=100),
        _Resp('{"ok": 1}', "stop"),
    ])
    out = c.chat([{"role": "user", "content": "hi"}])
    assert out == '{"ok": 1}'
    # 未传 max_tokens ⇒ 不该凭空造一个额度出来
    assert c._client.chat.completions.calls == [None, None]
