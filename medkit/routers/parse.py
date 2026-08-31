"""routers：素材解析（本地文本层 / 示例素材 / 素材会话 S3 复用）。"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..core import extract as ex
from ..core import sessions as sessmod
from ..core.slice import slice_text
from ._common import TEXT_SUFFIXES, _analyze_slices, _parse_bytes

router = APIRouter()

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "data" / "samples"
SAMPLE_TEXTBOOK = SAMPLE_DIR / "样例_儿科学_节选.md"
SAMPLE_TEACHER = SAMPLE_DIR / "样例_教师重点.md"


@router.post("/api/parse")
async def parse_files(files: list[UploadFile] = File(...),
                      role: str = Form("textbook")) -> dict[str, Any]:
    """本地文本层解析（线程池执行，绝不阻塞事件循环）；OCR 走任务接口。"""
    async def process(f: UploadFile) -> dict[str, Any]:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in TEXT_SUFFIXES:
            return {"name": f.filename,
                    "error": f"不支持的类型 {suffix}（支持 PDF/DOCX/MD/TXT；图片需开启 OCR）"}
        data = await f.read()
        return await asyncio.to_thread(_parse_bytes, f.filename, data, suffix)

    results = await asyncio.gather(*(process(f) for f in files))
    return {"role": role, "results": list(results)}


@router.get("/api/sample")
def sample_materials() -> dict[str, Any]:
    """一键载入示例素材（体验用）：返回与 /api/parse 相同结构。

    WP-12：纯净安装包不含示例数据 → 明确返回 available=False（前端隐藏/提示）。
    """
    if not SAMPLE_DIR.is_dir() or not SAMPLE_TEXTBOOK.exists() or not SAMPLE_TEACHER.exists():
        return {"sample": False, "available": False,
                "error": "示例素材仅开发版可用（纯净版不含示例数据）；请用你自备教材与教师重点，或上传官方 306 大纲"}

    def load(path: Path, name: str) -> dict[str, Any]:
        blocks = ex.extract_text(path)
        slices = slice_text(blocks)
        info = _analyze_slices(slices, blocks)
        return {"name": name, "ok": True, **info}

    try:
        return {"sample": True,
                "subject": "儿科学（示例）",
                "teacher_text": "\n".join(s["text"] for s in load(SAMPLE_TEACHER, "示例_教师重点.md")["slices"]),
                "textbook": load(SAMPLE_TEXTBOOK, "示例_教材_儿科学节选.md"),
                "teacher": load(SAMPLE_TEACHER, "示例_教师重点.md")}
    except ex.ExtractError as e:
        return {"sample": False, "error": str(e)}


# ---------------------------------------------------------------- 素材会话（S3 素材库复用）
class SessionBody(BaseModel):
    name: str = ""
    role: str = "textbook"
    source_name: str = ""
    slices: list[dict[str, Any]] = []


@router.post("/api/sessions")
def create_session(body: SessionBody) -> dict[str, Any]:
    """把一次解析结果保存为素材会话（跨项目复用 / 多教材合并）。"""
    try:
        info = sessmod.save_session(body.name, body.role, body.slices, body.source_name)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **info}


@router.get("/api/sessions")
def list_sessions() -> dict[str, Any]:
    return {"sessions": sessmod.list_sessions()}


@router.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict[str, Any]:
    try:
        return sessmod.get_session(sid)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(404, str(e)) from e


@router.delete("/api/sessions/{sid}")
def delete_session(sid: str) -> dict[str, Any]:
    try:
        ok = sessmod.delete_session(sid)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if not ok:
        raise HTTPException(404, "素材会话不存在")
    return {"ok": True}
