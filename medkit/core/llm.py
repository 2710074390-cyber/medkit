"""OpenAI 兼容 LLM 客户端封装（覆盖 DeepSeek/智谱/千问/自定义/Ollama）。

统一行为：超时、重试（指数退避）、JSON 规范化（剥 ``` 围栏、截取首个完整 JSON）。
"""

import json
import logging
import re
import threading
import time
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import errors as _errs
from . import usage

_logger = logging.getLogger("medkit.llm")


class LLMError(Exception):
    """LLM 调用失败（含解析失败），携带可读信息。"""


def _extract_json(text: str) -> Any:
    """从模型输出中稳健提取 JSON。

    处理：``` 围栏剥除、文前/文后散文（「好的，以下是…」）、首个 { 或 [ 到末个 } 或 ]。
    """
    t = text or ""
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    t = t.strip()
    if not t:
        raise LLMError("模型输出为空")

    candidates: list[str] = []
    # 候选1：整体就是 JSON
    if t.startswith("{") or t.startswith("["):
        candidates.append(t)
    # 候选2：从首个括号截取到末个对应括号（容忍前后散文）
    for opener, closer in (("{", "}"), ("[", "]")):
        i, j = t.find(opener), t.rfind(closer)
        if 0 <= i < j:
            candidates.append(t[i:j + 1])

    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    # R6-19：不把模型原始输出片段放进异常串（会经错误体回显给用户）——原文只进日志
    _logger.warning("JSON 解析失败，模型原始输出（前 200 字符）：%r", t[:200])
    raise LLMError("JSON 解析失败：模型返回内容不是合法 JSON（原始输出已写入日志供排查）")


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc)
    return any(k in msg for k in ("timeout", "timed out", "429", "500", "502", "503", "529", "rate limit", "overloaded"))


def _truncated(resp: Any) -> bool:
    """响应是否被 max_tokens 截断（finish_reason == "length"）。

    存在的理由：**推理模型（reasoning model）的 max_tokens 是「思考 + 正文」的总额度**，
    思考阶段会把额度吃光，正文一个字都产不出来。此时 SDK 不报错、HTTP 200、
    `message.content` 是空串——静默失败。实测（2026-09-28，deepseek-v4-flash）：
    同一道题 20 道里 5 道在 max_tokens=2000 下 `finish_reason=length` + `content=''`，
    调到 6000 全部正常（reasoning_tokens 最高 3806）。

    故这里**显式区分「截断」与「模型真的返回空」**：前者可重试（放大额度），
    后者不可（放大也没用）。上游 `chat` 据此决定是否重试。
    """
    try:
        return resp.choices[0].finish_reason == "length"
    except (AttributeError, IndexError, TypeError):
        return False


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 300.0, max_retries: int = 2,
                 cancel: Optional[threading.Event] = None):
        if not base_url:
            raise LLMError("未配置服务商地址（base_url）")
        if not model:
            raise LLMError("未配置模型")
        self.model = model
        self.max_retries = max_retries
        self._cancel = cancel   # R3-09/B24：取消事件——流式读取中提前退出，停止不再烧完整回复
        self._client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key or "none",
                              timeout=timeout, max_retries=0)  # 重试由本类控制

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.7,
             json_mode: bool = False, max_tokens: Optional[int] = None) -> str:
        last_err: Optional[Exception] = None
        # 截断重试用的**可变额度**：必须在循环外持有，否则每轮从 `max_tokens` 参数重建，
        # 翻倍会失效（实测踩过：重试仍以原额度发同一请求，白烧一次钱且必然再失败）。
        budget = max_tokens
        for attempt in range(self.max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if budget:
                    kwargs["max_tokens"] = budget
                if self._cancel is not None:
                    # R3-09/B24：带取消事件时走流式——用户点「停止」后提前退出读取，
                    # 不等待整段回复烧完 token（取消时已产生费用仍由 usage 记录）
                    if self._cancel.is_set():
                        raise LLMError("已取消（用户停止）")
                    parts: list[str] = []
                    stream = self._client.chat.completions.create(**kwargs, stream=True)
                    for chunk in stream:
                        if self._cancel.is_set():
                            raise LLMError("已取消（用户停止）")
                        try:
                            if chunk.choices and chunk.choices[0].delta                                     and chunk.choices[0].delta.content:
                                parts.append(chunk.choices[0].delta.content)
                        except Exception as e:  # noqa: BLE001  部分服务商终止块结构差异
                            _errs.record("llm.chat", "静默容错（U-15 留痕）", e=e)
                        if getattr(chunk, "usage", None) is not None:
                            usage.add(getattr(chunk.usage, "prompt_tokens", 0),
                                      getattr(chunk.usage, "completion_tokens", 0))
                    return "".join(parts)
                resp = self._client.chat.completions.create(**kwargs)
                use = getattr(resp, "usage", None)
                if use is not None:  # U5：记录实际消耗
                    usage.add(getattr(use, "prompt_tokens", 0),
                              getattr(use, "completion_tokens", 0))
                if _truncated(resp):
                    # 推理模型把额度全用在思考上 → 正文为空。这是**可重试**的：
                    # 放大额度重来一次，而不是把空串当结果返回给调用方。
                    # （旧行为：静默返回 ""，上层只看到「模型输出为空」，无从判断原因）
                    rt = getattr(getattr(use, "completion_tokens_details", None),
                                 "reasoning_tokens", 0) or 0
                    msg = (f"输出被 max_tokens={budget} 截断"
                           f"（reasoning_tokens={rt}，正文为空）——推理模型需更大额度")
                    last_err = LLMError(msg)
                    _errs.record("llm.chat", msg)
                    if attempt < self.max_retries:
                        # 额度翻倍（有上限）：2000 → 4000 → 8000
                        if budget:
                            budget = min(int(budget) * 2, 32000)
                        time.sleep(1)
                        continue
                    raise last_err
                return resp.choices[0].message.content or ""
            except LLMError:
                # 本类自己抛的语义化错误（截断/取消）：不要被下面的宽 except 重新包一层
                # `调用失败(...)` 而丢掉诊断信息——那会让调用方只能看到「模型输出为空」，
                # 无法区分「额度不够」与「模型真的返回空」。
                raise
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.max_retries and _is_retryable(e):
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"调用失败({self.model}): {e}") from e
        raise LLMError(f"调用失败({self.model}): {last_err}")

    def chat_stream(self, messages: list[dict[str, str]], temperature: float = 0.7,
                      max_tokens: Optional[int] = None):
        """WP-8：流式生成器——yield {delta, usage, canceled}，支持取消事件。

        与 chat() 的取消语义一致：取消时不等待整段回复烧完 token。
        R5-C-02：usage 记账改为「chunk 累计 → 结束/取消/异常处一次落账」——
        取消/异常路径补记**已见**的部分 token（此前只在 chunk.usage 存在时逐块记账，
        取消时收不到末块 usage（DeepSeek 仅末块携带）→ 该请求 prompt 整段漏记；
        现在只要中途/异常块带过 usage，即按已见部分快照落账）。
        R5-03：无论结束/取消/异常/断连（生成器被 close），finally 关闭 provider 流——
        断连时立即中止 HTTP 连接，不再让服务端继续生成烧 token。
        """
        if self._cancel is not None and self._cancel.is_set():
            yield {"delta": "", "usage": None, "canceled": True}
            return
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        acc = {"prompt_tokens": 0, "completion_tokens": 0}
        seen = False  # 是否收到过任何 usage 块（取消/异常时据此决定补记）
        stream: Any = None
        try:
            stream = self._client.chat.completions.create(**kwargs, stream=True)
            for chunk in stream:
                if self._cancel is not None and self._cancel.is_set():
                    if seen:
                        usage.add(**acc)   # C-02：取消路径快照补记
                    yield {"delta": "", "usage": dict(acc) if seen else None, "canceled": True}
                    return
                delta = ""
                try:
                    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                except Exception as e:  # noqa: BLE001  部分服务商终止块结构差异
                    _errs.record("llm.chat_stream", "静默容错（U-15 留痕）", e=e)
                if getattr(chunk, "usage", None) is not None:
                    acc["prompt_tokens"] += int(getattr(chunk.usage, "prompt_tokens", 0) or 0)
                    acc["completion_tokens"] += int(getattr(chunk.usage, "completion_tokens", 0) or 0)
                    seen = True
                yield {"delta": delta, "usage": dict(acc) if seen else None, "canceled": False}
            if seen:
                usage.add(**acc)   # 正常结束：整段记账（此前逐块记账，总量不变）
        except Exception as e:  # noqa: BLE001
            if seen:
                usage.add(**acc)   # C-02：异常路径也补记已见部分
            if self._cancel is not None and self._cancel.is_set():
                yield {"delta": "", "usage": dict(acc) if seen else None, "canceled": True}
                return
            raise LLMError(f"流式调用失败({self.model}): {e}") from e
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                close()   # R5-03：结束/取消/异常/断连全路径关闭 provider 连接

    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.7,
                  max_tokens: Optional[int] = None,
                  schema: Optional[type[BaseModel]] = None) -> Any:
        """chat + 回退解析：先 json_mode，失败后普通文本再剥围栏。

        schema（ADR-003 契约层）：传入了就在解析 JSON 后 ``model_validate``，
        校验失败抛 ``LLMError``（带错误详情，供调用方走「修复重发 / 人工复核」）；
        默认 ``None`` 时行为与旧版完全一致（向后兼容）。
        """
        try:
            raw = self.chat(messages, temperature=temperature, json_mode=True,
                            max_tokens=max_tokens)
            parsed = _extract_json(raw)
        except LLMError:
            raw = self.chat(messages, temperature=temperature, json_mode=False,
                            max_tokens=max_tokens)
            parsed = _extract_json(raw)
        if schema is not None:
            try:
                return schema.model_validate(parsed)
            except ValidationError as e:
                # R6-19：契约校验失败的详情（含模型输出值）只进日志，不回显
                _logger.warning("LLM 输出未通过 %s 契约：%s", schema.__name__, e)
                raise LLMError(
                    f"LLM 输出未通过 {schema.__name__} 契约校验（详情已写入日志）") from e
        return parsed

    def list_models(self, raise_on_error: bool = False) -> list[str]:
        try:
            return [m.id for m in self._client.models.list().data]
        except Exception as e:  # noqa: BLE001
            if raise_on_error:
                raise LLMError(f"获取模型列表失败：{e}") from e
            return []

    @staticmethod
    def _test_error_hint(e: Exception) -> str:
        """A-新21：把 openai 英文原串映射为可操作的中文原因（Key/地址/网络/超时）。"""
        m = str(e)
        low = m.lower()
        if any(k in low for k in ("timeout", "timed out", "read timed out")):
            return "连接失败：请求超时（约 8 秒无响应）——请检查 Base URL 是否可达、网络是否正常"
        if any(k in low for k in ("401", "403", "authentication", "invalid api key",
                                  "api key", "authorization", "unauthorized")):
            return "连接失败：API Key 无效或未授权——请检查 Key 是否正确、是否已充值/开通"
        if any(k in low for k in ("404", "not found", "no such host", "getaddrinfo",
                                  "name resolution", "dns")):
            return "连接失败：地址不存在或无法解析——请检查 Base URL（需 http(s):// 开头）"
        if any(k in low for k in ("connect", "connection", "refused", "network",
                                  "remote", "ssl")):
            return "连接失败：无法连接到服务端——请检查 Base URL 与网络"
        # SEC-REDACT ①（R8+W）：原始异常串必须过 redact 才能回显——
        # 该分支以 200 正常 JSON 的 msg 字段直出，**不经过** main.py 的统一错误出口。
        from . import errors as _errs
        return f"连接失败：{_errs.redact(m)}"

    def test(self) -> tuple[bool, str]:
        """测试连接：能拿到模型应答即成功（不校验应答内容，避免误报）。"""
        t0 = time.time()
        try:
            out = self.chat([{"role": "user", "content": "请回复：OK"}],
                            temperature=0.0, max_tokens=32)
            if out.strip():
                return True, f"连接成功（{time.time() - t0:.1f}s，模型正常应答）"
            return False, "连接成功但模型返回为空"
        except LLMError as e:
            # A-新21：失败返回中文原因（如 连接失败：请检查 Base URL/Key/网络），不再抛英文原串
            return False, self._test_error_hint(e)
