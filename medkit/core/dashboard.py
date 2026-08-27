"""学习闭环驾驶舱聚合：掌握度 + 复习(SM-2) + 提问式学习(MedTutor) 三闭环总览。

纯本地聚合，只读不写；按科目可选过滤。供 GET /api/library/dashboard 返回。
"""

from typing import Any

from . import explain as expl
from . import library as lib
from . import review as rev
from . import tutor as tut


def _subject_kps(subject: str) -> list[dict[str, Any]]:
    kps = lib.get_mastery_view()["knowledge"]
    if not subject:
        return kps
    return [k for k in kps if k.get("subject") == subject]


def summary(subject: str = "") -> dict[str, Any]:
    subject = (subject or "").strip()

    # 掌握度（subject 过滤后重算状态与统计）
    kps = _subject_kps(subject)
    state_names = ("weak", "shaky", "solid", "mastered")
    miss_total = sum(1 for k in kps if k.get("miss", 0) > 0)
    misuse = sum(k.get("miss", 0) for k in kps)
    mastery = {
        "total_knowledge": len(kps),
        **{s: sum(1 for k in kps if k.get("state") == s) for s in state_names},
        "total_mistakes": sum(1 for m in lib.list_mistakes()
                              if not subject or m.get("subject") == subject),
        "miss_kps": miss_total,
        "miss_count": misuse,
    }
    total = mastery["total_knowledge"] or 0
    # 掌握率口径 = (较熟练 + 已掌握) / 全部（与前端 dashDonut 中心文字一致）
    mastery["mastered_rate"] = round(100 * (mastery["solid"] + mastery["mastered"]) / total) if total else 0

    # 复习（SM-2）
    review = dict(rev.stats(subject))
    review["done"] = review["total"] - review["due"]

    # 提问式学习（MedTutor）
    sessions = tut.list_sessions(subject)
    by_state = {s: 0 for s in state_names}
    answered_rounds = 0
    for s in sessions:
        st = s.get("state")
        by_state[st] = by_state.get(st, 0) + 1
        answered_rounds += len(s.get("rounds") or [])
    tutor = {
        "total": len(sessions),
        "in_progress": sum(1 for s in sessions if s.get("state") != "mastered"),
        "answered_rounds": answered_rounds,
        "by_state": by_state,
    }

    # 闭环流转各环节在册数量（错题沉淀 → 讲解 → 提问 → 复习 → 掌握）
    loop = {
        "mistakes": mastery["total_mistakes"],
        "explains": len(expl.list_explains(subject)),
        "tutor": len(sessions),
        "review": review["total"],
        "mastered": mastery["mastered"],
    }

    return {
        "subject": subject,
        "subject_label": subject or "全部科目",
        "mastery": mastery,
        "review": review,
        "tutor": tutor,
        "loop": loop,
    }
