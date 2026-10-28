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
# 答案键字母表（与渲染层 qbank_html.LETTERS 同口径，供 R15 越界提示用）
LETTERS = "ABCDEFGHIJ"


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
    # R15 答案键越界（S2-7 / R8+W）：字母必须落在**实际选项范围内**。
    # 原实现只验「非空 + 单字母」，`answer="F"` 而只有 A~E 时全程无人拦——渲染后该题无正确项可勾，
    # 学生按答案键判分永远判错。X 型逐个字母都要在范围内。
    if answer:
        _letters = list(answer)
        for ch in _letters:
            if ord(ch) - ord("A") >= len(opts):
                add("R15", "fail",
                    f"答案键越界：{ch} 超出选项范围（共 {len(opts)} 个选项，仅到 "
                    f"{LETTERS[len(opts) - 1] if opts else '-'}）")
                break
    # R16 重复选项（S2-8 / R8+W）：归一化后内容相同的选项不得同时出现——原无任何门禁拦截，
    # 重复项会让「哪个是正确答案」出现歧义（渲染上看着是两个选项，实际指向同一内容）。
    _seen_opts: dict[str, int] = {}
    for _i, _o in enumerate(opts):
        _key = re.sub(r"\s+", "", _o).lower()
        if not _key:
            continue
        if _key in _seen_opts:
            add("R16", "fail",
                f"选项重复：第 {_seen_opts[_key] + 1} 项与第 {_i + 1} 项内容相同（{_o[:24]}）")
            break
        _seen_opts[_key] = _i
    return issues


def check_rationale_coverage(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """**逐选项理由**完整性（v0.22 阶段 4 / G4）。

    ## 判据是**批次级 opt-in**，不是全局强制

    只要本批里有**任何一题**带了 `option_rationales`，就认为这批是新契约产物，
    于是**每一题**的**每一个选项**都必须有理由（缺 ⇒ `fail`，进 MedFix / 人工复核）。
    反过来，整批都没有这个字段 ⇒ 这是旧产物或旧提示词，**一律不判**。

    为什么不做全局强制：历史项目与既有测试里的题都没有这个字段，
    一刀切会让「重跑旧项目」全红——那是把可选升级做成破坏性变更。
    """
    if not any(q.get("option_rationales") for q in questions):
        return []
    issues: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        cid = str(q.get("id") or f"Q{i + 1:03d}")
        opts = _effective_options(q)
        if not opts:
            continue
        letters = list(LETTERS[:len(opts)])
        have = q.get("option_rationales") or {}
        have = have if isinstance(have, dict) else {}
        missing = [L for L in letters if not str(have.get(L) or "").strip()]
        if missing:
            issues.append({
                "q_id": cid, "code": "R20", "severity": "fail",
                "reason": f"逐选项理由缺失：{'/'.join(missing)}"
                          f"（每个选项都要说明为什么对/为什么错）"})
        extra = [k for k in have if k not in letters]
        if extra:
            issues.append({
                "q_id": cid, "code": "R20", "severity": "info",
                "reason": f"逐选项理由存在超出本题选项的键：{'/'.join(sorted(extra))}"
                          f"（本题只有 {'/'.join(letters)}）"})
    return issues


def check_option_length_clue(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """**正确选项是不是明显最长**（经典命题缺陷：长度泄露答案）。

    只在「正确选项长度 ≥ 12 字且 > 其余选项长度的 1.6 倍」时告警——短选项
    （「肺炎链球菌」）天然长短不一，卡它会制造噪声。`warn` 级：能改就改，改不了不剔除。
    """
    issues: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        opts = [_strip_prefix(o) for o in _effective_options(q)]
        ans = str(q.get("answer") or "").strip().upper()
        if len(ans) != 1 or ans not in LETTERS or len(opts) < 3:
            continue
        idx = LETTERS.index(ans)
        if idx >= len(opts):
            continue
        mine = len(opts[idx])
        others = [len(o) for j, o in enumerate(opts) if j != idx]
        others.sort()
        mid = others[len(others) // 2] or 0
        if mine >= 12 and mid and mine > 1.6 * mid:
            issues.append({
                "q_id": str(q.get("id") or f"Q{i + 1:03d}"), "code": "R21",
                "severity": "warn",
                "reason": f"正确选项（{mine} 字）明显长于其余选项（中位 {mid} 字）"
                          f"——长度泄露答案，建议改写为等长结构"})
    return issues


def check_answer_distribution(questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """**答案位置分布**（批次级）：某个字母占比过高 ⇒ 猜题有便宜可占。

    样本 < 10 题不判（小样本必然偏）。`warn` 级 + 非定向哨兵 `q_id="ANSWER_DIST"`：
    它是**整批**属性，MedFix 按题号找不到原题，只能提醒人工/下一次生成调整。
    """
    answers = [str(q.get("answer") or "").strip().upper() for q in questions]
    answers = [a for a in answers if len(a) == 1 and a in LETTERS]
    if len(answers) < 10:
        return []
    counts = {L: answers.count(L) for L in LETTERS[:6]}
    top_letter, top_n = max(counts.items(), key=lambda kv: kv[1])
    share = top_n / len(answers)
    if share < 0.5:
        return []
    dist = "、".join(f"{k} {v / len(answers):.0%}" for k, v in counts.items() if v)
    return [{"q_id": "ANSWER_DIST", "code": "R22", "severity": "warn",
             "reason": f"答案位置分布失衡：{dist}（{top_letter} 占 {share:.0%}，"
                       f"共 {len(answers)} 题）——建议打散正确答案位置"}]


def check_all(questions: list[dict[str, Any]]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    for i, q in enumerate(questions):
        issues.extend(check_question(q, f"Q{i + 1:03d}"))
    # v0.22 阶段 4：批次级质量项（逐选项理由 / 长度线索 / 答案分布）
    issues.extend(check_rationale_coverage(questions))
    issues.extend(check_option_length_clue(questions))
    issues.extend(check_answer_distribution(questions))
    fails = [x for x in issues if x["severity"] == "fail"]
    return {"issues": issues, "fail_count": len(fails), "passed": len(fails) == 0}
