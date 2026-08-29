"""OpenAI 兼容 LLM 客户端封装（覆盖 DeepSeek/智谱/千问/自定义/Ollama）。

统一行为：超时、重试（指数退避）、JSON 规范化（剥 ``` 围栏、截取首个完整 JSON）。
"""

import json
import re
import threading
import time
from typing import Any, Optional

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import usage


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
    raise LLMError(f"JSON 解析失败: {t[:80]!r} …")


def _is_retryable(exc: Exception) -> bool:
    msg = str(exc)
    return any(k in msg for k in ("timeout", "timed out", "429", "500", "502", "503", "529", "rate limit", "overloaded"))


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
        for attempt in range(self.max_retries + 1):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
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
                        except Exception:  # noqa: BLE001  部分服务商终止块结构差异
                            pass
                        if getattr(chunk, "usage", None) is not None:
                            usage.add(getattr(chunk.usage, "prompt_tokens", 0),
                                      getattr(chunk.usage, "completion_tokens", 0))
                    return "".join(parts)
                resp = self._client.chat.completions.create(**kwargs)
                use = getattr(resp, "usage", None)
                if use is not None:  # U5：记录实际消耗
                    usage.add(getattr(use, "prompt_tokens", 0),
                              getattr(use, "completion_tokens", 0))
                return resp.choices[0].message.content or ""
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < self.max_retries and _is_retryable(e):
                    time.sleep(2 ** attempt)
                    continue
                raise LLMError(f"调用失败({self.model}): {e}") from e
        raise LLMError(f"调用失败({self.model}): {last_err}")

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
                raise LLMError(f"LLM 输出未通过 {schema.__name__} 契约: {e}") from e
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
        return f"连接失败：{m}"

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
