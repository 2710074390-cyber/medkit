"""W10 守卫：LLM 用量 **token 硬上限**（熔断）。

背景（2026-10-02 二轮审计 W10）：项目只有**事前估算**（`cost.estimate_run`，给前端看数字），
运行中**无任何熔断**——估 5 元、实际烧 50 元时无人拦，一路烧到管线结束。

本文件四道防线：
1. 行为：越线**抛** `BudgetExceeded`（不是静默记数继续）；
2. 行为：`limit=0`（默认）**不限制**——不能因加熔断把既有行为改坏；
3. 结构：`orchestrator.run_project` **真的**把配置上限传进了 `usage.activate`（AST 判调用实参）；
4. 结构：`DEFAULTS` 登记了 `run_token_limit`（可发现、可覆写）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from medkit.core import usage as U

_ORCH_SRC = Path(__file__).resolve().parents[1] / "medkit" / "core" / "orchestrator.py"


# ------------------------------------------------------------------ 行为
def test_add_raises_when_over_limit() -> None:
    ctx = U.UsageContext(limit_tokens=100)
    ctx.add(prompt_tokens=60, completion_tokens=30)      # 90 ≤ 100 → 不抛
    assert ctx.snapshot()["prompt_tokens"] == 60
    with pytest.raises(U.BudgetExceeded) as ei:
        ctx.add(prompt_tokens=20)                         # 110 > 100 → 抛
    assert ei.value.used == 110 and ei.value.limit == 100


def test_no_limit_means_unbounded() -> None:
    """默认/unset 上限 ⇒ 永不抛（保护既有行为）。"""
    ctx = U.UsageContext()
    ctx.add(prompt_tokens=10_000_000, completion_tokens=10_000_000)
    assert ctx.snapshot()["prompt_tokens"] == 10_000_000


def test_set_limit_can_tighten_and_clear() -> None:
    ctx = U.UsageContext()
    ctx.add(prompt_tokens=5000)
    assert ctx.limit == 0
    ctx.set_limit(1000)                                   # 收紧后，下一次 add 即越线
    with pytest.raises(U.BudgetExceeded):
        ctx.add(prompt_tokens=1)
    ctx.set_limit(0)                                      # 清除后不再抛
    ctx.add(prompt_tokens=999_999)
    assert ctx.snapshot()["prompt_tokens"] > 1_000_000 - 1


def test_budget_exceeded_is_runtimeerror() -> None:
    """必须是 RuntimeError 子类（既有 `except Exception` 兜底能接住，且可被优先捕获）。"""
    assert issubclass(U.BudgetExceeded, RuntimeError)


def test_context_manager_carries_limit() -> None:
    with U.context(limit_tokens=50) as ctx:
        ctx.add(prompt_tokens=40)
        with pytest.raises(U.BudgetExceeded):
            ctx.add(prompt_tokens=20)
    # 退出上下文后回到默认无限制账本
    assert U.current().limit == 0


# ------------------------------------------------------------------ 结构
def test_run_project_wires_limit_into_activate() -> None:
    """`run_project` 必须把配置值传给 `usage.activate(...)`（AST 判实参名）。"""
    tree = ast.parse(_ORCH_SRC.read_text(encoding="utf-8"))
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == "run_project"), None)
    assert fn is not None, "未找到 run_project"
    activations = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "activate"
    ]
    assert activations, "run_project 未调用 usage.activate"
    # 必须带 limit_tokens 关键字实参
    kws = [k.arg for k in activations[0].keywords]
    assert "limit_tokens" in kws, f"activate 未传 limit_tokens（实为 {kws}）"


def test_defaults_registers_run_token_limit() -> None:
    from medkit.core import config as cfg
    assert "run_token_limit" in cfg.DEFAULTS
    assert cfg.DEFAULTS["run_token_limit"] == 0, "默认必须关（0 = 不限制），避免突然掐断用户运行"
