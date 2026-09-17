"""门禁①-C 溯源回查：每题 analysis 必须含 [源:...]，且引用的切片 ID 必须存在。

v0.5：兼容全角括号【源：…】与全角冒号（LLM 实际输出常为【源:切片S001】，
旧实现只认半角括号/冒号 → 溯源全量误报 F2）。
"""

import re
from typing import Any, Optional

SRC_PATTERN = re.compile(r"[【\[]源[：:]([^】\]]{1,60})[】\]]")
SID_PATTERN = re.compile(r"切片?(S\d{3})")


def _align_hit(sub: str, text: str) -> bool:
    """知识点是否能在被引切片里找到（S2-9）。整串命中优先；否则退化为前 2 字命中。"""
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
