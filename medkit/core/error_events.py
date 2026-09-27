"""EP-01 · P4 数据层：append-only 事件流水（`error_events`）。

为什么单独一张表而不是复用 `mistakes`：
- `mistakes` 是**当前快照**，用户可编辑（改正题干、补归因、标已掌握）；
- 跨轮次迁移矩阵要的是**历史事实**——"3 月这题我归成机制混淆，7 月归成记忆偏差"。
  快照被编辑一次，这段历史就没了。故流水**只追加、不更新、不删除**。

借鉴实现：事件溯源（Event Sourcing）里「快照 + 流水」的标准分工——
快照服务读侧（列表/复习），流水服务分析侧（迁移/趋势）。

幂等：`id` 由业务键派生（见 `append` 的 `dedupe_key`），重复追加同一事件不会产生两条。
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, Optional

from . import db as dbs

TABLE = "error_events"
COLS = ("kp_id", "round", "error_tag", "is_correct", "occurred_at")

# 事件类型（只增不改：已落库的值不可变更语义）
EVENT_ANSWER = "answer"        # 一次作答（含对/错）
EVENT_ATTR = "attribution"     # 一次归因（人工或 AI）
EVENT_REVIEW = "review"        # 一次复习回合


# db 路径判定：与 errorpipe/kpid 同一策略（项目内两种 patch 写法都兼容）。
_DB_SNAPSHOT = dbs.DB_PATH


def _db_ready() -> bool:
    """库是否已建。dbs.DB_PATH 被改过（测试隔离）就以它为准，否则用导入时快照。"""
    return dbs.DB_PATH.exists() if dbs.DB_PATH != _DB_SNAPSHOT else _DB_SNAPSHOT.exists()


def _event_id(kp_id: str, round_: str, event: str, business_key: str) -> str:
    raw = f"{event}|{kp_id}|{round_}|{business_key}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def append(*, kp_id: str, round_: str, event: str, business_key: str,
           error_tag: str = "", is_correct: Optional[bool] = None,
           occurred_at: str = "", extra: Optional[dict[str, Any]] = None) -> str:
    """追加一条流水。返回事件 id（幂等：同 business_key 重复追加返回同一个 id）。

    `business_key` 是业务去重键——错题用 `mistake_id`，判分回流用 `pid:question_id`，
    复习回合用 `card_id:revision`。**由调用方提供**而不是内部生成，因为只有调用方知道
    "什么算同一次事件"。这一条是从项目既有 `sync_from_paper` 的去重教训来的：
    那里用「题干前 40 字」当键，遇到案例组子题（共享题干）就误并了。
    """
    eid = _event_id(kp_id, round_, event, business_key)
    if not _db_ready():
        dbs.migrate()      # 首次调用即建库（对齐 ADR-006：不再回落 JSON）
    rec = {
        "id": eid,
        "kp_id": str(kp_id or ""),
        "round": str(round_ or ""),
        "event": str(event or ""),
        "error_tag": str(error_tag or ""),
        "is_correct": (None if is_correct is None else (1 if is_correct else 0)),
        "business_key": str(business_key or ""),
        "occurred_at": occurred_at or time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    if extra:
        rec.update({k: v for k, v in extra.items() if k not in ("id",)})
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, TABLE, rec, COLS)
    return eid


def list_events(kp_id: str = "", round_: str = "",
                event: str = "") -> list[dict[str, Any]]:
    """按条件取流水（全表读；量级在万级以下，实时算成本可忽略）。

    注意：**不能用 `find_row`**——那是取单行（LIMIT 1）。这里要集合，
    故走 `list_rows` + 调用侧过滤，与 `library.list_mistakes` 同一写法。
    """
    if not _db_ready():
        return []
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        rows = dbs.list_rows(cur, TABLE)
    finally:
        cur.close()
    out = rows
    if kp_id:
        out = [r for r in out if str(r.get("kp_id") or "") == kp_id]
    if round_:
        out = [r for r in out if str(r.get("round") or "") == round_]
    if event:
        out = [r for r in out if str(r.get("event") or "") == event]
    return out


def count() -> int:
    """流水条数（COUNT(*) 而非解析全表——照 `library.count_mistakes` 的做法）。"""
    if not _db_ready():
        return 0
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        row = cur.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()
        return int(row[0] if row else 0)
    finally:
        cur.close()


def stats() -> dict[str, Any]:
    """流水概览：按轮次/事件类型分布。"""
    rows = list_events()
    by_round: dict[str, int] = {}
    by_event: dict[str, int] = {}
    for r in rows:
        by_round[str(r.get("round") or "(未标轮次)")] = by_round.get(str(r.get("round") or "(未标轮次)"), 0) + 1
        by_event[str(r.get("event") or "")] = by_event.get(str(r.get("event") or ""), 0) + 1
    return {"total": len(rows), "by_round": by_round, "by_event": by_event}
