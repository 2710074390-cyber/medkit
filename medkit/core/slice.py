"""章节切片：识别章节标题（第X章 / 一、 / 1.1 / ## 等），按 ≤max_chars 输出切片。

切片是出题管线的原子单位：{sid, title, text, source, page}。
"""

import re
from typing import Any

# 章节标题启发式（按优先级）
CHAPTER_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s*第[一二三四五六七八九十百0-9]+[章节篇讲部]"),
    re.compile(r"^\s*Chapter\s+\d+", re.I),
    re.compile(r"^\s*[一二三四五六七八九十]+、\S"),
    re.compile(r"^\s*\d+(?:\.\d+){1,3}\s+\S"),          # 1.1 / 1.1.1
    re.compile(r"^\s*附录[一二三]?"),
    re.compile(r"^\s*#{1,3}\s+\S"),
]

MAX_CHARS = 4000
MAX_TITLE = 40


def _is_chapter(line: str) -> bool:
    return any(p.match(line) for p in CHAPTER_PATTERNS)


def slice_text(blocks: list[dict[str, Any]], max_chars: int = MAX_CHARS) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None

    def flush() -> None:
        nonlocal cur
        if cur and cur["text"].strip():
            slices.append(cur)
        cur = None

    # B25：PDF 按页成块——不再每块重置标题/强切切片；续页续接当前切片，章节标题跨页传递
    # （否则章节跨页后切片标题退化为「P2」「P3」，污染 subtopic 与「按章去重」）
    cur_title = str(blocks[0].get("label", "") if blocks else "")[:MAX_TITLE]
    for blk in blocks:
        if not cur_title and blk.get("label"):
            cur_title = str(blk.get("label"))[:MAX_TITLE]
        for para in blk["text"].splitlines():
            para = para.strip()
            if not para:
                continue
            if _is_chapter(para):
                flush()
                cur_title = para[:MAX_TITLE]
            if cur is None:
                cur = {"sid": f"S{len(slices) + 1:03d}", "title": cur_title,
                       "text": "", "source": blk.get("source", ""),
                       "page": blk.get("label", "")}
            # 超长切片按段落拆（续接切片沿用当前章节标题）
            if len(cur["text"]) + len(para) + 1 > max_chars:
                flush()
                cur = {"sid": f"S{len(slices) + 1:03d}", "title": cur_title,
                       "text": "", "source": blk.get("source", ""),
                       "page": blk.get("label", "")}
            cur["text"] = (cur["text"] + "\n" + para).strip()
    flush()

    # 兜底：一个切片也没有（无章节标题）→ 全文单切片
    if not slices:
        all_text = "\n".join(b["text"] for b in blocks)
        for i in range(0, len(all_text), max_chars):
            slices.append({"sid": f"S{len(slices) + 1:03d}", "title": blk_title(blocks, i),
                           "text": all_text[i:i + max_chars],
                           "source": "", "page": ""})
    return slices


def blk_title(blocks: list[dict[str, Any]], _i: int) -> str:
    return (blocks[0].get("label", "") if blocks else "全文")[:MAX_TITLE]
