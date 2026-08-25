"""门禁①-B Bloom 分布校验（目标默认 30/40/25/5，偏差 >15% → fail）。

可玩性 2B：target 参数化（用户自定义配比；默认值不变 → 原有行为零回归）。
"""

from collections import Counter
from typing import Any, Optional

DEFAULT_TARGET = {"记忆": 0.30, "理解": 0.40, "应用": 0.25, "创造": 0.05}
DEVIATION_LIMIT = 0.15


def check_bloom(questions: list[dict[str, Any]],
                target: Optional[dict[str, float]] = None) -> dict[str, Any]:
    target = target or DEFAULT_TARGET
    n = max(len(questions), 1)
    counter = Counter(q.get("bloom") or "未知" for q in questions)
    dist = {k: round(counter.get(k, 0) / n, 3) for k in target}
    issues = []
    for level, tgt in target.items():
        dev = abs(dist.get(level, 0) - tgt)
        if dev > DEVIATION_LIMIT:
            issues.append({
                "q_id": "BLOOM", "code": "D16", "severity": "fail",
                "reason": f"Bloom[{level}] 实际 {dist.get(level, 0):.0%} vs 目标 {tgt:.0%}，"
                          f"偏差 {dev:.0%} > 15%"})
        elif dev > 0.08:
            issues.append({
                "q_id": "BLOOM", "code": "D16", "severity": "warn",
                "reason": f"Bloom[{level}] 实际 {dist.get(level, 0):.0%} vs 目标 {tgt:.0%}"})
    return {"distribution": dist, "issues": issues,
            "fail_count": sum(1 for x in issues if x["severity"] == "fail")}
