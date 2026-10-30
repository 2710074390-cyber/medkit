"""错点变式训练出题人（P1 · 2026-10-29）。

只依据**原错题**生成同考点、不同问法的 2~5 道单最佳答案题。
生成后逐题过现有 `gates.options_check`；不过门禁的题**不展示**，并如实回报原因。
"""

from __future__ import annotations

import re
from typing import Any

from ..core.schema import QuestionItem
from ..gates import options_check
from . import get_client, render_prompt

MAX_TOKENS = 6000
MAX_VARIANTS = 5
VARIANT_TYPES = {"stem", "scenario", "option", "negative", "numeric", "discrimination"}

_OPT_PREFIX = re.compile(r"^[A-E][\.、\s]")
_PUNCT = re.compile(r"[\s·、，,。．\.；;：:！!？?（）()\[\]【】「」『』<>/\\|_\-—–～~*#\"'`]+")


class VariantError(RuntimeError):
    pass


def _option_map(v: Any) -> dict[str, str]:
    if not isinstance(v, dict):
        return {}
    out: dict[str, str] = {}
    for k, val in v.items():
        key = str(k or "").strip().upper()
        key = key[-1:] if key and key[-1] in "ABCDE" else ""
        text = str(val or "").strip()
        if key and text:
            out[key] = text[:400]
    return out


def _norm_text(s: Any) -> str:
    return _PUNCT.sub("", str(s or "").lower())


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: str, b: str) -> float:
    ba, bb = _bigrams(_norm_text(a)), _bigrams(_norm_text(b))
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _opt_key(options: list[Any]) -> str:
    return "|".join(sorted(_norm_text(_OPT_PREFIX.sub("", str(o).strip())) for o in options))


def _answers(s: Any) -> str:
    return "".join(ch for ch in str(s or "").upper() if ch in "ABCDE")


def _normalize(raw: Any, idx: int, source: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """把一条模型输出归一成可判分的变式；返回 (item, "") 或 (None, reason)。"""
    if not isinstance(raw, dict):
        return {}, "不是对象"
    question = str(raw.get("question") or "").strip()
    if not question:
        return {}, "缺题干"
    raw_opts = raw.get("options")
    opts = [str(o).strip() for o in raw_opts if str(o).strip()] if isinstance(raw_opts, list) else []
    if len(opts) != 5:
        return {}, f"选项数不是 5（实际 {len(opts)}）"
    answer = _answers(raw.get("answer"))
    if len(answer) != 1:
        return {}, "答案必须是单个字母（本版只做单最佳答案）"
    if ord(answer) - ord("A") >= len(opts):
        return {}, "答案键越界"
    analysis = str(raw.get("analysis") or "").strip()
    if not analysis:
        return {}, "缺解析"
    rats = _option_map(raw.get("option_rationales"))
    missing = [L for L in "ABCDE"[:len(opts)] if not rats.get(L)]
    if missing:
        return {}, f"逐选项理由缺失：{'/'.join(missing)}"
    mics = _option_map(raw.get("misconceptions"))
    # 误解标签只保留错误选项，防止模型把正确项也写成「误解」
    mics = {k: v for k, v in mics.items() if k != answer}
    try:
        diff = int(float(raw.get("difficulty") or 3))
    except (TypeError, ValueError):
        diff = 3
    diff = diff if 1 <= diff <= 5 else 3
    vtype = str(raw.get("variant_type") or "stem").strip().lower()
    if vtype not in VARIANT_TYPES:
        vtype = "stem"

    item = {
        "id": f"v{idx}",
        "question": question,
        "type": "A1",
        "bloom": "理解",
        "options": opts,
        "answer": answer,
        "analysis": analysis[:1200],
        "option_rationales": {k: v[:300] for k, v in rats.items()},
        "misconceptions": {k: v[:80] for k, v in mics.items()},
        "source_quote": str(raw.get("source_quote") or "").strip()[:600],
        "difficulty": diff,
        "variant_type": vtype,
        "variant_of": str(source.get("id") or source.get("path") or ""),
    }
    # 契约形状校验（QuestionItem 只收它认识的字段；variant_type/of 是后加的）。
    try:
        QuestionItem.model_validate({k: item[k] for k in (
            "question", "type", "bloom", "options", "answer", "analysis",
            "option_rationales", "misconceptions", "source_quote", "difficulty")})
    except Exception as e:  # noqa: BLE001  pydantic 细节不回显
        return {}, f"契约校验失败：{e.__class__.__name__}"
    issues = options_check.check_all([item])
    if not issues.get("passed"):
        reasons = [x.get("reason") or x.get("code") for x in issues.get("issues", [])
                   if x.get("severity") == "fail"]
        return {}, "门禁未过：" + "；".join(reasons[:2])
    return item, ""


def _duplicate(item: dict[str, Any], source: dict[str, Any],
               accepted: list[dict[str, Any]]) -> bool:
    """纯重复 / 几乎重复都拒绝；**问法变式**（题干同、选项不同）允许。"""
    q = str(item.get("question") or "")
    sq = str(source.get("question") or "")
    if _norm_text(q) == _norm_text(sq) and _opt_key(item.get("options") or []) == _opt_key(source.get("options") or []):
        return True
    for prev in accepted:
        if _norm_text(q) == _norm_text(prev.get("question")) and \
                _opt_key(item.get("options") or []) == _opt_key(prev.get("options") or []):
            return True
    if _jaccard(q, sq) > 0.92 and \
            _jaccard(_opt_key(item.get("options") or []), _opt_key(source.get("options") or [])) > 0.8:
        return True
    return False


def generate(source: dict[str, Any], n: int = 3, client: Any = None) -> dict[str, Any]:
    """生成变式题。`source` 是 `vault.read_record` 的记录（含 question/options/answer/analysis）。"""
    n = max(1, min(int(n or 3), MAX_VARIANTS))
    question = str(source.get("question") or "").strip()
    if not question:
        raise VariantError("原错题没有题干，无法生成变式")
    opts = source.get("options") or []
    answer = _answers(source.get("answer"))
    if len(opts) < 2 or not answer:
        raise VariantError("原错题缺少选项或答案——先在错题本里补全，再练变式")
    client = client or get_client("gen")
    # 提示词名写成字面量：门禁按 `render_prompt("<literal>")` 判「该提示词真的被加载」。
    system = render_prompt(
        "variant_gen.md",
        n=n,
        subject=str(source.get("subject") or "未分类"),
        chapter=str(source.get("chapter") or ""),
        topic=str(source.get("topic") or ""),
        question=question,
        options="\n".join(f"- {o}" for o in opts),
        answer=str(source.get("answer") or ""),
        analysis=str(source.get("analysis") or "（无）"),
        understanding=str(source.get("my_reasoning") or "（未写）"),
        doubts=str(source.get("doubts") or "（未写）"),
    )
    data = client.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": f"请输出 {n} 道变式题的 JSON。"}],
        temperature=0.6, max_tokens=MAX_TOKENS)
    raw_items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(raw_items, list) or not raw_items:
        raise VariantError("模型没有返回变式题（JSON 缺少 items 数组）")
    items: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for i, raw in enumerate(raw_items[:n]):
        item, reason = _normalize(raw, i + 1, {**source, "id": source.get("id") or source.get("path")})
        if not item:
            rejected.append({"index": i, "reason": reason})
            continue
        if _duplicate(item, {**source, "options": opts}, items):
            rejected.append({"index": i, "reason": "与原题或已接受的变式重复"})
            continue
        items.append(item)
    if not items:
        raise VariantError("这轮变式全部未通过校验——换一个错点或重试")
    return {"items": items, "rejected": rejected}
