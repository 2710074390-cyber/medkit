"""routers：只读诊断（U-15）。

让用户能自查「按钮点了没反应」的原因：近 N 条错误摘要 + 计数。
服务本身已由 `main.py` 的回环守卫（Host 白名单 + Origin 校验）限制在本机访问，
故不再额外加守卫；端点**只读**且不回显敏感字段（`core/errors.redact` 已在记录前脱敏）。
"""

from typing import Any

from fastapi import APIRouter

from ..core import errors as errs

router = APIRouter()


@router.get("/api/diagnostics/errors")
def diagnostics_errors(limit: int = 50) -> dict[str, Any]:
    """错误计数 + 最近 N 条摘要（不含 API Key / 模型原始输出）。"""
    return errs.snapshot(limit)
