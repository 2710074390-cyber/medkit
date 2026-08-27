"""门禁①-A 选项质量校验（移植自 MedAgentWork validate_options 的 R 规则子集）。

每一违规给 {q_id, code(规则号), severity(fail|warn), reason}。
"""

import re
from typing import Any

ABSOLUTE_WORDS = ["总是", "从不", "所有", "必须", "完全", "绝对", "唯一", "全部", "以上都对", "以上都不对"]
MEANINGLESS_SUFFIX = re.compile(r"[（(](?:相关表现|相关类型|相关症状|同上文|见上文|参见上文|排除以上)[）)]")
TAIL_PARTICLES = ("的", "和", "与", "或", "于", "并", "且", "而", "及", "等", "则", "即")
NUMBER_NO_UNIT = re.compile(
    r"[0-9]+(?:\.[0-9]+)?(?![%℃a-zA-Z年岁天小时分钟秒kg毫克毫升/])")
OPTION_PREFIX = re.compile(r"^[A-E][\.、\s]")

EXPECT_OPTION_COUNT = {"A1": 5, "A2": 5, "X": 5, "B1": 5, "A3": 5, "A4": 5}
ALLOWED_BLOOM = {"记忆", "理解", "应用", "创造"}
ALLOWED_TYPES = {"A1", "A2", "X", "B1", "A3", "A4"}  # S3：案例题 A3/A4 纳入门禁


def _strip_prefix(opt: str) -> str:
    return OPTION_PREFIX.sub("", opt.strip()).strip()


def _effective_options(q: dict[str, Any]) -> list[str]:
    """实际渲染/校验的选项：B1 组题共享选项在 group 字段（S3：自身 options 可为空）。"""
    opts = q.get("options") or []
    if not opts and q.get("group_kind") == "option_group":
        grp = q.get("group") or {}
        if isinstance(grp, dict):
            opts = grp.get("options") or []
    return [o for o in opts if isinstance(o, str)]


def check_question(q: dict[str, Any], idx: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    cid = str(q.get("id") or idx)
    opts_raw = _effective_options(q)
    opts = [_strip_prefix(o) for o in opts_raw]

    def add(code: str, severity: str, reason: str) -> None:
        issues.append({"q_id": cid, "code": code, "severity": severity, "reason": reason})

    # R14 选项数超渲染上限（>6 → 触发 MedFix 改写；渲染前终检兜底剔除）
    if len(opts) > 6:
        add("R14", "fail", f"选项数 {len(opts)} > 6（渲染上限）")
    # R1 选项数
    expect = EXPECT_OPTION_COUNT.get(q.get("type", "A1"), 5)
    if len(opts) != expect:
        add("R1", "fail", f"选项数 {len(opts)} ≠ {expect}（{q.get('type')}型）")
        return issues
    # HC-7 禁用绝对化用语 / 以上都对
    for o in opts:
        for w in ABSOLUTE_WORDS:
            if w in o:
                add("R7", "fail", f"选项含绝对化/兜底用语「{w}」：{o[:24]}")
                break
    # R12 无意义括号后缀
    for o in opts:
        if MEANINGLESS_SUFFIX.search(o):
            add("R12", "fail", f"选项含无意义括号后缀：{o[:30]}")
            break
    # R7/R8 截断与半截选项
    for o in opts:
        if ".." in o or ". ." in o:
            add("R7", "fail", f"选项含双点截断残留：{o[:30]}")
            break
        if o.endswith(TAIL_PARTICLES) and not o.endswith(("剂", "病", "症", "征", "型", "法", "症群")):
            add("R8", "fail", f"选项以连接词/助词结尾（疑似截断）：{o[:30]}")
            break
        if len(o) <= 2:
            add("R7", "fail", f"选项长度过短（{len(o)}字，疑似残片）：{o[:30]}")
            break
    # R13 长度上限（防过度加长）
    lens = [len(o) for o in opts]
    if lens and max(lens) > 20:
        add("R13", "fail", f"单选项超 20 字（最长 {max(lens)} 字）：{opts[lens.index(max(lens))][:30]}…")
    if lens and sum(lens) / len(lens) > 18:
        add("R13", "warn", f"选项平均长度 {sum(lens) / len(lens):.1f} > 18 字（偏长）")
    # R2 长度失衡（>1.5x）
    if len(opts) >= 2 and lens:
        long, short = max(lens), min(lens)
        if short > 0 and long / short > 1.5:
            add("R2", "warn", f"选项长度失衡（{long} vs {short}，>1.5x）")
    # R9 数值缺单位（临床参数）
    for o in opts:
        if NUMBER_NO_UNIT.search(o) and not re.search(r"[0-9]+(?:\.\d+)?[-~～]?\s*[0-9]*\s*[%℃年岁天小时分钟秒kg毫克ml/]", o):
            add("R9", "warn", f"数值疑似缺单位：{o[:30]}")
            break
    # 答案键存在性（D19：归一化口径与渲染层 answersEqual 统一——空白/半全角逗号/顿号）
    answer = re.sub(r"[\s,，、]+", "", str(q.get("answer", ""))).upper()
    if not answer:
        add("R0", "fail", "缺少答案键")
    elif q.get("type") != "X" and len(answer) != 1:
        add("R0", "fail", f"单选/病例题答案应为单字母，当前「{answer}」")
    # Bloom / type 合法性
    if q.get("bloom") not in ALLOWED_BLOOM:
        add("D16", "fail", f"bloom 标注非法：「{q.get('bloom')}」（应为记忆/理解/应用/创造）")
    if q.get("type") not in ALLOWED_TYPES:
        add("R0", "fail", f"题型非法：「{q.get('type')}」")
    return issues


def check_all(questions: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        issues.extend(check_question(q, f"Q{i + 1:03d}"))
    fails = [x for x in issues if x["severity"] == "fail"]
    return {"issues": issues, "fail_count": len(fails), "passed": len(fails) == 0}
