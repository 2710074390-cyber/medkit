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


def _contract_warnings(subject: str = "") -> dict[str, Any]:
    """NX-03（R-2）：项目 meta 契约告警计数聚合（medgen 软校验计数落 meta）。

    读取各项目 meta.json 的 ``contract_warnings``（最近一轮生成计数），按科目可选过滤；
    项目目录不存在/未生成过 → 全 0（概览卡仅 count>0 时显示提示）。
    """
    import json as _json

    total = 0
    by_subject: dict[str, int] = {}
    root = expl._proj_root()
    if root.is_dir():
        for proj in root.iterdir():
            mp = proj / "meta.json"
            if not mp.is_file():
                continue
            try:
                meta = _json.loads(mp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            n = int(meta.get("contract_warnings") or 0)
            if n <= 0:
                continue
            s = (meta.get("subject") or "").strip() or "未分类"
            if subject and s != subject:
                continue
            total += n
            by_subject[s] = by_subject.get(s, 0) + n
    return {"total": total, "by_subject": by_subject}


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
        # NX-03（R-2）：契约告警计数（生成输出软校验；0 表示最近一轮无告警或未生成过）
        "contract_warnings": _contract_warnings(subject),
    }
