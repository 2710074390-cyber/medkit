"""OpenAI 兼容 LLM 客户端封装（覆盖 DeepSeek/智谱/千问/自定义/Ollama）。

统一行为：超时、重试（指数退避）、JSON 规范化（剥 ``` 围栏、截取首个完整 JSON）。
"""

import json
import re
import time
from typing import Any, Optional

from openai import OpenAI

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
                 timeout: float = 300.0, max_retries: int = 2):
        if not base_url:
            raise LLMError("未配置服务商地址（base_url）")
        if not model:
            raise LLMError("未配置模型")
        self.model = model
        self.max_retries = max_retries
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
                  max_tokens: Optional[int] = None) -> Any:
        """chat + 回退解析：先 json_mode，失败后普通文本再剥围栏。"""
        try:
            raw = self.chat(messages, temperature=temperature, json_mode=True,
                            max_tokens=max_tokens)
            return _extract_json(raw)
        except LLMError:
            raw = self.chat(messages, temperature=temperature, json_mode=False,
                            max_tokens=max_tokens)
            return _extract_json(raw)

    def list_models(self) -> list[str]:
        try:
            return [m.id for m in self._client.models.list().data]
        except Exception:  # noqa: BLE001
            return []

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
            return False, f"连接失败：{e}"
