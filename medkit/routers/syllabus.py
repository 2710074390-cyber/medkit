"""routers：考试锚定（WP-01 大纲覆盖度引擎）——大纲种子/粘贴解析/确认/覆盖报表。

命名空间 /api/syllabus/*，挂载进 main.py。零 LLM 原则：parse 走本地规则（可选 LLM 增强由
前端后续版本接 chat_json）；覆盖判定/报告纯本地。数据在 SQLite syllabus_items（迁移 v2）。
"""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..core import db as dbs
from ..core import syllabus as syl

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
    replace: bool = False   # True = 同一 (subject, chapter) 先删后插（订正模式）


class SeedBody(BaseModel):
    force: bool = False


# ---------------------------------------------------------------- 种子与元信息
@router.get("/api/syllabus/status")
def syllabus_status() -> dict[str, Any]:
    dbs.migrate()
    return {"seed": syl.seed_info(),
            "subjects": syl.list_subjects(),
            "teacher": {"items": sum(s["items"] for s in syl.list_subjects("teacher")),
                        "subjects": [s["subject"] for s in syl.list_subjects("teacher")]}}


@router.post("/api/syllabus/ensure")
def syllabus_ensure(body: SeedBody) -> dict[str, Any]:
    dbs.migrate()
    return syl.ensure_seed(force=body.force)


@router.post("/api/syllabus/sync-teacher")
def syllabus_sync_teacher() -> dict[str, Any]:
    """以教师重点为纲：从所有项目扫描 teacher 切片 → 考点条目（幂等）。"""
    return syl.sync_teacher()


# ---------------------------------------------------------------- 粘贴解析（零 LLM）
@router.post("/api/syllabus/parse")
def syllabus_parse(body: ParseBody) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(400, "粘贴内容为空")
    drafts = syl.parse_text(body.text, body.subject)
    if not drafts:
        return {"drafts": [], "note": "未识别到条目（格式建议：章一行、条目一行，如「一、呼吸系统」「1、肺通气」）"}
    return {"drafts": drafts[:200], "count": len(drafts)}


# ---------------------------------------------------------------- 确认落库（merge/订正）
@router.post("/api/syllabus/confirm")
def syllabus_confirm(body: ConfirmBody) -> dict[str, Any]:
    if not body.items:
        raise HTTPException(400, "无条目")
    dbs.migrate()
    added = replaced = 0
    with dbs.tx(write=True) as cur:
        if body.replace:
            targets = {(i.subject, i.chapter) for i in body.items}
            for subject, chapter in targets:
                cur.execute("DELETE FROM syllabus_items WHERE subject=? AND chapter=?",
                            (subject, chapter))
                replaced += cur.rowcount
        for it in body.items:
            rec = {"id": syl._row_id(it.subject, it.chapter, it.item, "item", "paste"),
                   "subject": it.subject.strip(), "chapter": it.chapter.strip(),
                   "kind": "item", "item": it.item.strip(),
                   "weight": it.weight, "source": "paste", "created_at": _now()}
            exists = cur.execute("SELECT 1 FROM syllabus_items WHERE id=?",
                                 (rec["id"],)).fetchone()
            dbs.put_row(cur, "syllabus_items", rec,
                        ("subject", "chapter", "kind", "item", "weight", "source"))
            if not exists:
                added += 1
    return {"added": added, "replaced_rows": replaced}


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 查询与报表
@router.get("/api/syllabus/tree")
def syllabus_tree(subject: str = "", source: str = "all") -> dict[str, Any]:
    dbs.migrate()
    return syl.coverage(subject, source)


@router.get("/api/syllabus/coverage")
def syllabus_coverage(subject: str = "", source: str = "all") -> dict[str, Any]:
    dbs.migrate()
    return syl.coverage(subject, source)


@router.get("/api/syllabus/report")
def syllabus_report(subject: str = "", source: str = "all") -> dict[str, str]:
    dbs.migrate()
    return {"markdown": syl.report_md(subject, source)}
