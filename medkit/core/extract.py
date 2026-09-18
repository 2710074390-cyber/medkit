"""素材文本抽取：PDF(文本层) / DOCX / MD / TXT → 文本块列表。

每个文本块 = {index, label(页码/来源), text, chars}；扫描件 PDF 显式报错并给出提示。
"""

from pathlib import Path
from typing import Any


class ExtractError(Exception):
    """素材解析失败（含可读提示）。"""


MAX_PDF_PAGES = 2000   # M2-04：单份 PDF 页数上限


def extract_text(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise ExtractError(f"文件不存在: {p}")
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(p)
    if suffix == ".docx":
        return _extract_docx(p)
    if suffix in (".md", ".markdown", ".txt"):
        return _extract_textfile(p)
    raise ExtractError(f"不支持的文件类型: {suffix}（支持 PDF/DOCX/MD/TXT）")


def _extract_pdf(p: Path) -> list[dict[str, Any]]:
    try:
        import fitz  # PyMuPDF
    except ImportError as e:
        raise ExtractError("未安装 PyMuPDF（pip install pymupdf）") from e
    blocks: list[dict[str, Any]] = []
    total = 0
    with fitz.open(str(p)) as doc:
        # M2-04（R8+W）：页数闸——超长 PDF 会把解析线程占住很久（本机 DoS），
        # 且本软件按「一次一章」设计，数千页的 PDF 本就不该整体上传。
        if doc.page_count > MAX_PDF_PAGES:
            raise ExtractError(
                f"PDF 页数过多（{doc.page_count} 页，上限 {MAX_PDF_PAGES} 页）——"
                "请按章节拆分后再上传")
        for i, page in enumerate(doc):
            text = (page.get_text() or "").strip()
            if not text:
                continue
            total += len(text)
            blocks.append({"index": len(blocks), "label": f"P{i + 1}", "text": text,
                           "chars": len(text)})
    if total < 200:
        raise ExtractError(
            "该 PDF 疑似扫描件（无文本层）。请改用「连接服务商」页配置的 MinerU OCR 识别"
            "（扫描件会自动识别并加入输入），或使用带文本层的 PDF。"
        )
    return blocks


def _extract_docx(p: Path) -> list[dict[str, Any]]:
    try:
        import docx
    except ImportError as e:
        raise ExtractError("未安装 python-docx（pip install python-docx）") from e
    d = docx.Document(str(p))
    parts: list[str] = [para.text for para in d.paragraphs if para.text.strip()]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n".join(parts)
    if len(text) < 50:
        raise ExtractError("DOCX 内容过少或为空")
    return [{"index": 0, "label": "DOCX", "text": text, "chars": len(text)}]


def _extract_textfile(p: Path) -> list[dict[str, Any]]:
    raw = p.read_bytes()
    text = None
    for enc in ("utf-8", "gbk"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    if len(text.strip()) < 20:
        raise ExtractError("文本文件内容过少或为空")
    return [{"index": 0, "label": p.suffix.upper(), "text": text, "chars": len(text)}]
