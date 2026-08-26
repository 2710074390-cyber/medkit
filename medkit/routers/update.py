"""routers：v0.6 更新检查（GitHub Releases）。"""

from typing import Any

from fastapi import APIRouter

from ..core import update as upd

router = APIRouter()


@router.get("/api/update/check")
def update_check() -> dict[str, Any]:
    return upd.check()
