"""复习调度（M5）：轻量 SM-2 复习队列，纯本地算法、不调 LLM。

存储：~/.medkit/library/review_queue.json（跨项目个人资产）。
参考设计文档 v0.7 §2.3 / §4.6：卡片 state ∈ new|learning|review|relearning，
历史记在 review_log（[{"t":iso,"quality":0..5}]）。做对间隔翻倍、做错重学，数据持久跨重启。

简化 SM-2 规则（与 Anki 精神一致，但零依赖、几行可测）：
- quality≥3 视为「记得」：reps+1，间隔按 1 → 6 → round(prev*ease) 增长，ease 增；否则按失败回退。
- quality<3 视为「忘了」：间隔归 0 → relearning，lapses+1，ease−0.2（下限 1.3）。
"""  # noqa: E501

import itertools
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from typing import Any, Iterator, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import read_json_list, write_json_atomic

LIBRARY_DIR = cfg.CONFIG_DIR / "library"
REVIEW_QUEUE_FILE = LIBRARY_DIR / "review_queue.json"

# SQL 模式（S0·方案 §2.3）：medkit.db 存在即行级事务（BEGIN IMMEDIATE 串行读-改-写）。
DB_FILE = dbs.DB_PATH
_LOCK = threading.RLock()
_R_COLS = ("subject", "kp_name", "state", "due", "created_at")
_SEQ = itertools.count()

# SM-2 参数
INIT_EASE = 2.5
MIN_EASE = 1.3
GRADE_PASS = 3          # quality≥3 记「记得」
INTERVAL_FIRST = 1      # 第一次记住 → 1 天
INTERVAL_SECOND = 6     # 第二次记住 → 6 天

CARD_STATES = ["new", "learning", "review", "relearning"]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> date:
    return date.today()


def _store_is_sql() -> bool:
    return DB_FILE.exists()


def _load() -> list[dict[str, Any]]:
    """读复习队列；缺失/损坏 → 空（统一容错）。SQL 模式读表，JSON 模式读文件。"""
    if _store_is_sql():
        conn = dbs.get_conn()
        cur = conn.cursor()
        try:
            return dbs.list_rows(cur, "review_cards")
        finally:
            cur.close()
    return read_json_list(REVIEW_QUEUE_FILE)


def _save(records: list[dict[str, Any]]) -> None:
    """写复习队列。SQL 模式事务整组替换；JSON 模式复用 fsutil 原子写。"""
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            dbs.replace_all(cur, "review_cards", records, _R_COLS)
        return
    write_json_atomic(REVIEW_QUEUE_FILE, records)


@contextmanager
def _store() -> Iterator[dict[str, Any]]:
    """队列读-改-写视图；退出时按 dirty 写回（SQL 单事务 / JSON RLock+原子写）。"""
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            st: dict[str, Any] = {"cards": dbs.list_rows(cur, "review_cards"),
                                  "cur": cur, "dirty": False}
            yield st
            if st["dirty"]:
                dbs.replace_all(cur, "review_cards", st["cards"], _R_COLS)
        return
    with _LOCK:
        st = {"cards": list(_load()), "cur": None, "dirty": False}
        yield st
        if st["dirty"]:
            _save(st["cards"])


# ---------------------------------------------------------------- 纯逻辑（可测/可解释）
def next_intervals(ease: float, reps: int, interval: int) -> tuple[int, int]:
    """SM-2 间隔推进：返回 (new_interval, new_reps)。连记两次后按前隔×ease 增长。"""
    reps += 1
    if reps == 1:
        return INTERVAL_FIRST, reps
    if reps == 2:
        return INTERVAL_SECOND, reps
    return max(int(round(interval * ease)), interval + 1), reps


def update_ease(ease: float, quality: int, passed: bool) -> float:
    """SM-2 记忆难度更新。passed→小幅增减；failed→固定 −0.2。"""
    if passed:
        e = ease + (0.1 - (5 - max(0, min(quality, 5))) * (0.08 + (5 - quality) * 0.02))
    else:
        e = ease - 0.2
    return max(MIN_EASE, round(e, 2))


def grade_card(card: dict[str, Any], quality: int) -> dict[str, Any]:
    """按 quality(0~5) 更新一张卡片的间隔/ease/state，返回更新后的卡片（改的是 dict 副本）。"""
    q = max(0, min(int(quality), 5))
    passed = q >= GRADE_PASS
    ease = update_ease(float(card.get("ease", INIT_EASE)), q, passed)
    interval, reps = next_intervals(ease, int(card.get("reps", 0)),
                                    int(card.get("interval", 0)))
    if passed:
        state = "review" if reps >= 2 else "learning"
    else:
        interval, reps = 0, 0
        state = "relearning"
        card["lapses"] = int(card.get("lapses", 0)) + 1
    card["ease"] = ease
    card["interval"] = interval
    card["reps"] = reps
    card["state"] = state
    card["due"] = (_today() + timedelta(days=interval)).isoformat()
    log = card.get("review_log") or []
    log.append({"t": _now(), "quality": q})
    card["review_log"] = log[-200:]
    card["updated_at"] = _now()
    return card


# ---------------------------------------------------------------- 队列 CRUD
def enqueue(kp_name: str, subject: str = "",
            kp_id: str = "", interval_hint: int = 0) -> dict[str, Any]:
    """把一个知识点卡片入队。已存在（同 kp_id 或同 kp_name）则直接返回既有卡，不入重复。"""
    with _store() as st:
        cards = st["cards"]
        for c in cards:
            if (kp_id and c.get("kp_id") == kp_id) or \
                    (not kp_id and c.get("kp_name") == kp_name):
                return c
        cid = f"rev_{int(time.time() * 1000)}_{next(_SEQ)}"   # 时间戳+序号：同毫秒/并发不撞 id
        card = {
            "id": cid, "kp_id": kp_id or "", "kp_name": kp_name, "subject": subject,
            "state": "new", "ease": INIT_EASE, "interval": interval_hint,
            "due": _today().isoformat(), "reps": 0, "lapses": 0,
            "review_log": [], "created_at": _now(), "updated_at": _now(),
        }
        cards.append(card)
        st["dirty"] = True
    return card


def enqueue_knowledge(subject: str = "") -> list[dict[str, Any]]:
    """把「掌握度低于 solid」的知识点全部入队（复习计划批量铺卡）。返回本次新增卡。"""
    from . import library as lib
    added = []
    for k in lib.list_knowledge():
        if k.get("subject") and subject and k.get("subject") not in (subject, "未分类"):
            continue
        if k.get("state") in ("solid", "mastered"):
            continue
        before = len(_load())
        enqueue(k.get("name") or "", k.get("subject") or "", k.get("id") or "")
        if len(_load()) > before:
            added.append(k)
    return added


def get_card(cid: str) -> Optional[dict[str, Any]]:
    return next((c for c in _load() if c.get("id") == cid), None)


def list_cards(subject: str = "") -> list[dict[str, Any]]:
    cards = _load()
    if subject:
        cards = [c for c in cards if c.get("subject") == subject
                 or c.get("subject") == ""]
    cards.sort(key=lambda c: (c.get("due") or ""))
    return cards


def today_cards(subject: str = "") -> list[dict[str, Any]]:
    today = _today().isoformat()
    return [c for c in list_cards(subject) if (c.get("due") or "") <= today]


def grade(cid: str, quality: int) -> Optional[dict[str, Any]]:
    """写回一次复习结果：SM-2 推进 → 落盘 → 回写知识点 last_reviewed。"""
    with _store() as st:
        cards = st["cards"]
        idx = next((i for i, c in enumerate(cards) if c.get("id") == cid), None)
        if idx is None:
            return None
        cards[idx] = grade_card(cards[idx], quality)
        st["dirty"] = True
        card = cards[idx]
    try:
        from . import library as lib
        lib.log_knowledge_event(card.get("kp_name") or "",
                                "review",
                                note=f"{card.get('subject', '')} / q={int(quality)} / 下次 {card.get('due')}")
    except Exception:  # noqa: BLE001  知识点不存在/写盘失败不阻塞复习
        pass
    return card


def delete_card(cid: str) -> bool:
    with _store() as st:
        cards = st["cards"]
        remains = [c for c in cards if c.get("id") != cid]
        if len(remains) == len(cards):
            return False
        st["cards"] = remains
        st["dirty"] = True
    return True


def stats(subject: str = "") -> dict[str, int]:
    cards = list_cards(subject)
    today = _today().isoformat()
    return {
        "total": len(cards),
        "new": sum(1 for c in cards if c.get("state") == "new"),
        "due": sum(1 for c in cards if (c.get("due") or "") <= today),
        "in_progress": sum(1 for c in cards if c.get("state") in ("learning", "relearning")),
        "review": sum(1 for c in cards if c.get("state") == "review"),
    }
