"""门禁①-D 查重（U6）+ 查源（S1-3）：n-gram 相似度 → warn 进 MedFix 改写 / 人工复核。

纯本地零依赖；两类比对：
1. **题 ↔ 题**（原 U6）：相邻切片/多轮生成的近似题（只差数字/表述）给出预警。
2. **题 ↔ 源文本**（S1-3 / R8+W）：题目是否整段照抄源切片或真题原文。原实现只比题与题之间，
   于是「把教材原文整段搬成题干」这类题既不重复也不违反选项规则，能一路通过所有门禁进入产物。
"""

import re
from typing import Any, Optional

# v0.5：保留数字/字母/汉字（剥除标点与空白），使「血钾 5.5」与「血钾 7.0」保持可判别；
# 旧实现 [\W\d]+ 连数字一起剥 → 两道仅数值不同的临床题被误报近似重复。
_NON_WORD = re.compile(r"[\W_]+")
NGRAM_SIZES = (2, 3, 4)
THRESHOLD = 0.8
# S1-3：题↔源文本用**包含率**而非 Jaccard——题短源长时 Jaccard 必然很小，
# 但「整段照抄」的包含率接近 1，才是要抓的信号。
SOURCE_COPY_THRESHOLD = 0.9
SOURCE_COPY_MIN_CHARS = 12   # 题干过短（如「下列哪项正确」）不参与查源，避免噪声


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


def _containment(a: set[str], b: set[str]) -> float:
    """a 有多少比例落在 b 里（|a∩b| / |a|）。用于「题目是否照抄源文本」。"""
    if not a:
        return 0.0
    return len(a & b) / len(a)


def _group_key(q: dict[str, Any]) -> tuple:
    """S3：案例/选项组内子题互不查重（共用题干/共享选项天然相似，非重复题）。"""
    gk = q.get("group_kind")
    if gk == "case" and q.get("case_id"):
        return ("case", q.get("case_id"))
    if gk == "option_group" and isinstance(q.get("group"), dict):
        return ("og", tuple(str(o) for o in (q["group"].get("options") or [])))
    return ("single", q.get("id") or str(id(q)))


def check_source_overlap(questions: list[dict[str, Any]],
                         source_texts: dict[str, str],
                         threshold: float = SOURCE_COPY_THRESHOLD,
                         fallback_texts: Optional[list[str]] = None) -> dict[str, Any]:
    """S1-3（R8+W）：生成题 ↔ 源切片/真题原文 的照抄检测。

    每题按其 `sid` 取回源切片文本（取不到时退到 `fallback_texts`，如教师重点/真题全文），
    用 n-gram **包含率**衡量「题干有多少内容直接来自源文本」；≥ 阈值 → warn 进人工复核。

    只 warn 不 fail：正常命题本就会复用教材术语，直接剔除会误伤；照抄是「可疑」而非「错误」，
    交由 MedFix 改写 / 人工判断。
    """
    issues: list[dict[str, Any]] = []
    checked = 0
    fallback_grams = [_grams(t) for t in (fallback_texts or [])]
    for q in questions:
        stem = str(q.get("question") or "")
        cid = str(q.get("id") or "")
        if len(_NON_WORD.sub("", stem)) < SOURCE_COPY_MIN_CHARS:
            continue
        grams = _grams(stem)
        if not grams:
            continue
        sid = str(q.get("sid") or "")
        cands: list[set[str]] = []
        if sid and source_texts.get(sid):
            cands.append(_grams(source_texts[sid]))
        cands.extend(fallback_grams)
        if not cands:
            continue          # 无源文本可比（如纯自命题）→ 不妄判
        checked += 1
        best = max(_containment(grams, g) for g in cands)
        if best >= threshold:
            issues.append({
                "q_id": cid, "code": "SRC_COPY", "severity": "warn",
                "reason": f"题干 {best:.0%} 的内容与源文本（切片 {sid or '未标注'}）重合，"
                          f"疑似照抄原文——建议改写题干或换考点表述"})
    return {"issues": issues, "checked": checked, "fail_count": 0}


def check_dup(questions: list[dict[str, Any]], threshold: float = THRESHOLD,
              source_texts: Optional[dict[str, str]] = None,
              fallback_texts: Optional[list[str]] = None) -> dict[str, Any]:
    """返回 {issues:[...], pairs:n, src_checked:n, fail_count:0}。

    `source_texts` 给出「sid → 源切片文本」时，同时做题↔源文本查源（S1-3）；
    不传则只做原有的题↔题查重（向后兼容）。
    """
    issues: list[dict[str, Any]] = []
    pairs = 0
    grams: dict[str, set[str]] = {}
    keys: dict[str, tuple] = {}
    for q in questions:
        cid = str(q.get("id") or "")
        if q.get("question"):
            grams[cid] = _grams(q["question"])
            keys[cid] = _group_key(q)
    ids = list(grams)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if keys.get(a) == keys.get(b):
                continue  # 同案例/同选项组 → 跳过组内查重
            sim = _jaccard(grams[a], grams[b])
            if sim < threshold:
                continue
            pairs += 1
            issues.append({
                "q_id": b, "code": "DUP", "severity": "warn",
                "reason": f"与 {a} 题干高度相似（n-gram Jaccard {sim:.2f} ≥ {threshold}），"
                          f"建议改写题干或更换考点"})
    src_checked = 0
    if source_texts or fallback_texts:
        src = check_source_overlap(questions, source_texts or {},
                                   fallback_texts=fallback_texts)
        issues.extend(src["issues"])
        src_checked = src["checked"]
    return {"issues": issues, "pairs": pairs, "src_checked": src_checked,
            "fail_count": 0}
