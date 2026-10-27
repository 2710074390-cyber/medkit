"""routers：题库同步（`/api/bank/sync/*`）——从 MedAgentWork 项目拉取最新题库。

分层与题库域其余端点一致：`routers → core(banksync) → db`，本文件不出现 SQL。

## 为什么 status 网络失败也返回 200

同步面板的头等诉求是「我上次同步到哪了」。远端不可达时把它渲染成红叉，
用户会以为**本地数据坏了**（其实是 GitHub 连不上）。故 `status` 降级为
「本地状态 + `error` 文案」，由前端在面板里用一行提示说明——与 `routers/update.py`
的更新检查（`{"error": "network"}` 走 200）同一口径。

`/run` 则不同：**一个源都没跑成**属于请求失败，用 502 让前端如实报错。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import banksync

router = APIRouter()


class SyncRunBody(BaseModel):
    paths: list[str] = []      # 空 = 全部远端题库
    dedupe: str = "update"     # update | skip | new（与题库域导入同义）
    force: bool = False        # true = 忽略 sha 未变，强制重下重并


@router.get("/api/bank/sync/sources")
def sync_sources(refresh: bool = False) -> dict[str, Any]:
    """远端题库清单 + 本地同步状态。`refresh=1` 绕过 10 分钟 tree 缓存。"""
    return banksync.status(refresh=bool(refresh))


@router.get("/api/bank/sync/state")
def sync_state() -> dict[str, Any]:
    """本地同步状态（**不联网**）：面板初开/离线时用它先渲染出「上次同步于…」。"""
    state = banksync.load_state()
    sources = state.get("sources") or {}
    rows = [{"path": p, **rec} for p, rec in sources.items() if isinstance(rec, dict)]
    rows.sort(key=lambda r: str(r.get("synced_at") or ""), reverse=True)
    return {"ok": True, "updated_at": state.get("updated_at") or "", "sources": rows}


@router.post("/api/bank/sync/run")
def sync_run(body: SyncRunBody) -> dict[str, Any]:
    """执行同步。逐源独立成败——部分失败仍返回 200 与逐源结果。"""
    try:
        return banksync.sync(body.paths, dedupe=body.dedupe, force=bool(body.force))
    except banksync.SyncError as e:
        code = {"busy": 409, "empty": 400, "ratelimit": 429}.get(e.kind, 502)
        raise HTTPException(code, e.message)
