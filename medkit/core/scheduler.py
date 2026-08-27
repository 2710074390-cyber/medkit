"""记忆卡调度（WP-05/NX-04）：Scheduler 协议 + py-fsrs 默认 / SM-2 legacy 可切。

设计（与执行方案 §4 WP-05 对齐）：
- **协议**：``grade(card, quality, now) -> card``，quality 0~5（SM-2 口径，与既有复习一致）；
  cards 为「调度无关」的 dict 载体（ease/interval/reps/state/due/review_log……）。
- **FSRS（默认）**：py-fsrs 6.3.2（K2 已验证），``enable_fuzzing=False`` 保证可测可解释；
  quality → Rating 映射：0/1=Again、2=Hard、3/4=Good、5=Easy；卡片按 ``sched='fsrs'`` 绑定。
- **SM-2 legacy**：复用 :mod:`medkit.core.review` 的零依赖实现（``sched='sm2'``）。
- **切换语义**：算法按「创建时」绑定到卡片（``card['sched']``）；切换配置只影响新卡，
  既有队列携带原算法字段继续排程——队列不丢、可回滚。

卡片 dict 结构（由 core/cards.py 维护，本模块只做排程推进）::

    {id, subject, kp_name, kind, front, back, source, sched, state, due,
     ease, interval, reps, lapses, sched_data(dict), review_log, ...}
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

DEFAULT_SCHED = "fsrs"          # NX-04：FSRS 为默认调度
SCHEDS = ("fsrs", "sm2")

# 质量分（0~5，SM-2 口径）→ FSRS Rating
RATING_BY_QUALITY: dict[int, Any] = {}


def _load_ratings() -> None:
    global RATING_BY_QUALITY
    if RATING_BY_QUALITY:
        return
    from fsrs import Rating

    RATING_BY_QUALITY = {
        0: Rating.Again, 1: Rating.Again, 2: Rating.Hard,
        3: Rating.Good, 4: Rating.Good, 5: Rating.Easy,
    }


class CardScheduler(Protocol):
    """记忆卡排程协议：单次自评后推进卡片调度状态。"""

    name: str

    def grade(self, card: dict[str, Any], quality: int,
              now: datetime = None) -> dict[str, Any]:  # pragma: no cover - 协议
        ...


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    try:
        v = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return _utcnow()
    return v if v.tzinfo else v.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------- FSRS（默认）
class FsrsScheduler:
    """py-fsrs 调度（默认）。卡片调度字段存于 card['sched_data']（fsrs.Card 可序列化视图）。"""

    name = "fsrs"

    def __init__(self, enable_fuzzing: bool = False) -> None:
        from fsrs import Scheduler

        self._sched = Scheduler(enable_fuzzing=enable_fuzzing)

    @staticmethod
    def _to_data(c: Any) -> dict[str, Any]:
        return {
            "card_id": getattr(c, "card_id", None),
            "state": getattr(c, "state", None).name
            if getattr(c, "state", None) is not None else "Learning",
            "step": getattr(c, "step", 0),
            "stability": getattr(c, "stability", None),
            "difficulty": getattr(c, "difficulty", None),
            "due": getattr(c, "due", None).isoformat() if getattr(c, "due", None) else None,
            "last_review": (getattr(c, "last_review", None).isoformat()
                            if getattr(c, "last_review", None) else None),
        }

    @staticmethod
    def _from_data(d: dict[str, Any]) -> Any:
        from fsrs import Card, State

        state = State[d.get("state", "Learning")] if d.get("state") else State.Learning
        return Card(
            card_id=d.get("card_id"),
            state=state,
            step=int(d.get("step") or 0),
            stability=d.get("stability"),
            difficulty=d.get("difficulty"),
            due=_parse_dt(d.get("due", "")) if d.get("due") else None,
            last_review=_parse_dt(d["last_review"]) if d.get("last_review") else None,
        )

    def grade(self, card: dict[str, Any], quality: int,
              now: datetime = None) -> dict[str, Any]:
        _load_ratings()
        now = now or _utcnow()
        q = max(0, min(int(quality), 5))
        rating = RATING_BY_QUALITY[q]
        fsrs_card = self._from_data(card.get("sched_data") or {})
        new_card, _log = self._sched.review_card(fsrs_card, rating, now)
        data = self._to_data(new_card)
        card["sched_data"] = data
        # 面向展示/统计的映射字段（state 名称与既有 SM-2 一致）
        card["state"] = data["state"].lower()
        card["due"] = data["due"][:19] if data["due"] else now.isoformat()
        card["ease"] = round(float(data["difficulty"] or 0.0), 2)
        try:
            card["interval"] = max(0, (datetime.fromisoformat(card["due"]) - now).days)
        except (TypeError, ValueError):
            card["interval"] = 0
        card["reps"] = len(card.get("review_log") or []) + 1
        if data["state"] == "Relearning" or rating.name == "Again":
            card["lapses"] = int(card.get("lapses") or 0) + 1
        else:
            card["lapses"] = int(card.get("lapses") or 0)
        card["last_reviewed"] = (data["last_review"][:19] if data["last_review"]
                                 else now.isoformat())
        card["updated_at"] = now.isoformat(timespec="seconds")
        return card


# ---------------------------------------------------------------- SM-2 legacy
class Sm2Scheduler:
    """SM-2 legacy（复用 core/review 的零依赖实现；仅因 NX-04 需要协议统一而做薄封装）。"""

    name = "sm2"

    def grade(self, card: dict[str, Any], quality: int,
              now: datetime = None) -> dict[str, Any]:
        from . import review as rev

        if now is not None:
            # review.grade_card 以真实日期推进；测试冻结时钟由 freezegun 负责
            pass
        return rev.grade_card(card, quality)


# ---------------------------------------------------------------- 工厂
def make_scheduler(name: str = DEFAULT_SCHED) -> CardScheduler:
    name = (name or DEFAULT_SCHED).strip().lower()
    if name == "sm2":
        return Sm2Scheduler()
    return FsrsScheduler()
