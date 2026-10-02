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
    """一次运行/试出/重掷的独立账本。

    W10（2026-10-02 二轮审计）：可选 **token 硬上限**。此前只有「事前估算」
    （`cost.estimate_run`，给前端看的数字），**没有任何运行中的熔断**——
    估计 5 元、实际烧 50 元时无人拦。`limit_tokens>0` 时，累加一旦越线即抛
    `BudgetExceeded`，由调用链（管线/试出）捕获后中止本次运行。

    `limit_tokens=0`（默认）⇒ 不限制（保持既有行为，不影响现有测试与调用点）。
    """

    def __init__(self, limit_tokens: int = 0) -> None:
        self._state: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        self._lock = threading.Lock()
        self._limit = int(limit_tokens or 0)

    def set_limit(self, limit_tokens: int) -> None:
        """设置/清除硬上限（0 = 不限制）。"""
        with self._lock:
            self._limit = int(limit_tokens or 0)

    @property
    def limit(self) -> int:
        return self._limit

    def reset(self) -> None:
        with self._lock:
            self._state["prompt_tokens"] = 0
            self._state["completion_tokens"] = 0

    def add(self, prompt_tokens: int = 0, completion_tokens: int = 0) -> None:
        with self._lock:
            self._state["prompt_tokens"] += int(prompt_tokens or 0)
            self._state["completion_tokens"] += int(completion_tokens or 0)
            total = self._state["prompt_tokens"] + self._state["completion_tokens"]
            limit = self._limit
        # 越界判断放在锁外抛（异常构造不占锁）
        if limit and total > limit:
            raise BudgetExceeded(total, limit)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return dict(self._state)


class BudgetExceeded(RuntimeError):
    """本次运行的 token 用量越过硬上限（W10 熔断）。

    继承 RuntimeError：既有 `except Exception` 兜底路径天然能接住，
    但调用方可**优先**捕获它来做「中止本次运行 + 给用户可操作提示」。
    """

    def __init__(self, used: int, limit: int) -> None:
        self.used = int(used)
        self.limit = int(limit)
        super().__init__(
            f"本次运行已用 {used} tokens，超过上限 {limit}——已中止以避免继续计费。"
            f"可在配置里调高上限后重试。")


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


def activate(limit_tokens: int = 0) -> Token:
    """进入独立账本（run/regen/trial 用）；返回 token 交给 deactivate 还原。

    `limit_tokens>0` 时给该账本装 W10 硬上限（越线抛 `BudgetExceeded`）。
    """
    ctx = UsageContext(limit_tokens=limit_tokens)
    return _ACTIVE.set(ctx)


def deactivate(token: Token) -> None:
    try:
        _ACTIVE.reset(token)
    except ValueError:
        pass  # 极端情况（token 来自已失效上下文）→ 忽略


@contextmanager
def context(limit_tokens: int = 0) -> Iterator[UsageContext]:
    """with 一段代码独立记账（试出/重掷/自定义调用）；可带 W10 硬上限。"""
    token = activate(limit_tokens=limit_tokens)
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
