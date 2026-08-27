"""routers：真题考点频次（WP-02）——粘贴/上传真题文本 → 草稿 → 人工确认 → 热力表/导出。

命名空间 /api/library/realexams/*。红线：未确认数据不进权重；任何输出不含真题原文。
"""

from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..core import db as dbs
from ..core import realexams as rex

router = APIRouter()


class AnalyzeBody(BaseModel):
    text: str = ""
    subject: str = ""


class ConfirmItem(BaseModel):
    id: str = ""
    subject: str = ""
    chapter: str = ""
    item: str = ""
    freq: int = 1
    confirmed: bool = True


class ConfirmBody(BaseModel):
    items: list[ConfirmItem]


@router.post("/api/library/realexams/analyze")
def rex_analyze(body: AnalyzeBody) -> dict[str, Any]:
    if not body.text.strip():
        raise HTTPException(400, "请粘贴或上传真题文本")
    return rex.analyze(body.text, body.subject)


@router.post("/api/library/realexams/analyze-file")
async def rex_analyze_file(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise HTTPException(400, "文件编码无法识别（请用 UTF-8）")
    if not text.strip():
        raise HTTPException(400, "文件为空")
    return rex.analyze(text, "")


@router.get("/api/library/realexams")
def rex_list(subject: str = "", confirmed: bool = False) -> dict[str, Any]:
    dbs.migrate()
    return {"drafts": rex.list_drafts(subject, confirmed=confirmed),
            "all": rex.list_drafts(subject, confirmed=None)}


@router.post("/api/library/realexams/confirm")
def rex_confirm(body: ConfirmBody) -> dict[str, Any]:
    if not body.items:
        raise HTTPException(400, "无条目")
    return rex.confirm([it.model_dump() for it in body.items])


@router.delete("/api/library/realexams/{rid}")
def rex_delete(rid: str) -> dict[str, Any]:
    if not rex.delete(rid):
        raise HTTPException(404, "条目不存在")
    return {"ok": True}


@router.get("/api/library/realexams/freq")
def rex_freq(subject: str = "") -> dict[str, Any]:
    dbs.migrate()
    return rex.freq_view(subject)


@router.get("/api/library/realexams/report")
def rex_report(subject: str = "") -> dict[str, str]:
    dbs.migrate()
    return {"markdown": rex.report_md(subject)}
