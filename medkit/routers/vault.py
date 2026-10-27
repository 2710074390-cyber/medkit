"""错题 vault 路由 —— 本地文件为真身，数据库只是可弃索引。

## 端点族

- `/api/vault/status|init|rebuild` —— 目录管理
- `/api/vault/mistakes` —— 列表（读索引）
- `/api/vault/mistakes/{path}/download` —— **下载一条错题的原始 md**（attachment）
- `/api/vault/export` —— 合并导出全部错题为一个 md（attachment）
- `/api/vault/stats` —— 薄弱项（纯本地计算，零 LLM）
- `/api/vault/mistakes/{path}/section` —— **唯一写回入口**（用户确认后才调）
- `/api/vault/import-from-library` —— 从旧错题本一键导出为 md
- `/api/vault/ask` —— Agent 问答（SSE 流式，支持多轮 history）
- `/api/vault/handbook` —— 按科目生成「薄弱点复习手册」（LLM，可缺席）
- `/api/vault/scheduler` —— 复习调度算法（FSRS 默认 / SM-2 legacy）查询与切换

## 红线

`{path}` 一律先过 `_safe_vault_rel()`：必须解析后仍落在 vault 根内
（路径穿越在本域里等于能读/写用户整个磁盘）。
"""

from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse

from ..agents.mistake_agent import ask as _agent_ask
from ..agents.mistake_agent import handbook as _agent_handbook
from ..core import config as cfg
from ..core import library as lib
from ..core import scheduler as _sched
from ..core import vault, vault_index

router = APIRouter(tags=["vault"])


def _attachment(name: str) -> str:
    """`Content-Disposition` 值：中文名走 RFC 5987 `filename*`（ASCII 兜底名另给）。

    只写 `filename="中文.md"` 时，各浏览器对非 ASCII 的处理不一致（有的乱码、有的丢名），
    故统一补一个 `filename*=UTF-8''…`。
    """
    ascii_fallback = "".join(ch if ch.isascii() and ch not in '"\\' else "_" for ch in name) or "download.md"
    return f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(name)}"



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
def mistakes(subject: str = "", status: str = "", q: str = "",
             chapter: str = "", topic: str = "") -> dict[str, Any]:
    items = vault_index.list_items(subject=subject, status=status, q=q,
                                   chapter=chapter, topic=topic)
    return {"count": len(items), "items": items, "subjects": vault_index.subjects()}


@router.get("/api/vault/stats")
def stats(subject: str = "") -> dict[str, Any]:
    return vault_index.stats(subject=subject)


# ------------------------------------------------------------------ 下载（原始 md）
@router.get("/api/vault/mistakes/{path:path}/download")
def download_mistake(path: str) -> FileResponse:
    """下载**一条错题的原始 markdown**（真身文件，逐字节原样）。

    为什么必须是「原始文件」而不是重新序列化：错题文件是真身、用户可以手改，
    重新序列化会把用户的排版/注释按本程序的格式重写一遍——那等于悄悄改了用户的东西。
    """
    rel = _safe_vault_rel(path)
    f = vault.vault_root() / rel
    if not f.is_file():
        raise HTTPException(status_code=404, detail="文件不存在（可能已删）")
    return FileResponse(f, media_type="text/markdown; charset=utf-8", filename=Path(rel).name)


@router.get("/api/vault/export")
def export_all(subject: str = "") -> Response:
    """把错题**合并导出为一个 md**（每题原文之间用 `---` 分隔），便于打印 / 发给同学。

    同样读**原始文件字节**（不做重序列化），只是拼在一起。
    """
    items = vault_index.list_items(subject=subject)
    chunks: list[str] = []
    for it in items:
        rel = str(it.get("path") or "")
        if not rel:
            continue
        try:
            f = vault.vault_root() / rel
            if not f.is_file():
                continue
            chunks.append(f.read_text(encoding="utf-8", errors="replace").rstrip())
        except OSError:
            continue                      # 单文件读失败只跳过它，不整包失败
    head = f"# MedKit 错题导出（{subject or '全部科目'}）\n\n共 {len(chunks)} 条 · 导出日期 {date.today().isoformat()}\n"
    body = head + "\n\n---\n\n" + "\n\n---\n\n".join(chunks) + "\n" if chunks else head + "\n（没有可导出的错题）\n"
    name = f"MedKit错题导出_{subject or '全部科目'}_{date.today().isoformat()}.md"
    return Response(content=body, media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition": _attachment(name)})


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


# ------------------------------------------------------------------ 新建错题（直接成 md）
@router.post("/api/vault/mistakes")
def create_mistake(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """新建一条错题文件（拍题/粘贴都走这里）。**题干为唯一必填**，其余可空。"""
    q = str(body.get("question") or "").strip()
    if not q:
        raise HTTPException(status_code=400, detail="题干不能为空")
    rec = {
        "question": q,
        "options": [str(o).strip() for o in (body.get("options") or []) if str(o).strip()],
        "answer": str(body.get("answer") or "").strip(),
        "analysis": str(body.get("analysis") or "").strip(),
        "my_reasoning": str(body.get("my_reasoning") or "").strip(),
        "doubts": str(body.get("doubts") or "").strip(),
        "subject": str(body.get("subject") or "").strip(),
        "chapter": str(body.get("chapter") or "").strip(),
        "topic": str(body.get("topic") or "").strip(),
        "source": "manual",
    }
    return {"ok": True, **vault_index.create_item(rec)}


# ------------------------------------------------------------------ 复习闭环（调度器，零 LLM）
@router.get("/api/vault/review/due")
def review_due(subject: str = "") -> dict[str, Any]:
    """今日到期的错题 + 当前调度算法标识。

    `scheduler` 字段让前端能如实显示「FSRS」还是「SM-2」——排期口径变了必须可见，
    不能让用户在不知情的情况下经历复习节奏的变化。
    """
    items = vault_index.due_items(subject=subject)
    return {"count": len(items), "items": items,
            "stats": vault_index.review_stats(subject=subject),
            "scheduler": _sched.get_scheduler().name}


@router.get("/api/vault/scheduler")
def scheduler_info() -> dict[str, Any]:
    """当前复习调度算法与参数（前端「我的 → 复习设置」用）。"""
    return {"ok": True, **_sched.describe()}


@router.put("/api/vault/scheduler")
def scheduler_set(body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """切换复习调度算法。

    **切换不丢卡数据**：两套算法的字段并存于错题 front matter，各自只读自己的那套
    （`core/scheduler.py` 的模块 docstring 有字段职责表）。切回 SM-2 时，
    `ease` / `interval` 还是上次用 SM-2 时的值（可能已过时，但语义自洽）。
    """
    name = str(body.get("name") or "").strip().lower()
    if name not in _sched.SCHEDULERS:
        raise HTTPException(400, "复习算法只能是 fsrs 或 sm2")
    saved = cfg.load()
    saved["scheduler"] = name
    if body.get("fsrs_retention") is not None:
        try:
            ret = float(body["fsrs_retention"])
        except (TypeError, ValueError):
            raise HTTPException(400, "fsrs_retention 必须是 0.70~0.98 的数字")
        if not 0.70 <= ret <= 0.98:
            raise HTTPException(400, "fsrs_retention 必须是 0.70~0.98 的数字")
        saved["fsrs_retention"] = ret
    if body.get("fsrs_max_interval") is not None:
        try:
            mx = int(body["fsrs_max_interval"])
        except (TypeError, ValueError):
            raise HTTPException(400, "fsrs_max_interval 必须是正整数（天）")
        if mx < 1 or mx > 3650:
            raise HTTPException(400, "fsrs_max_interval 必须是 1~3650 的天数")
        saved["fsrs_max_interval"] = mx
    try:
        cfg.save(saved)
    except OSError as e:  # noqa: BLE001 —— 磁盘满/只读 → 500 中文提示（不裸 traceback）
        raise HTTPException(500, f"配置保存失败（磁盘满或只读？）：{e}") from e
    return {"ok": True, **_sched.describe(name)}


@router.post("/api/vault/review/{path:path}/grade")
def review_grade(path: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """写回一次复习评分（quality 0~5）：推进调度器并原子改写该文件。"""
    rel = _safe_vault_rel(path)
    # 用 `body["quality"]` 而非 `.get()`：`.get()` 的静态类型是 `Any | None`，
    # mypy 2.4 起会直接拒 `int(Any | None)`（本地 mypy 2.3.1 放行 ⇒ 曾假绿一次，
    # CI 两平台同时红：routers/vault.py:168 arg-type）。缺键走 KeyError，行为与原来一致
    # （原来 `.get()` 得 None → int(None) 抛 TypeError，同样落到 400）。
    try:
        quality = int(body["quality"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=400, detail="quality 必须是 0~5 的整数")
    if not 0 <= quality <= 5:
        raise HTTPException(status_code=400, detail="quality 越界（应在 0~5）")
    try:
        return vault_index.grade_item(rel, quality)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在（可能已删）")


# ------------------------------------------------------------------ Agent 问答
@router.post("/api/vault/ask")
async def ask(body: dict[str, Any] = Body(...)) -> StreamingResponse:
    """错题助手（SSE）：`stage` / `delta`（**逐字流式**）/ `result` / `error`，以 `[DONE]` 收尾。

    `cancel`（`threading.Event`）在**流结束、出错、或客户端断连**（Starlette 关闭生成器 ⇒
    `GeneratorExit`）时一律置位——`LLMClient.chat_stream` 见位即截断 provider 流，
    所以用户点「停止生成」/关页面之后**不会继续烧 token**（口径与已删的讲解/提问域一致）。
    """
    question = str(body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    scope = str(body.get("scope") or "").strip()
    path = _safe_vault_rel(str(body.get("path") or "")) if body.get("path") else ""
    raw_hist = body.get("history")
    history: list[dict[str, Any]] = []
    if isinstance(raw_hist, list):
        for h in raw_hist[-8:]:                      # 只带最近 8 条，避免上下文无限膨胀
            if isinstance(h, dict) and str(h.get("content") or "").strip():
                history.append({"role": str(h.get("role") or "user"),
                                "content": str(h.get("content"))[:4000]})
    cancel = threading.Event()

    async def gen():
        try:
            async for ev in _agent_ask(question, scope=scope, path=path,
                                       history=history, cancel=cancel):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001  工具失败必须让用户看见，不得静默
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
        finally:
            cancel.set()
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


# ------------------------------------------------------------------ 薄弱点复习手册
@router.post("/api/vault/handbook")
async def handbook(body: dict[str, Any] = Body(default_factory=dict)) -> StreamingResponse:
    """按科目生成「薄弱点复习手册」（SSE 流式，Markdown 正文）。

    **依据只有用户自己的 vault**（薄弱项分组 + 错题条目原文）；没有 API Key / 没有错题时
    都给出明确原因，不返回半成品。空错题库**不调模型**（直接给说明）。
    """
    subject = str(body.get("subject") or "").strip()
    cancel = threading.Event()

    async def gen():
        try:
            async for ev in _agent_handbook(subject, cancel=cancel):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001  模型/网络异常必须让用户看见原因
            yield f"data: {json.dumps({'type': 'error', 'message': f'手册生成失败：{e}'}, ensure_ascii=False)}\n\n"
        finally:
            cancel.set()
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")

