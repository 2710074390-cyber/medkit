"""错题 vault 路由 —— 本地文件为真身，数据库只是可弃索引。

## 端点族

- `/api/vault/status|init|rebuild` —— 目录管理
- `/api/vault/mistakes` —— 列表（读索引）
- `/api/vault/stats` —— 薄弱项（纯本地计算，零 LLM）
- `/api/vault/mistakes/{path}/section` —— **唯一写回入口**（用户确认后才调）
- `/api/vault/import-from-library` —— 从旧错题本一键导出为 md
- `/api/vault/ask` —— Agent 问答（SSE 流式）

## 红线

`{path}` 一律先过 `_safe_vault_rel()`：必须解析后仍落在 vault 根内
（路径穿越在本域里等于能读/写用户整个磁盘）。
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import StreamingResponse

from ..agents.mistake_agent import ask as _agent_ask
from ..core import library as lib
from ..core import vault, vault_index

router = APIRouter(tags=["vault"])


def _safe_vault_rel(rel: str) -> str:
    """把用户/前端给的相对路径约束在 vault 根内；越界即 400。

    两道判据（缺一不可）：
    ① **形态**：空、绝对路径、任何 `..` 段一律先拒——它们从来不是合法的索引键；
    ② **解析**：`resolve()` 后必须仍在 vault 根内（挡符号链接）。
    """
    raw = str(rel or "").replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith("/") or ".." in raw.split("/"):
        raise HTTPException(status_code=400, detail="非法路径")
    root = vault.vault_root().resolve()
    target = (root / raw).resolve()
    if target != root and root not in target.parents:
        raise HTTPException(status_code=400, detail="路径越出 vault 根目录")
    return raw


# ------------------------------------------------------------------ 目录管理
@router.get("/api/vault/status")
def status() -> dict[str, Any]:
    root = vault.vault_root()
    return {
        "root": str(root),
        "is_vault": vault.is_vault(root),
        "exists": root.is_dir(),
        "default_root": str(vault.default_root()),
        "count": vault_index.count(),
        "subjects": vault_index.subjects(),
    }


@router.post("/api/vault/init")
def init(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """创建/确认 vault；`path` 为空则用当前配置或默认目录。"""
    raw = str(body.get("path") or "").strip()
    try:
        info = vault.init_vault(raw or None)
    except vault.VaultError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if raw:
        vault.set_vault_dir(raw)
        info["root"] = str(vault.vault_root())
    out = vault_index.rebuild()
    return {**info, **out}


@router.post("/api/vault/rebuild")
def rebuild() -> dict[str, Any]:
    return vault_index.rebuild()


# ------------------------------------------------------------------ 读
@router.get("/api/vault/mistakes")
def mistakes(subject: str = "", status: str = "", q: str = "") -> dict[str, Any]:
    items = vault_index.list_items(subject=subject, status=status, q=q)
    return {"count": len(items), "items": items, "subjects": vault_index.subjects()}


@router.get("/api/vault/stats")
def stats(subject: str = "") -> dict[str, Any]:
    return vault_index.stats(subject=subject)


# ------------------------------------------------------------------ 写回（用户确认后）
@router.post("/api/vault/mistakes/{path:path}/section")
def set_section(path: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    rel = _safe_vault_rel(path)
    key = str(body.get("key") or "")
    try:
        return vault_index.update_section(rel, key, str(body.get("text") or ""))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在（可能已删）")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------------ 一键迁移
@router.post("/api/vault/import-from-library")
async def import_from_library(body: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    """把旧错题本（`library.mistakes`）导出为 vault md。**不删旧数据**。"""
    only_unlearned = bool(body.get("only_unlearned"))
    recs = lib.list_mistakes()
    if only_unlearned:
        recs = [r for r in recs if not r.get("learned")]
    root = vault.vault_root()
    if not vault.is_vault(root):
        vault.init_vault(str(root))
    written, skipped = 0, 0
    for rec in recs:
        if not str(rec.get("question") or "").strip():
            skipped += 1
            continue
        vault.write_record(rec, root)
        written += 1
    out = vault_index.rebuild()
    return {"written": written, "skipped": skipped, "total_in_library": len(recs), **out}


# ------------------------------------------------------------------ Agent 问答
@router.post("/api/vault/ask")
async def ask(body: dict[str, Any] = Body(...)) -> StreamingResponse:
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    scope = str(body.get("scope") or "").strip()
    path = _safe_vault_rel(str(body.get("path") or "")) if body.get("path") else ""

    async def gen():
        try:
            async for ev in _agent_ask(question, scope=scope, path=path):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001  工具失败必须让用户看见，不得静默
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
