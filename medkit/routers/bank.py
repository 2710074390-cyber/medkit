"""routers：题库域（`/api/bank/*`）——自建 / 导入题库 + 应用内刷题 + 判错回流。

## 分层

`routers → core(bank/bank_iq) → db`。本文件**不出现** `cur.execute` / `dbs.tx`
（跨层一致性用例锁住），存储一律经 `core/bank.py`。

## 与押题卷的关系

判分口径与「导出的押题卷」**同源**（`core/bank.answers_equal` ↔
`render/qbank_html.py` 内联 JS 的 `answersEqual`）；判错回流复用
`library.sync_from_paper()`——同一批题目走同一条通道，不产生第二套口径。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from ..core import bank as banklib
from ..core import bank_iq
from ..core import config as cfg
from ._common import MAX_FILE_SIZE, _safe_pid, read_upload_capped

router = APIRouter()

_MAX_ITEMS = 500     # 单次导入/入库上限（对齐 mistakes 的 500 条口径）
_TEXT_LIMIT = 5_000_000   # 单次粘贴/文件文本上限（≈5MB；万题级 md 通常 <2MB）


def _fmt_of(name: str, fallback: str = "md") -> str:
    ext = Path(name or "").suffix.lower().lstrip(".")
    if ext in ("md", "markdown"):
        return "md"
    if ext in ("txt", "text"):
        return "txt"
    if ext == "json":
        return "json"
    if ext == "csv":
        return "csv"
    return fallback


def _preview(text: str, fmt: str) -> dict[str, Any]:
    if not (text or "").strip():
        raise HTTPException(400, "内容为空——请粘贴或上传题库文本")
    if len(text) > _TEXT_LIMIT:
        raise HTTPException(400, "内容过大（>5MB），请分批导入")
    rows = bank_iq.parse_bank_text(text, fmt)
    items: list[dict[str, Any]] = []
    warnings: list[str] = []
    for i, r in enumerate(rows, 1):
        rec = bank_iq.normalize_item(r, bid="", origin="md_import", now="")
        if rec is None:
            warnings.append(f"#{i}: 缺题干，已跳过")
            continue
        w = rec.pop("_warn", "")
        if w:
            warnings.append(f"#{i}: {w}")
        rec["_type_inferred"] = not bool(r.get("type"))
        items.append(rec)
    return {"items": items, "total": len(items),
            "parsed": len(rows), "warnings": warnings[:50]}


class BankBody(BaseModel):
    name: str = ""
    subject: str = ""
    source: str = ""


class ItemBody(BaseModel):
    """题目（新增/编辑共用）。`PUT` 用 `exclude_unset=True` 做**局部更新**。"""

    question: str = ""
    options: list[str] = []
    answer: str = ""
    analysis: str = ""
    type: str = ""
    bloom: str = ""
    subtopic: str = ""
    case_id: str = ""
    case_order: int = 0
    case_stem: str = ""
    group_kind: str = ""
    group: dict[str, Any] | None = None
    image_ref: str = ""
    data_table: str = ""
    source_type: str = ""
    source_year: str = ""
    starred: bool = False
    note: str = ""


class StarBody(BaseModel):
    starred: bool = False


class ImportPreviewBody(BaseModel):
    text: str = ""
    fmt: str = "md"


class ImportCommitBody(BaseModel):
    bank_id: str = ""          # 空 → 新建题库（用 name/subject）
    name: str = ""
    subject: str = ""
    source: str = ""
    items: list[dict[str, Any]] = []
    dedupe: str = "update"     # update | skip | new
    raw: str = ""              # 导入原文（存下来供「重新解析」；旧客户端不传则为空）
    fmt: str = "md"


class GradeBody(BaseModel):
    bank_id: str = ""
    item_id: str = ""
    user_answer: str = ""
    session_id: str = ""
    mode: str = "practice"
    secs: int = 0
    confidence: Optional[int] = None    # 作答前自评 1..5；None/0 = 未采集（可跳过）


@router.get("/api/bank/banks")
def list_banks() -> dict[str, Any]:
    return {"banks": banklib.list_banks()}


@router.post("/api/bank/banks")
def create_bank(body: BankBody) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(400, "题库名称不能为空")
    return {"ok": True, "bank": banklib.create_bank(body.name, body.subject, body.source)}


@router.get("/api/bank/banks/{bid}")
def get_bank(bid: str) -> dict[str, Any]:
    bank = banklib.get_bank(bid)
    if bank is None:
        raise HTTPException(404, "题库不存在")
    return {"bank": bank, "items": banklib.list_items(bid), "stats": banklib.bank_stats(bid)}


@router.put("/api/bank/banks/{bid}")
def update_bank(bid: str, body: BankBody) -> dict[str, Any]:
    rec = banklib.update_bank(bid, {"name": body.name, "subject": body.subject, "source": body.source})
    if rec is None:
        raise HTTPException(404, "题库不存在")
    return {"ok": True, "bank": rec}


@router.delete("/api/bank/banks/{bid}")
def delete_bank(bid: str) -> dict[str, Any]:
    res = banklib.delete_bank(bid)
    if not res.get("deleted"):
        raise HTTPException(404, "题库不存在")
    return {"ok": True, **res}


@router.delete("/api/bank/items/{iid}")
def delete_item(iid: str) -> dict[str, Any]:
    if not banklib.delete_item(iid):
        raise HTTPException(404, "题目不存在")
    return {"ok": True}


@router.post("/api/bank/banks/{bid}/items")
def add_item(bid: str, body: ItemBody) -> dict[str, Any]:
    """手动加一道题（F2）。走与导入同一条归一通道 ⇒ 形状一致，不产生第二套题目形态。"""
    if banklib.get_bank(bid) is None:
        raise HTTPException(404, "题库不存在")
    if not body.question.strip():
        raise HTTPException(400, "题干不能为空")
    stats = banklib.add_items(bid, [body.model_dump()], origin="manual", dedupe="new")
    if not stats.get("added"):
        raise HTTPException(400, "题目未入库：" + "；".join(stats.get("errors") or ["未知原因"]))
    return {"ok": True, **stats}


@router.put("/api/bank/items/{iid}")
def update_item(iid: str, body: ItemBody) -> dict[str, Any]:
    """改一道题（F2 局部更新）：导入的题库常有解析偏差，改掉即可，不必重新导入。"""
    patch = body.model_dump(exclude_unset=True)
    if "question" in patch and not str(patch["question"]).strip():
        raise HTTPException(400, "题干不能为空")
    try:
        rec = banklib.update_item(iid, patch)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if rec is None:
        raise HTTPException(404, "题目不存在")
    return {"ok": True, "item": rec}


@router.post("/api/bank/items/{iid}/star")
def star_item(iid: str, body: StarBody) -> dict[str, Any]:
    """题目级收藏（F9）。"""
    rec = banklib.set_starred(iid, bool(body.starred))
    if rec is None:
        raise HTTPException(404, "题目不存在")
    return {"ok": True, "item": rec}


@router.get("/api/bank/items/{iid}/reveal")
def reveal_item(iid: str) -> dict[str, Any]:
    """背题模式：只读返回答案 / 解析 / 笔记 / 出处，**不记作答流水**。

    与 `/grade` 的区别只有一处，但很关键：背题是「看答案」不是「作答」——
    若走 grade 会把每道背过的题都记成一次作答（正确答案恒为「未作答 ⇒ 错」），
    既污染正确率，又会把题库里所有题灌进错题本。故单开一个只读端点。
    """
    it = banklib.get_item(iid)
    if it is None:
        raise HTTPException(404, "题目不存在")
    return {
        "ok": True,
        "answer": str(it.get("answer") or ""),
        "analysis": str(it.get("analysis") or ""),
        "note": str(it.get("note") or ""),
        "source_type": str(it.get("source_type") or ""),
        "source_year": str(it.get("source_year") or ""),
        "starred": bool(it.get("starred")),
        # v0.22 阶段 4：背题模式也要看得到逐选项理由（旧题为空值，不影响既有前端）
        "option_rationales": dict(it.get("option_rationales") or {}),
        "misconceptions": dict(it.get("misconceptions") or {}),
        "source_quote": str(it.get("source_quote") or ""),
        "difficulty": int(it.get("difficulty") or 0),
    }


@router.post("/api/bank/import/preview")
def import_preview(body: ImportPreviewBody) -> dict[str, Any]:
    """解析预览（**不落库**）：返回识别到的题目 + 逐条告警，由用户确认后再 commit。"""
    return {"ok": True, **_preview(body.text, body.fmt)}


@router.post("/api/bank/import/file")
async def import_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """上传文件解析预览（md/txt/json/csv，本地解析零 LLM，**不落库**）。"""
    raw = await read_upload_capped(file, MAX_FILE_SIZE, "文件过大（限 200MB）")
    if not raw:
        raise HTTPException(400, "文件为空")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gbk")
        except UnicodeDecodeError:
            raise HTTPException(400, "无法识别文件编码（请另存为 UTF-8）")
    return {"ok": True, **_preview(text, _fmt_of(file.filename or ""))}


@router.post("/api/bank/import/commit")
def import_commit(body: ImportCommitBody) -> dict[str, Any]:
    """确认入库：items 已由预览接口归一（含剥字母前缀）；此处再走一次 normalize 兜底。"""
    if not body.items:
        raise HTTPException(400, "没有可导入的题目（请先解析预览）")
    if len(body.items) > _MAX_ITEMS:
        raise HTTPException(400, f"单次最多导入 {_MAX_ITEMS} 条（请分批提交）")
    bid = (body.bank_id or "").strip()
    if bid:
        if banklib.get_bank(bid) is None:
            raise HTTPException(404, "题库不存在")
    else:
        bank = banklib.create_bank(body.name or "导入题库", body.subject, body.source or "md_import")
        bid = str(bank["id"])
    stats = banklib.add_items(bid, body.items, origin="md_import", dedupe=body.dedupe)
    # 保存原文：解析器将来改进时，可对**已入库的题库**一键重解析（见 db._V10_UP 注释）。
    # 旧客户端不传 raw ⇒ 该题库只是少了重解析能力，不影响本次导入。
    if (body.raw or "").strip():
        banklib.save_source(bid, body.raw, fmt=body.fmt)
    return {"ok": True, "bank_id": bid, **stats}


@router.post("/api/bank/banks/{bid}/reparse")
def reparse(bid: str) -> dict[str, Any]:
    """用保存的原文**重新解析**该题库（解析器改进后修旧数据）。

    会清空该题库的作答流水（题目 id 全部重发）；返回里 `attempts_cleared` 如实告知清了几条。
    """
    try:
        return {"ok": True, **banklib.reparse_bank(bid)}
    except KeyError as e:
        raise HTTPException(404, str(e.args[0] if e.args else "无法重解析"))


@router.get("/api/bank/banks/{bid}/drill")
def drill(bid: str, mode: str = "all", limit: int = 0) -> dict[str, Any]:
    """取练习题目集（mode：all/wrong/undone/starred）。返回的题目**不含答案/解析/笔记**（判分走 /grade）。"""
    if banklib.get_bank(bid) is None:
        raise HTTPException(404, "题库不存在")
    items = banklib.drill_items(bid, mode=mode, limit=max(0, min(int(limit or 0), 500)))
    safe = [{k: v for k, v in it.items() if k not in ("answer", "analysis", "note")} for it in items]
    return {"ok": True, "items": safe, "total": len(safe)}


@router.post("/api/bank/grade")
def grade(body: GradeBody) -> dict[str, Any]:
    """判一道题：比对答案 → 记流水 + 更新知识点状态 → 判错回流错题本。返回判分结果（含解析）。

    响应里的 `kc` 段说明本题影响了哪些知识点（`mapped=false` = 这题还没映射到知识点，
    不是错误，只是该题暂不参与掌握度）。`confidence` 越界一律 **400**，不静默吞掉——
    它是可选字段，但**填了就得对**，否则校准曲线会被脏数据污染。
    """
    if not body.item_id:
        raise HTTPException(400, "缺少题目 id")
    if body.confidence is not None and body.confidence != 0 and not 1 <= body.confidence <= 5:
        raise HTTPException(400, "confidence 必须是 1~5（或留空表示不采集）")
    try:
        res = banklib.grade(body.bank_id, body.item_id, body.user_answer,
                            session_id=body.session_id, mode=body.mode,
                            secs=max(0, int(body.secs or 0)),
                            confidence=int(body.confidence or 0))
    except KeyError as e:
        raise HTTPException(404, str(e.args[0] if e.args else "题目不存在"))
    return {"ok": True, **res}


@router.get("/api/bank/banks/{bid}/stats")
def stats(bid: str) -> dict[str, Any]:
    if banklib.get_bank(bid) is None:
        raise HTTPException(404, "题库不存在")
    return {"ok": True, "stats": banklib.bank_stats(bid)}


@router.get("/api/bank/today")
def today(date: str = "") -> dict[str, Any]:
    """今日作答汇总（每日目标进度；date 为空取本地今天）。"""
    return {"ok": True, **banklib.today_attempts(date)}


# ---------------------------------------------------------------- 从出题工坊导入（F11）
def _projects_root() -> Path:
    return Path(cfg.load()["projects_dir"])


def _final_questions(pid: str) -> Path:
    return _projects_root() / pid / "最终产物" / "questions_final.json"


@router.get("/api/bank/projects")
def list_import_sources() -> dict[str, Any]:
    """列出**可导入题库**的项目（已生成 `最终产物/questions_final.json` 的）。

    只读元数据与题数，不读题目正文（列表页不需要，也避免大文件被反复解析）。
    """
    root = _projects_root()
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return {"projects": out}
    for d in sorted(root.iterdir(), reverse=True):
        f = _final_questions(d.name)
        if not f.is_file():
            continue
        subject = ""
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            subject = str((meta or {}).get("subject") or "")
        except Exception:  # noqa: BLE001  元数据缺失/损坏不阻断列举
            subject = ""
        try:
            qs = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001  损坏产物跳过（不列出来，避免点了才报错）
            continue
        out.append({"pid": d.name, "subject": subject,
                    "count": len(qs) if isinstance(qs, list) else 0})
    return {"projects": out}


class ImportProjectBody(BaseModel):
    pid: str = ""
    bank_id: str = ""          # 空 → 新建题库
    name: str = ""
    dedupe: str = "update"


@router.post("/api/bank/import/project")
def import_project(body: ImportProjectBody) -> dict[str, Any]:
    """把出题工坊产出的题目**复制**进题库。

    **复制而非引用**：项目可能被删（`DELETE /api/projects/{pid}`），题库不该跟着消失
    （`docs/题库刷题功能_调研与实现方案_2026-10-02.md` §3.4）。
    """
    if not body.pid:
        raise HTTPException(400, "缺少项目 ID")
    pid = _safe_pid(body.pid)
    f = _final_questions(pid)
    if not f.is_file():
        raise HTTPException(404, "该项目还没有生成产物（最终产物/questions_final.json）")
    try:
        qs = json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "产物文件损坏，无法导入")
    if not isinstance(qs, list) or not qs:
        raise HTTPException(400, "产物里没有题目")
    if len(qs) > _MAX_ITEMS:
        raise HTTPException(400, f"单次最多导入 {_MAX_ITEMS} 条（该产物有 {len(qs)} 条，请分批）")
    bid = (body.bank_id or "").strip()
    if bid:
        if banklib.get_bank(bid) is None:
            raise HTTPException(404, "题库不存在")
    else:
        name = (body.name or "").strip()
        if not name:
            try:
                meta = json.loads((_projects_root() / pid / "meta.json").read_text(encoding="utf-8"))
                name = str((meta or {}).get("subject") or "")
            except Exception:  # noqa: BLE001
                name = ""
        bank = banklib.create_bank(name or f"项目 {pid}", "", source="project")
        bid = str(bank["id"])
    stats = banklib.add_items(bid, qs, origin="project", dedupe=body.dedupe)
    return {"ok": True, "bank_id": bid, "total": len(qs), **stats}


# ---------------------------------------------------------------- 导出回流（F10）
@router.get("/api/bank/banks/{bid}/export/paper")
def export_paper(bid: str) -> Response:
    """题库 → 交互押题卷 HTML（复用 `render/qbank_html.export_paper_html`，零 LLM）。

    **缺省 inline**（与 `/api/projects/{pid}/files/{name}` 同口径）：产物页依赖同源渲染，
    附件下载会让「在线打开」变成下载。要下载由前端自己加 `?dl=1` 语义（浏览器另存）。
    """
    bank = banklib.get_bank(bid)
    if bank is None:
        raise HTTPException(404, "题库不存在")
    items = banklib.list_items(bid)
    if not items:
        raise HTTPException(404, "题库里还没有题目")
    from ..render.qbank_html import export_paper_html

    title = f"{bank.get('name') or '题库'} · 押题卷"
    html = export_paper_html(items, title, subject=str(bank.get("subject") or ""),
                             notice="本卷由「练习」页的题库导出；判分与错题回流在应用内进行。")
    filename = f"bank_{bid}_paper.html"
    return Response(content=html, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'inline; filename="{filename}"'})
