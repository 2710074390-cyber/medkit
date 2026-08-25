"""routers：配置预设（内置三套只读 / 用户自建 CRUD）。"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import presets as prs
from ._common import _safe_pid

router = APIRouter()


class PresetBody(BaseModel):
    name: str
    desc: str = ""
    payload: dict[str, Any] = {}


@router.get("/api/presets")
def list_presets() -> dict[str, Any]:
    return prs.list_presets()


@router.post("/api/presets")
def create_preset(body: PresetBody) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(400, "预设名称不能为空")
    return prs.save_preset(body.name, body.desc, body.payload)


@router.delete("/api/presets/{pid}")
def delete_preset(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)  # v0.5：路径穿越消毒（旧实现可删任意 ~/.medkit 下 .json）
    ok = prs.delete_preset(pid)
    if not ok:
        raise HTTPException(400, "内置预设不可删除（或预设不存在）")
    return {"ok": True}
