"""门禁①-D 查重（U6）：题干 n-gram Jaccard 相似度，>阈值 → warn 进 MedFix 改写。

纯本地零依赖；对相邻切片/多轮生成的近似题（只差数字/表述）给出预警。
"""

import re
from typing import Any

# v0.5：保留数字/字母/汉字（剥除标点与空白），使「血钾 5.5」与「血钾 7.0」保持可判别；
# 旧实现 [\W\d]+ 连数字一起剥 → 两道仅数值不同的临床题被误报近似重复。
_NON_WORD = re.compile(r"[\W_]+")
NGRAM_SIZES = (2, 3, 4)
THRESHOLD = 0.8


def _grams(text: str) -> set[str]:
    t = _NON_WORD.sub("", text or "")
    out: set[str] = set()
    if len(t) < 2:
        return {t} if t else set()
    for k in NGRAM_SIZES:
        for i in range(len(t) - k + 1):
            out.add(t[i:i + k])
    return out


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def check_dup(questions: list[dict[str, Any]], threshold: float = THRESHOLD) -> dict[str, Any]:
    """返回 {issues:[{q_id, code:'DUP', severity:'warn', reason}], pairs:n}。"""
    issues: list[dict[str, Any]] = []
    pairs = 0
    grams: dict[str, set[str]] = {}
    for q in questions:
        cid = str(q.get("id") or "")
        if q.get("question"):
            grams[cid] = _grams(q["question"])
    ids = list(grams)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            sim = _jaccard(grams[a], grams[b])
            if sim < threshold:
                continue
            pairs += 1
            issues.append({
                "q_id": b, "code": "DUP", "severity": "warn",
                "reason": f"与 {a} 题干高度相似（n-gram Jaccard {sim:.2f} ≥ {threshold}），"
                          f"建议改写题干或更换考点"})
    return {"issues": issues, "pairs": pairs,
            "fail_count": 0}
