"""routers：素材解析（本地文本层 / 示例素材）。"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, UploadFile

from ..core import extract as ex
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
    """一键载入示例素材（体验用）：返回与 /api/parse 相同结构。"""
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
