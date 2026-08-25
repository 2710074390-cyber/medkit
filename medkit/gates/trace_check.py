"""门禁①-C 溯源回查：每题 analysis 必须含 [源:...]，且引用的切片 ID 必须存在。"""

import re
from typing import Any

SRC_PATTERN = re.compile(r"\[源:([^\]]{1,60})\]")
SID_PATTERN = re.compile(r"切片?(S\d{3})")


def check_trace(questions: list[dict[str, Any]],
                known_sids: set[str]) -> dict[str, Any]:
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
            elif "切片" not in src and not re.search(r"\d+", src):
                issues.append({"q_id": cid, "code": "F2", "severity": "warn",
                               "reason": f"溯源格式建议为 [源:切片SXXX]，当前「{src[:30]}」"})
    return {"issues": issues, "ok_count": ok,
            "fail_count": sum(1 for x in issues if x["severity"] == "fail"),
            "coverage": round(ok / max(len(questions), 1), 3)}


def SOURCE_SID(src: str) -> str:
    m = SID_PATTERN.search(src)
    return m.group(1) if m else ""
