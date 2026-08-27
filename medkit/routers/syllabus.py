"""routers：考试锚定（WP-01 大纲覆盖度引擎）——内置大纲种子 / 教师重点自动导入 / 覆盖报表。

命名空间 /api/syllabus/*，挂载进 main.py。零 LLM 原则：教师重点自动处理（两档解析）、
覆盖判定/报告全本地。唯一 LLM 触点：官方大纲文件导入的契约抽取（K3/IMP-13，
/seed/parse-file + /seed/import-file，回退本地规则；spike 核验 recall 100% / precision 96.5%）。
数据在 SQLite syllabus_items（迁移 v2；用户自供内容 source 统一为 'teacher'，迁移 v4 归一）。

大纲标准二选一（与前端「数据标准」切换一一对应）：
- ``seed``：软件内置西综306 大纲（bundled 种子，``ensure`` 幂等导入；亦可上传官方大纲 md 入库）；
- ``teacher``：用户导入的教师重点内容（文件/粘贴 自动解析结构化入库，或项目切片同步）。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..core import db as dbs
from ..core import syllabus as syl
from ._common import require_flag

router = APIRouter()


# ---------------------------------------------------------------- 模型
class ParseBody(BaseModel):
    text: str
    subject: str = ""


class ConfirmItem(BaseModel):
    subject: str
    chapter: str = ""
    item: str
    weight: float = 1.0


class ConfirmBody(BaseModel):
    items: list[ConfirmItem]
    replace: bool = False   # True = 目标 (subject, chapter) 的教师重点条目先删后插（订正模式）


class SeedBody(BaseModel):
    force: bool = False


class TeacherImportBody(BaseModel):
    text: str
    subject: str = ""


# ---------------------------------------------------------------- 种子与元信息
@router.get("/api/syllabus/status")
def syllabus_status() -> dict[str, Any]:
    require_flag("syllabus")
    dbs.migrate()
    return {"seed": syl.seed_info(),
            "subjects": syl.list_subjects(),
            "teacher": {"items": sum(s["items"] for s in syl.list_subjects("teacher")),
                        "subjects": [s["subject"] for s in syl.list_subjects("teacher")]}}


@router.post("/api/syllabus/ensure")
def syllabus_ensure(body: SeedBody) -> dict[str, Any]:
    """导入/重建软件内置西综306 大纲种子（source='seed'，幂等）。"""
    require_flag("syllabus")
    dbs.migrate()
    return syl.ensure_seed(force=body.force)


@router.post("/api/syllabus/sync-teacher")
def syllabus_sync_teacher() -> dict[str, Any]:
    """以教师重点为纲：从所有项目扫描 teacher 切片 → 考点条目（幂等）。"""
    require_flag("syllabus")
    return syl.sync_teacher()


# ---------------------------------------------------------------- 教师重点 解析/自动导入（零 LLM）
def _teacher_parse_or_400(text: str, subject: str) -> dict[str, Any]:
    """教师重点文本 → 两档结构化草稿（structured：章/条目；flat：要点行）。"""
    parsed = syl.import_teacher_text(text, subject)
    return {"drafts": parsed["drafts"][:200], "count": len(parsed["drafts"]),
            "mode": parsed["mode"], "subject": parsed["subject"],
            "note": parsed["note"]}


@router.post("/api/syllabus/parse")
def syllabus_parse(body: ParseBody) -> dict[str, Any]:
    """教师重点粘贴 → 自动解析预览（不落库）。mode: structured / flat / none。"""
    require_flag("syllabus")
    if not body.text.strip():
        raise HTTPException(400, "粘贴内容为空")
    return _teacher_parse_or_400(body.text, body.subject)


@router.post("/api/syllabus/teacher/import")
def syllabus_teacher_import(body: TeacherImportBody) -> dict[str, Any]:
    """教师重点文本 → 自动解析 + 结构化 + 知识点提取 + 入库（source='teacher'，幂等）。

    自动化一步到位：无需人工确认；重复导入同 (subject, chapter, item) 不重复（added=0）。
    """
    require_flag("syllabus")
    if not body.text.strip():
        raise HTTPException(400, "内容为空")
    parsed = syl.import_teacher_text(body.text, body.subject)
    if parsed["mode"] == "none":
        return {"mode": "none", "subject": parsed["subject"], "added": 0, "total": 0,
                "drafts": [], "note": parsed["note"]}
    saved = syl.add_teacher_items(parsed["drafts"])
    return {"mode": parsed["mode"], "subject": parsed["subject"],
            "added": saved["added"], "total": saved["total"],
            "drafts": parsed["drafts"][:50],
            "note": parsed["note"] + f"；入库新增 {saved['added']} 条（幂等，重复导入不重复）"}


_TEACHER_EXTS = (".pdf", ".docx", ".md", ".markdown", ".txt", ".text")
_TEACHER_FILE_MAX = 20 * 1024 * 1024  # 20MB（PDF/DOCX 可能较大；文本格式本就远小于此）


@router.post("/api/syllabus/teacher/import-file")
async def syllabus_teacher_import_file(file: UploadFile = File(...),
                                       subject: str = Form("")) -> dict[str, Any]:
    """教师重点文件（PDF 文本层 / DOCX / MD / TXT）→ 自动解析入库（零 LLM，幂等）。

    自动完成：文本抽取 → 两档解析（章/条目结构化 ↔ 要点行）→ 结构化整理 → 知识点提取 →
    落库（source='teacher'）。扫描件 PDF 等抽取失败时 mode='error'（不落库，note 提示 OCR）。
    """
    require_flag("syllabus")
    name = (file.filename or "").lower()
    ext = Path(name).suffix
    if ext not in _TEACHER_EXTS:
        raise HTTPException(
            400, "仅支持 PDF（带文本层）/ DOCX / MD / TXT；扫描件请先转文字版或走项目内 OCR")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件内容为空")
    if len(raw) > _TEACHER_FILE_MAX:
        raise HTTPException(400, "文件过大（限 20MB）")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        tmp.write(raw)
        tmp.flush()
        return syl.import_teacher_file(tmp.name, subject=subject)
    finally:
        tmp.close()
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass




# ---------------------------------------------------------------- 官方大纲文件导入（K3/IMP-13：LLM 契约抽取 → seed）
_SEED_EXTS = (".md", ".txt", ".text")


def _decode_text(raw: bytes) -> str:
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _seed_parse(text: str) -> dict[str, Any]:
    """官方大纲文本 → 草稿（LLM 契约抽取；LLM 不可用回退本地规则）。"""
    outline = syl.extract_outline(text)
    if outline:
        drafts = syl.outline_drafts(outline)
        if drafts:
            note = f"LLM 契约抽取（{len(outline['subjects'])} 科）"
            if outline.get("errors"):
                note += f"；{len(outline['errors'])} 科抽取失败已跳过"
            return {"drafts": drafts[:200], "count": len(drafts), "mode": "llm", "note": note}
    drafts = syl.parse_text(text)
    if not drafts:
        return {"drafts": [], "count": 0, "mode": "none",
                "note": "未识别到条目（建议上传带「一、科目 / （一）章 / 条目」结构的大纲 md）"}
    return {"drafts": drafts[:200], "count": len(drafts), "mode": "local",
            "note": "LLM 抽取不可用，已按本地规则回退"}


@router.post("/api/syllabus/seed/parse-file")
async def syllabus_seed_parse_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """官方大纲文件（md/txt）→ 结构化草稿预览（LLM 契约抽取，不落库）。"""
    require_flag("syllabus")
    name = (file.filename or "").lower()
    if not name.endswith(_SEED_EXTS):
        raise HTTPException(400, "仅支持 .md / .txt 文本大纲（PDF 请先转成 md）")
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(400, "文件过大（限 2MB）")
    text = _decode_text(raw).strip()
    if not text:
        raise HTTPException(400, "文件内容为空")
    return _seed_parse(text)


@router.post("/api/syllabus/seed/import-file")
async def syllabus_seed_import_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """官方大纲文件（md/txt）→ 抽取 + 落库（source='seed'，幂等）。返回预览 + 入库统计。"""
    parsed = await syllabus_seed_parse_file(file)
    drafts = parsed.get("drafts") or []
    if not drafts:
        return parsed
    saved = syl.add_seed_items(drafts)
    parsed.update(added=saved["added"], total=saved["total"], source="seed")
    return parsed
# ---------------------------------------------------------------- 确认落库（merge/订正，source='teacher'）
@router.post("/api/syllabus/confirm")
def syllabus_confirm(body: ConfirmBody) -> dict[str, Any]:
    """教师重点草稿 → 落库（source='teacher'；二选一模型下用户自供内容统一为教师重点）。

    幂等：同 (subject, chapter, item) 重复确认不重复；replace=True 时仅删目标
    (subject, chapter) 的 teacher 行（不动内置大纲 seed 行）。
    """
    require_flag("syllabus")
    if not body.items:
        raise HTTPException(400, "无条目")
    replaced = 0
    if body.replace:
        with dbs.tx(write=True) as cur:
            targets = {(i.subject.strip(), (i.chapter or "教师重点").strip())
                       for i in body.items}
            for subject, chapter in targets:
                cur.execute(
                    "DELETE FROM syllabus_items WHERE subject=? AND chapter=? AND source='teacher'",
                    (subject, chapter))
                replaced += cur.rowcount
    drafts = [{"subject": i.subject, "chapter": i.chapter or "教师重点",
               "item": i.item, "weight": i.weight} for i in body.items]
    res = syl.add_teacher_items(drafts)
    return {"added": res["added"], "replaced_rows": replaced, "source": "teacher"}


# ---------------------------------------------------------------- 查询与报表
@router.get("/api/syllabus/tree")
def syllabus_tree(subject: str = "", source: str = "all") -> dict[str, Any]:
    require_flag("syllabus")
    dbs.migrate()
    return syl.coverage(subject, source)


@router.get("/api/syllabus/coverage")
def syllabus_coverage(subject: str = "", source: str = "all") -> dict[str, Any]:
    """覆盖度：source 限定 seed（内置大纲）/ teacher（教师重点）；all 供出题锚定等内部聚合。"""
    require_flag("syllabus")
    dbs.migrate()
    return syl.coverage(subject, source)


@router.get("/api/syllabus/report")
def syllabus_report(subject: str = "", source: str = "all") -> dict[str, str]:
    require_flag("syllabus")
    dbs.migrate()
    return {"markdown": syl.report_md(subject, source)}
