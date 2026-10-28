"""门禁①-C 溯源回查：每题 analysis 必须含 [源:...]，且引用的切片 ID 必须存在。

v0.5：兼容全角括号【源：…】与全角冒号（LLM 实际输出常为【源:切片S001】，
旧实现只认半角括号/冒号 → 溯源全量误报 F2）。

v0.22 阶段 4（G5）：新增 :func:`check_entailment` —— 从「**字符串对齐**」升级到
「**断言级**」：`source_quote` 必须是所引切片里的**原文**。旧实现只看 subtopic 前 2 字
是否出现在切片里，于是「引用对了切片、内容说错了」照样放行。
"""

import re
import unicodedata
from typing import Any, Optional

SRC_PATTERN = re.compile(r"[【\[]源[：:]([^】\]]{1,60})[】\]]")
SID_PATTERN = re.compile(r"切片?(S\d{3})")

# 引文分段：模型可能用省略号跳读，逐段核（每段至少这么长才核，短于它没有判别力）
QUOTE_SEGMENT_MIN = 6
# `…` 单个就够（中文省略号常只写一个）；ASCII 省略号至少要两个点，否则会误切小数点/句点
_SEGMENT_SPLIT = re.compile(r"…+|\.{2,}|；|;")
# 答案锚定：正确选项文本多长才参与「解析里有没有原样依据」的提示（太长的选项是整句，核不了）
ANCHOR_MAX_CHARS = 30


def _norm_for_match(s: str) -> str:
    """只保留「字与数字」：去掉空白与所有标点（中英），用于**包含**判定。

    为什么要归一标点：模型引文常把「，」写成「,」或补一个句号，
    那是排版差异不是编造。归一的是**格式**，判的是**内容**。
    """
    return "".join(ch for ch in str(s or "")
                   if not ch.isspace() and not unicodedata.category(ch).startswith("P"))


def _quote_segments(quote: str) -> list[str]:
    segs = [_norm_for_match(s) for s in _SEGMENT_SPLIT.split(str(quote or ""))]
    return [s for s in segs if len(s) >= QUOTE_SEGMENT_MIN]


def _effective_options(q: dict[str, Any]) -> list[str]:
    """实际选项（B1 组题的共享选项在 `group.options`）。与 `options_check` 同口径。"""
    opts = q.get("options") or []
    if not opts and q.get("group_kind") == "option_group":
        grp = q.get("group") or {}
        if isinstance(grp, dict):
            opts = grp.get("options") or []
    return [str(o) for o in opts if isinstance(o, str)]


def check_entailment(questions: list[dict[str, Any]],
                     slice_texts: Optional[dict[str, str]] = None,
                     strict: bool = False) -> dict[str, Any]:
    """断言级溯源：`source_quote` 必须能在**所引切片**里逐字核到。

    ## 判据（两条，都只看可观测文本）

    1. **引文核实**（`F3`）：归一标点后，`source_quote` 的每一段都必须出现在所引切片里。
       核不到 ⇒ 引文是编的（或引错了切片）。
    2. **答案锚定**（`F4`，`info` 级）：正确选项的原样文本应当出现在
       「引文 + 解析」里；否则提示「答案缺原样依据」——不阻断，只进人工复核。

    ## 为什么默认 `warn` 而不是 `fail`

    提示词要求逐字引用，但 LLM 的「逐字」经常带轻微改写。若默认 `fail`，
    一次 50 题的批量会把整题库剔到空——**这是把可选功能做成致命功能**。
    所以默认 `warn`（进 MedFix 定向修复 + 人工复核清单），`strict=True` 才 `fail`。
    这条取舍写在实施方案 §4「可选降级，不许阻断出题」。

    ## 降级

    一批题里**没有任何** `source_quote`（旧产物 / 未启用提示词）⇒ `available=False`、
    零 issue。**不能因为「没这个字段」就判红**，否则历史项目重跑会全红。
    """
    texts = slice_texts or {}
    quoted = [q for q in questions if str(q.get("source_quote") or "").strip()]
    if not quoted:
        return {"available": False, "checked": 0, "grounded": 0,
                "issues": [], "fail_count": 0, "warn_count": 0, "coverage": 0.0}

    severity = "fail" if strict else "warn"
    issues: list[dict[str, Any]] = []
    grounded = 0
    for i, q in enumerate(questions):
        cid = str(q.get("id") or f"Q{i + 1:03d}")
        quote = str(q.get("source_quote") or "").strip()
        if not quote:
            continue
        segs = _quote_segments(quote)
        if not segs:
            continue
        analysis = str(q.get("analysis") or "")
        sids = [SOURCE_SID(s) for s in SRC_PATTERN.findall(analysis)]
        targets = [str(texts.get(sid) or "") for sid in sids if sid and texts.get(sid)]
        if not targets:
            issues.append({"q_id": cid, "code": "F3", "severity": "info",
                           "reason": "有 source_quote 但没标注切片 ID（或该切片无文本），"
                                     "引文无法核对——已跳过（溯源格式由 F2 判）"})
        else:
            hit = any(all(seg in _norm_for_match(t) for seg in segs) for t in targets)
            if hit:
                grounded += 1
            else:
                issues.append({
                    "q_id": cid, "code": "F3", "severity": severity,
                    "reason": f"引文未能在所引切片（{sids[0]} 等）中核到原文："
                              f"「{quote[:28]}」——疑似编造引文或引错切片"})
        # 答案锚定（只在单选时判；X 型多答案逐项核会放大噪声）
        ans = str(q.get("answer") or "").strip().upper()
        opts = _effective_options(q)
        if len(ans) == 1 and ans in "ABCDEF":
            idx = "ABCDEF".index(ans)
            if idx < len(opts):
                opt = re.sub(r"^[A-E][\.、\s]", "", opts[idx].strip()).strip()
                if 2 <= len(opt) <= ANCHOR_MAX_CHARS:
                    hay = _norm_for_match(quote + analysis)
                    if _norm_for_match(opt) not in hay:
                        issues.append({
                            "q_id": cid, "code": "F4", "severity": "info",
                            "reason": f"正确选项「{opt[:16]}」在引文与解析里都没有原样出现"
                                      f"——请人工确认解析确实支撑该答案"})
    fails = sum(1 for x in issues if x["severity"] == "fail")
    warns = sum(1 for x in issues if x["severity"] == "warn")
    return {"available": True, "checked": len(quoted), "grounded": grounded,
            "issues": issues, "fail_count": fails, "warn_count": warns,
            "coverage": round(grounded / max(len(quoted), 1), 3)}


def _align_hit(sub: str, text: str) -> bool:
    """知识点是否能在被引切片里找到（S2-9）。整串命中优先；否则退化为前 2 字命中。

    ⚠️ 这是**弱判据**：前 2 字命中的误报率很高（「诊断」在任何一页都出现）。
    它保留只为兼容既有 F2 告警；真正的内容核对看 :func:`check_entailment`。
    """
    if not sub or not text:
        return True          # 无 subtopic 可对齐 → 不妄判
    if sub in text:
        return True
    return len(sub) >= 2 and sub[:2] in text


def check_trace(questions: list[dict[str, Any]],
                known_sids: set[str],
                slice_texts: Optional[dict[str, str]] = None) -> dict[str, Any]:
    """溯源校验。

    S2-9（R8+W）：原实现只验「切片 ID 是否存在」——引用了**别的切片**（ID 真实存在但与本题
    知识点无关）时无人发现，溯源形同虚设。传入 `slice_texts`（sid → 切片文本）后，
    额外核对本题 `subtopic` 能否在该切片里找到；找不到 → `warn`（进人工复核，不直接剔除：
    知识点命名与切片表述常有差异，交由 MedFix/人工判断）。
    """
    issues = []
    ok = 0
    for q in questions:
        cid = str(q.get("id") or "")
        analysis = q.get("analysis") or ""
        srcs = SRC_PATTERN.findall(analysis)
        if not srcs:
            issues.append({"q_id": cid, "code": "F2", "severity": "fail",
                           "reason": "analysis 缺少 [源:…] 溯源标注"})
            continue
        ok += 1
        for src in srcs:
            sid = SOURCE_SID(src)
            if sid:
                if sid not in known_sids:
                    issues.append({"q_id": cid, "code": "F2", "severity": "fail",
                                   "reason": f"溯源指向不存在的切片 {sid}（已知：{sorted(known_sids)[:6]}…）"})
                elif slice_texts and not _align_hit(str(q.get("subtopic") or "").strip(),
                                                    str(slice_texts.get(sid) or "")):
                    issues.append({
                        "q_id": cid, "code": "F2", "severity": "warn",
                        "reason": f"溯源切片 {sid} 中找不到本题知识点"
                                  f"「{str(q.get('subtopic') or '')[:16]}」——疑似引用错切片"})
            elif "切片" not in src and not re.search(r"\d+", src):
                issues.append({"q_id": cid, "code": "F2", "severity": "warn",
                               "reason": f"溯源格式建议为 [源:切片SXXX]，当前「{src[:30]}」"})
    return {"issues": issues, "ok_count": ok,
            "fail_count": sum(1 for x in issues if x["severity"] == "fail"),
            "coverage": round(ok / max(len(questions), 1), 3)}


def SOURCE_SID(src: str) -> str:
    m = SID_PATTERN.search(src)
    return m.group(1) if m else ""
