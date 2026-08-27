"""提问式学习会话（M4，借鉴 Cogniloop）：本地 Socratic 会话 + 概念状态机。

存储：~/.medkit/library/tutor_sessions.json（跨项目个人资产，与出题解耦）。
无外部依赖；原子写复用 fsutil.write_json_atomic。
仅负责「会话持久化 + 状态晋升 + 提问类型轮换」等**纯本地布尔/规则逻辑**；
真正的出问/判分/追问文本由 agents/medtutor.py 交给 LLM，这里不调模型。

概念状态：weak → shaky → solid → mastered（与掌握度状态机同字段名，独立演进）。
规则：单轮判分 score∈[0,3]；score≥2 记「通过」，连续通过 {PASS_STREAK} 次升一档；
score<2 视为「有差距」，打断连击并重出同类追问（不立即降档，避免挫败）。
提问类型五类轮换：解释/应用/对比/预测/追溯。
"""

import itertools
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import read_json_list, write_json_atomic

LIBRARY_DIR = cfg.CONFIG_DIR / "library"
TUTOR_SESSIONS_FILE = LIBRARY_DIR / "tutor_sessions.json"

# SQL 模式（S0·方案 §2.3）：medkit.db 存在即行级事务（BEGIN IMMEDIATE 串行读-改-写）。
DB_FILE = dbs.DB_PATH
_LOCK = threading.RLock()
_T_COLS = ("subject", "kp_name", "state", "updated_at")
_SEQ = itertools.count()

CONCEPT_STATES = ["weak", "shaky", "solid", "mastered"]

# 五类提问轮换（解释→应用→对比→预测→追溯）；上一轮有差距则同类型追问不换档
QUESTION_TYPES = ["explain", "apply", "contrast", "predict", "trace"]
QUESTION_LABELS = {
    "explain": "解释", "apply": "应用", "contrast": "对比",
    "predict": "预测", "trace": "追溯",
}

PASS_SCORE = 2          # 判分 0~3；≥2 计通过
PASS_STREAK = 2         # 连续通过次数 → 升一档
MAX_ROUNDS = 24         # 会话最大轮次护栏，防失控滚轮


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _store_is_sql() -> bool:
    return DB_FILE.exists()


def _load() -> list[dict[str, Any]]:
    """读会话列表；缺失/损坏 → 空（统一容错）。SQL 模式读表，JSON 模式读文件。"""
    if _store_is_sql():
        conn = dbs.get_conn()
        cur = conn.cursor()
        try:
            return dbs.list_rows(cur, "tutor_sessions")
        finally:
            cur.close()
    return read_json_list(TUTOR_SESSIONS_FILE)


def _save(records: list[dict[str, Any]]) -> None:
    """写会话列表。SQL 模式事务整组替换；JSON 模式复用 fsutil 原子写。"""
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            dbs.replace_all(cur, "tutor_sessions", records, _T_COLS)
        return
    write_json_atomic(TUTOR_SESSIONS_FILE, records)


@contextmanager
def _store() -> Iterator[dict[str, Any]]:
    """会话读-改-写视图；退出时按 dirty 写回（SQL 单事务 / JSON RLock+原子写）。"""
    if _store_is_sql():
        with dbs.tx(write=True) as cur:
            st: dict[str, Any] = {"sessions": dbs.list_rows(cur, "tutor_sessions"),
                                  "cur": cur, "dirty": False}
            yield st
            if st["dirty"]:
                dbs.replace_all(cur, "tutor_sessions", st["sessions"], _T_COLS)
        return
    with _LOCK:
        st = {"sessions": list(_load()), "cur": None, "dirty": False}
        yield st
        if st["dirty"]:
            _save(st["sessions"])


# ---------------------------------------------------------------- 纯逻辑（可测/可解释）
def next_state(state: str) -> str:
    """概念状态升一档；已是 mastered 则保持。"""
    i = CONCEPT_STATES.index(state) if state in CONCEPT_STATES else 0
    j = min(i + 1, len(CONCEPT_STATES) - 1)
    return CONCEPT_STATES[j]


def next_question_type(qtype: str, result_score: Optional[int]) -> str:
    """提问类型轮换：本轮判分有差距（result_score<2）→ 同类型追问；否则按顺序推进。"""
    if result_score is not None and result_score < PASS_SCORE:
        return qtype if qtype in QUESTION_TYPES else QUESTION_TYPES[0]
    i = QUESTION_TYPES.index(qtype) + 1 if qtype in QUESTION_TYPES else 0
    return QUESTION_TYPES[i % len(QUESTION_TYPES)]


def apply_score(state: str, streak: int, score: int) -> tuple[str, int]:
    """本地判定：根据单轮判分推进概念状态与连击。返回 (new_state, new_streak)。"""
    score = max(0, min(int(score), 3))
    if score >= PASS_SCORE:
        streak += 1
        if state != "mastered" and streak >= PASS_STREAK:
            return next_state(state), 0
        return state, streak
    return state, 0     # 有差距 → 打断连击，不降档（重出同类追问）


# ---------------------------------------------------------------- 会话 CRUD
def start_session(subject: str, kp_name: str, kp_id: str = "") -> dict[str, Any]:
    """开一个提问会话，初始概念状态取该知识点当前掌握度（无则 weak）。"""
    with _store() as st:
        sessions = st["sessions"]
        sid = f"tu_{int(time.time() * 1000) % 100000000}_{next(_SEQ)}"   # 时间戳+序号：防同毫秒撞 id
        state = "weak"
        if kp_name:
            try:
                from . import library as lib
                kp = next((k for k in lib.list_knowledge() if k.get("name") == kp_name), None)
                if kp:
                    state = kp.get("state") or "weak"
            except Exception:  # noqa: BLE001
                state = "weak"
        session = {
            "id": sid, "subject": subject, "kp_name": kp_name, "kp_id": kp_id or "",
            "state": state, "streak": 0,
            "current": {"type": "explain", "text": ""},   # 待学生作答的当前问题
            "rounds": [],                                  # 已回答的轮次（同一 round=1 起）
            "created_at": _now(), "updated_at": _now(),
        }
        sessions.append(session)
        st["dirty"] = True
    return session


def seed_first(sid: str, qtype: str, question: str) -> Optional[dict[str, Any]]:
    """start 后把第一问写进 session.current（学生尚未作答）。"""
    with _store() as st:
        s = next((x for x in st["sessions"] if x.get("id") == sid), None)
        if s is None:
            return None
        s["current"] = {"type": qtype, "text": question}
        s["updated_at"] = _now()
        st["dirty"] = True
    return s


def get_session(sid: str) -> Optional[dict[str, Any]]:
    return next((s for s in _load() if s.get("id") == sid), None)


def delete_session(sid: str) -> bool:
    """删除一场会话，返回是否命中。"""
    with _store() as st:
        sessions = st["sessions"]
        remains = [s for s in sessions if s.get("id") != sid]
        if len(remains) == len(sessions):
            return False
        st["sessions"] = remains
        st["dirty"] = True
    return True


def cleanup_stale(days: int = 30) -> int:
    """C18：清理 days 天无活动的会话（防止会话列表无限增长）。

    与 dashboard._active_within 同口径：无 updated_at/created_at 的异常记录保守保留。
    返回删除条数。
    """
    from datetime import datetime, timedelta

    now = datetime.now()
    cutoff = now - timedelta(days=max(1, int(days)))
    removed = 0
    with _store() as st:
        sessions = st["sessions"]
        keep: list[dict[str, Any]] = []
        for s in sessions:
            ts = s.get("updated_at") or s.get("created_at") or ""
            stale = False
            if ts:
                try:
                    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    stale = t.replace(tzinfo=None) < cutoff
                except Exception:  # noqa: BLE001  无法解析 → 保守保留
                    stale = False
            if stale:
                removed += 1
            else:
                keep.append(s)
        if removed:
            st["sessions"] = keep
            st["dirty"] = True
    return removed


def list_sessions(subject: str = "") -> list[dict[str, Any]]:
    sessions = _load()
    if subject:
        sessions = [s for s in sessions if s.get("subject") == subject
                    or s.get("subject") == ""]
    sessions.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return sessions


def record_answer(sid: str, user_answer: str, score: int,
                  gap: str, next_question: str) -> Optional[dict[str, Any]]:
    """提交一轮作答：写回 rounds + 推进状态机 + 计算下一问类型。返回更新后的会话。"""
    with _store() as st:
        sessions = st["sessions"]
        s = next((x for x in sessions if x.get("id") == sid), None)
        if s is None:
            return None
        cur = s.get("current") or {"type": "explain", "text": ""}
        qtype, qtext = cur.get("type", "explain"), cur.get("text", "")
        rounds = s.get("rounds") or []
        if len(rounds) >= MAX_ROUNDS:       # 护栏：超轮次不再继续（防无限滚轮）
            return s
        score = max(0, min(int(score), 3))
        state, streak = apply_score(s.get("state", "weak"), int(s.get("streak", 0)), score)
        rounds.append({
            "round": len(rounds) + 1, "type": qtype, "question": qtext,
            "user_answer": user_answer, "score": score, "gap": gap, "at": _now(),
        })
        next_type = next_question_type(qtype, score)
        s["state"] = state
        s["streak"] = streak
        s["rounds"] = rounds
        s["current"] = {"type": next_type, "text": next_question or ""}
        s["updated_at"] = _now()
        st["dirty"] = True
    return s
