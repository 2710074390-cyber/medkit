"""医学记忆卡工厂产物存储（WP-05/NX-04）：讲解产物 → 3~6 张记忆卡 → 复习队列。

与 :mod:`medkit.core.review` 的分工：review 管「知识点卡」（SM-2），本模块管「医学记忆卡」
（value/mnemonic/contrast/concept 四型，FSRS 默认 / SM-2 可切，创建时绑定算法）。

存储：~/.medkit/library/memory_cards.json（JSON 模式）或 medkit.db `cards` 表（SQL 模式，迁移 v5），
沿用 review.py 的「SQL 事务 / JSON RLock + 原子写」双态模式。
"""

from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Iterator, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import read_json_list, write_json_atomic
from .scheduler import DEFAULT_SCHED, make_scheduler

LIBRARY_DIR = cfg.CONFIG_DIR / "library"
CARDS_FILE = LIBRARY_DIR / "memory_cards.json"

# SQL 模式（S0·方案 §2.3）：medkit.db 存在即行级事务（BEGIN IMMEDIATE 串行读-改-写）。
DB_FILE = dbs.DB_PATH
_LOCK = threading.RLock()
_C_COLS = ("subject", "source", "kind", "state", "due", "created_at")
_SEQ = itertools.count()

MAX_CARDS_PER_EXPLAIN = 6   # 一次讲解最多生成 6 张（与 medcards.md「3~6 张」提示词口径一致；契约上限 10 兜底）


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _store_is_sql() -> bool:
    return DB_FILE.exists()


def _ensure_schema() -> None:
    """NX-04：旧库（<v5，无 cards 表）按需迁移——与应用内既有按需迁移惯例一致。

    仅 SQL 模式且 cards 表缺失时触发（幂等）；JSON 模式（未建库）零开销。
    """
    if not _store_is_sql():
        return
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        dbs.list_rows(cur, "cards")
    except Exception:  # noqa: BLE001  表缺失 → 升级到 v5 后再由调用方读取
        dbs.migrate()
    finally:
        cur.close()


def _load() -> list[dict[str, Any]]:
    """读记忆卡列表；缺失/损坏 → 空（统一容错）。SQL 模式读表，JSON 模式读文件。"""
    if _store_is_sql():
        conn = dbs.get_conn()
        cur = conn.cursor()
        try:
            return dbs.list_rows(cur, "cards")
        finally:
            cur.close()
    return read_json_list(CARDS_FILE)


def _save(records: list[dict[str, Any]]) -> None:
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            dbs.replace_all(cur, "cards", records, _C_COLS)
        return
    write_json_atomic(CARDS_FILE, records)


@contextmanager
def _store() -> Iterator[dict[str, Any]]:
    """记忆卡读-改-写视图；退出时按 dirty 写回（SQL 单事务 / JSON RLock+原子写）。"""
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            st: dict[str, Any] = {"cards": dbs.list_rows(cur, "cards"),
                                  "cur": cur, "dirty": False}
            yield st
            if st["dirty"]:
                dbs.replace_all(cur, "cards", st["cards"], _C_COLS)
        return
    with _LOCK:
        st = {"cards": list(_load()), "cur": None, "dirty": False}
        yield st
        if st["dirty"]:
            _save(st["cards"])


# ---------------------------------------------------------------- CRUD
def create_from_drafts(drafts: list[dict[str, Any]], subject: str, kp_name: str,
                       source: str, sched: str = DEFAULT_SCHED) -> list[dict[str, Any]]:
    """讲解产物 → 记忆卡入库（幂等：同 source+kp_name+front 已存在则跳过）。

    ``source`` 为生成来源（讲解产物 id）；``sched`` 创建时绑定调度算法（切换只影响新卡）。
    """
    from .schema import CARD_KIND_LABELS, CardDraft

    _ensure_schema()
    sched = sched if sched in ("fsrs", "sm2") else DEFAULT_SCHED
    added: list[dict[str, Any]] = []
    with _store() as st:
        existing = {(c.get("source"), c.get("kp_name"), c.get("front"))
                    for c in st["cards"]}
        for d in drafts[:MAX_CARDS_PER_EXPLAIN]:
            try:
                draft = CardDraft.model_validate(d)
            except Exception:  # noqa: BLE001  契约不通过的草稿不与入库（调用方已用 CardDrafts 校验）
                continue
            key = (source, kp_name, draft.front)
            if key in existing:
                continue
            cid = f"mcm_{int(time.time() * 1000)}_{next(_SEQ)}"
            card = {
                "id": cid, "subject": subject, "kp_name": kp_name, "kind": draft.kind,
                "kind_label": CARD_KIND_LABELS.get(draft.kind, draft.kind),
                "front": draft.front, "back": draft.back, "source": source,
                "sched": sched,
                # 调度字段（fsrs/sm2 公共视图；算法细节在 sched_data）
                "state": "new", "due": _today(), "ease": 0.0, "interval": 0,
                "reps": 0, "lapses": 0, "sched_data": None,
                "review_log": [], "created_at": _now(), "updated_at": _now(),
            }
            st["cards"].append(card)
            st["dirty"] = True
            added.append(card)
            existing.add(key)
    return added


def list_cards(subject: str = "", due_only: bool = False) -> list[dict[str, Any]]:
    _ensure_schema()
    cards = _load()
    if subject:
        cards = [c for c in cards if c.get("subject") == subject or c.get("subject") == ""]
    if due_only:
        today = _today()
        cards = [c for c in cards if (str(c.get("due") or "")[:10]) <= today]
    cards.sort(key=lambda c: (str(c.get("due") or ""), str(c.get("created_at") or "")))
    return cards


def get_card(cid: str) -> Optional[dict[str, Any]]:
    return next((c for c in _load() if c.get("id") == cid), None)


def stats(subject: str = "") -> dict[str, int]:
    cards = list_cards(subject)
    today = _today()
    return {
        "total": len(cards),
        "new": sum(1 for c in cards if c.get("state") == "new"),
        "due": sum(1 for c in cards if (str(c.get("due") or "")[:10]) <= today),
        "review": sum(1 for c in cards if c.get("state") in ("review", "learning")),
        "relearning": sum(1 for c in cards if c.get("state") == "relearning"),
    }


def grade_card(cid: str, quality: int) -> Optional[dict[str, Any]]:
    """按 quality(0~5) 推进一张记忆卡（算法 = 卡片创建时绑定的 sched）。"""
    _ensure_schema()
    with _store() as st:
        idx = next((i for i, c in enumerate(st["cards"]) if c.get("id") == cid), None)
        if idx is None:
            return None
        card = st["cards"][idx]
        scheduler = make_scheduler(card.get("sched") or DEFAULT_SCHED)
        card = scheduler.grade(card, quality)
        log = card.get("review_log") or []
        log.append({"t": _now(), "quality": max(0, min(int(quality), 5)), "sched": scheduler.name})
        card["review_log"] = log[-200:]
        card["updated_at"] = _now()
        st["cards"][idx] = card
        st["dirty"] = True
        return card


def delete_card(cid: str) -> bool:
    _ensure_schema()
    with _store() as st:
        remains = [c for c in st["cards"] if c.get("id") != cid]
        if len(remains) == len(st["cards"]):
            return False
        st["cards"] = remains
        st["dirty"] = True
    return True
