"""门禁①-E 数值核验（S1-2 / R8+W）：把「医学事实」里可程序化核对的那部分从 LLM 自评里拿回来。

背景（收口报告 S1-2）：MedQC 的 F1 事实性规则（`prompts/medqc.md` 的 `| F1 |` 行）由 LLM 自评，
**没有程序化数值锚点**——同一模型既出题又判自己，剂量/阈值写错时可能被判「看似合理」而放行。
本模块补一条**不依赖 LLM** 的硬核对：题干与选项里出现的临床数值，必须能在该题的源切片
（或教师重点/真题全文）里找到出处；找不到即 `fail`，由 MedFix 带着源切片去改，改不动才剔除。

设计取舍（避免误伤——**首版就踩过坑**）：
- ⚠️ **只核「正确选项」的数值，绝不核干扰项**：选择题的干扰项**本来**就是错误数值
  （把 110 改成 150 才叫干扰项），若把 options 整体纳入核对，几乎所有数值型选择题都会被判 fail。
  首版实现正是如此，手工试跑立刻暴露——见 `tests/test_r8w_numeric_anchor.py` 的对照用例。
- 正确选项里的**临床单位**数值（mg/kg/ml/mmHg/mmol/℃/%/次每分…）无出处 → `fail`
  （「答案与教材不符」的直接程序化信号，对应 MedQC 的 F1 规则，但**不经过 LLM**）。
- 题干里的数值无出处 → `warn`（病例题干本就可自行构造「10 岁男童」）。
- 裸数字（无单位）→ 只 `warn`。
- 取不到源文本（纯自命题 / sid 缺失）→ **跳过**，不妄判。
"""

import re
from typing import Any, Optional

# 带这些单位的数值属「可核对的事实性数值」：正确选项里与源文本不符 → fail
CLINICAL_UNITS = ("mg", "g", "kg", "ml", "mmhg", "mmol", "μmol", "umol", "μ", "ug", "ng", "pg",
                  "iu", "u/l", "次/分", "次/min", "kcal", "kj", "cm", "mm", "℃", "°c", "%")

# 数字 + 紧随其后的单位片段（含 / 以覆盖 kcal/kg、次/分 这类复合单位）
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*([A-Za-zμ℃°%/]*)")
_OPT_PREFIX = re.compile(r"^[A-E][\.、\s]")


def _extract(text: str) -> list[tuple[float, str]]:
    """返回 [(数值, 单位小写)]；单位为空表示裸数字。"""
    out: list[tuple[float, str]] = []
    for m in _NUM_UNIT.finditer(text or ""):
        try:
            out.append((float(m.group(1)), (m.group(2) or "").lower()))
        except ValueError:
            continue
    return out


def _source_values(texts: list[str]) -> set[float]:
    vals: set[float] = set()
    for t in texts:
        for v, _u in _extract(t):
            vals.add(v)
    return vals


def _is_clinical(unit: str) -> bool:
    """单位是临床单位，或数字紧邻临床单位（如「110kcal/kg」被切成 kcal/kg）→ 视为硬数值。"""
    if not unit:
        return False
    if any(unit.startswith(u) for u in CLINICAL_UNITS):
        return True
    return any(u in unit for u in ("kcal", "kj", "mmhg", "mmol"))


def _effective_options(q: dict[str, Any]) -> list[str]:
    """B1 组题共享选项在 group 字段（与 options_check 同口径）。"""
    opts = q.get("options") or []
    if not opts and q.get("group_kind") == "option_group":
        grp = q.get("group") or {}
        if isinstance(grp, dict):
            opts = grp.get("options") or []
    return [str(o) for o in opts if isinstance(o, str)]


def _answer_texts(q: dict[str, Any]) -> list[str]:
    """把答案键（A~E / X 型多字母）映射回**正确选项**的文本。"""
    opts = _effective_options(q)
    ans = re.sub(r"[\s,，、]+", "", str(q.get("answer") or "")).upper()
    out: list[str] = []
    for ch in ans:
        idx = ord(ch) - ord("A")
        if 0 <= idx < len(opts):
            out.append(_OPT_PREFIX.sub("", opts[idx].strip()).strip())
    return out


def check_numbers(questions: list[dict[str, Any]],
                  source_texts: Optional[dict[str, str]] = None,
                  fallback_texts: Optional[list[str]] = None,
                  ) -> dict[str, Any]:
    """核验**正确选项**与题干中的数值是否有源文本出处。

    返回 `{issues, checked, fail_count, passed}`；issue 形如
    `{q_id, code:'NUM_UNSOURCED', severity:'fail'|'warn', reason}`。
    """
    issues: list[dict[str, Any]] = []
    checked = 0
    for q in questions:
        cid = str(q.get("id") or "")
        sid = str(q.get("sid") or "")
        texts: list[str] = []
        if source_texts and sid and source_texts.get(sid):
            texts.append(source_texts[sid])
        texts.extend(t for t in (fallback_texts or []) if t)
        if not texts:
            continue                      # 无源可比 → 跳过（不妄判）
        allowed = _source_values(texts)
        if not allowed:
            continue                      # 源文本里本来就没数值 → 无从核对
        checked += 1
        # ① 正确选项：硬核对（fail）
        ans_text = " ".join(_answer_texts(q))
        hard = [(v, u) for v, u in _extract(ans_text)
                if v not in allowed and _is_clinical(u)]
        if hard:
            shown = "、".join(f"{v:g}{u}" for v, u in hard[:4])
            issues.append({
                "q_id": cid, "code": "NUM_UNSOURCED", "severity": "fail",
                "reason": f"正确选项中的临床数值「{shown}」在源切片（{sid or '未标注'}）"
                          f"中找不到出处——答案与教材数值不一致"})
            continue
        # ② 题干：软核对（warn）——病例数据本就可自行构造
        stem_bad = [(v, u) for v, u in _extract(str(q.get("question") or ""))
                    if v not in allowed and _is_clinical(u)]
        if stem_bad:
            shown = "、".join(f"{v:g}{u}" for v, u in stem_bad[:4])
            issues.append({
                "q_id": cid, "code": "NUM_UNSOURCED", "severity": "warn",
                "reason": f"题干中的临床数值「{shown}」在源切片（{sid or '未标注'}）中未见，"
                          f"如为病例自行设定可忽略；建议核对是否与教材一致"})
    fails = [x for x in issues if x["severity"] == "fail"]
    return {"issues": issues, "checked": checked,
            "fail_count": len(fails), "passed": len(fails) == 0}
