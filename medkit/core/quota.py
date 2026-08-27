"""模块配额分配：切片字数 ×（1 + 教师重点词频加权）→ 各切片目标题数。

教师重点中出现的术语（2~8 字词语）在切片中命中越多，该切片配额越高 ——
桌面版以「教师重点词频」替代历史考频蓝图（y 轴：用户自己的重点 = 最准的考频信息）。
"""

import re
from collections import Counter
from typing import Any

KEYWORD_MIN_LEN = 2
KEYWORD_MAX_LEN = 8
BOOST = 2.0  # 命中权重上限倍率
TEACHER_TEXT_LIMIT = 4000  # 与 medgen.TEACHER_CHAR_LIMIT 对齐：配额词频与出题注入同口径（超长重点后段不参与锚定）


def extract_keywords(teacher_text: str, limit: int = 120) -> list[str]:
    """从教师重点中切词（中文按 2~8 字滑窗 + 英文单词），返回高频词。"""
    text = re.sub(r"\s+", "", teacher_text)
    counter: Counter[str] = Counter()
    for k in range(KEYWORD_MIN_LEN, KEYWORD_MAX_LEN + 1):
        for i in range(0, len(text) - k + 1):
            seg = text[i:i + k]
            if re.search(r"[\u4e00-\u9fa5]", seg):
                counter[seg] += 1
    # 只留出现 ≥2 次的词，且去掉公共字串（简化：保持原文滑窗，不做切词器）
    words = [w for w, c in counter.most_common(limit * 4) if c >= 2]
    # 去掉被长词包含的短词（去重噪声）
    words.sort(key=len, reverse=True)
    picked: list[str] = []
    for w in words:
        if any(w in p for p in picked):
            continue
        picked.append(w)
        if len(picked) >= limit:
            break
    return picked


def allocate(slices: list[dict[str, Any]], teacher_text: str,
             target: int) -> list[dict[str, Any]]:
    """返回 [{sid, count}]，舍入后求和 == target（最大余额法）。"""
    kws = extract_keywords(teacher_text[:TEACHER_TEXT_LIMIT])
    weights: list[float] = []
    for s in slices:
        text = s.get("text", "")
        hits = sum(text.count(kw) for kw in kws)
        base = max(len(text), 1)
        boost = 1.0 + BOOST * min(hits / 20.0, 1.0)  # 命中 20 次封顶
        weights.append(base * boost)
    total_w = sum(weights) or 1.0
    raw = [target * w / total_w for w in weights]
    counts = [int(x) for x in raw]
    remainder = target - sum(counts)
    # 最大余额法分配余数
    fracs = sorted(range(len(raw)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in range(remainder):
        counts[fracs[i % len(counts)]] += 1
    return [{"sid": s["sid"], "count": c} for s, c in zip(slices, counts, strict=False) if c > 0]
