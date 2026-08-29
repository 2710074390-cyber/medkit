"""用量记账（U5）：按次上下文记录 LLM usage，供 run.log / meta.json / 响应返回实际成本。

v0.5（2026-08 审计）：原全局单账本导致 run / trial / regen 互相串账（trial 的 token 会
被并行的管线快照；regen 会污染下一次 run 的起点）。现改为「按次上下文」：
- run_project 进入独立账本（ContextVar；ThreadPoolExecutor.submit 不传播 ContextVar，
  需在提交处用 contextvars.copy_context().run 包装——见 orchestrator/medqc 的提交点）；
- trial / regen 各自 with usage.context() 独立记账并随响应返回；
- 无显式上下文的调用（如外部脚本直达 LLMClient）落在线程局部默认账本，互不干扰。
线程安全：每账本自带锁（并发切片/QC 批次共用）。
"""

import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Iterator


class UsageContext:
    """一次运行/试出/重掷的独立账本。"""

    def __init__(self) -> None:
        self._state: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._state["prompt_tokens"] = 0
            self._state["completion_tokens"] = 0

    def add(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self._state["prompt_tokens"] += int(prompt_tokens or 0)
            self._state["completion_tokens"] += int(completion_tokens or 0)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._state)


_ACTIVE: ContextVar[UsageContext | None] = ContextVar("medkit_usage_ctx", default=None)
_local = threading.local()


def _default() -> UsageContext:
    """线程局部默认账本（无显式上下文时使用）。"""
    ctx = getattr(_local, "ctx", None)
    if ctx is None:
        ctx = UsageContext()
        _local.ctx = ctx
    return ctx


def current() -> UsageContext:
    """当前线程生效的账本：显式上下文优先，否则线程本地默认。"""
    return _ACTIVE.get() or _default()


def activate() -> Token:
    """进入独立账本（run/regen/trial 用）；返回 token 交给 deactivate 还原。"""
    ctx = UsageContext()
    return _ACTIVE.set(ctx)


def deactivate(token: Token) -> None:
    try:
        _ACTIVE.reset(token)
    except ValueError:
        pass  # 极端情况（token 来自已失效上下文）→ 忽略


@contextmanager
def context() -> Iterator[UsageContext]:
    """with 一段代码独立记账（试出/重掷/自定义调用）。"""
    token = activate()
    try:
        yield _ACTIVE.get() or _default()
    finally:
        deactivate(token)


def reset() -> None:
    """兼容旧调用：重置当前有效账本（run_project 迁移后不再依赖全局 reset）。"""
    current().reset()


def add(prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
    current().add(prompt_tokens, completion_tokens)


def snapshot() -> dict[str, int]:
    return current().snapshot()


def estimate_cost_cny(tokens_in: int, tokens_out: int,
                      price: dict[str, float] | None) -> float | None:
    """按服务商单价（元 / 1M token，以官网为准）折算人民币；无价格表 → None。"""
    if not price:
        return None
    return tokens_in / 1e6 * price.get("input", 0.0) + tokens_out / 1e6 * price.get("output", 0.0)
