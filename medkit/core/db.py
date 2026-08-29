"""学习库 SQLite 底座（S0·方案 §2.3）：WAL + user_version 迁移 + 升级前自动备份 + JSON 幂等导入。

约定（《结构化执行方案_2026-08-27.md》§2.3）：
- `core/db.py` 只暴露 get_conn() / tx() / migrate() / import_from_json() / backup_library()；
  各 domain 模块（library/review/…）对外函数签名与现状完全一致（routers 零改动），内部换成 SQL。
- 存储模型：每表 `id TEXT PRIMARY KEY + data TEXT(整条 JSON) + 少量查询列`——
  JSON→行→dict 无损往返，域代码的 dict 语义 100% 保持（数据永远可退：.pre-db-*.bak 随时可回）。
- 迁移：MIGRATIONS = [1, 2, …]，PRAGMA user_version 单调前进；每版配升级/回滚函数；
  升级前自动备份（backup_library 复制 library 目录全部 JSON + 既有 db）。
- 幂等：import_from_json 以 id 为键 INSERT OR REPLACE，重跑不重复；导入成功后原 JSON 改名 *.pre-db-<ts>.bak。
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config as cfg

# 模块级常量（调用时读取 → 测试可 monkeypatch 到临时目录，不影响真实 ~/.medkit）
LIBRARY_DIR = cfg.CONFIG_DIR / "library"
DB_PATH = LIBRARY_DIR / "medkit.db"

# ---------------------------------------------------------------- schema（版本 1）
def _tbl(name: str, cols: str) -> str:
    return (
        f"CREATE TABLE IF NOT EXISTS {name} (\n"
        f"    id TEXT PRIMARY KEY,\n"
        f"    data TEXT NOT NULL,\n"
        f"    {cols}\n)"
    )


_V1_UP: list[str] = [
    _tbl("mistakes",
         "subject TEXT, chapter TEXT, topic TEXT, state TEXT, "
         "miss_count INTEGER, learned INTEGER, created_at TEXT"),
    _tbl("knowledge",
         "name TEXT, subject TEXT, chapter TEXT, state TEXT, "
         "priority REAL, score REAL, attempts INTEGER, last_tried TEXT"),
    _tbl("explains", "subject TEXT, kp_name TEXT, created_at TEXT"),
    _tbl("review_cards", "subject TEXT, kp_name TEXT, state TEXT, due TEXT, created_at TEXT"),
    _tbl("tutor_sessions", "subject TEXT, kp_name TEXT, state TEXT, updated_at TEXT"),
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
    # slices_fts：S1 检索辅表（FTS5 预分词列；D2 经 K1 验证可用）
    "CREATE VIRTUAL TABLE IF NOT EXISTS slices_fts USING fts5(subject UNINDEXED, text, tokens)",
]

_V1_DOWN: list[str] = [
    "DROP TABLE IF EXISTS slices_fts",
    "DROP TABLE IF EXISTS tutor_sessions",
    "DROP TABLE IF EXISTS review_cards",
    "DROP TABLE IF EXISTS explains",
    "DROP TABLE IF EXISTS knowledge",
    "DROP TABLE IF EXISTS mistakes",
    "DROP TABLE IF EXISTS meta",
]

# v2（WP-01 大纲覆盖度）：syllabus_items —— 考试锚定条目（kind=chapter 章 或 item 考点）。
_V2_UP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS syllabus_items (
        id TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        subject TEXT, chapter TEXT, kind TEXT,
        item TEXT, weight REAL, source TEXT, created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_syll_subject ON syllabus_items(subject)",
    "CREATE INDEX IF NOT EXISTS idx_syll_chapter ON syllabus_items(chapter)",
    "CREATE INDEX IF NOT EXISTS idx_syll_kind ON syllabus_items(kind)",
]

_V2_DOWN: list[str] = [
    "DROP TABLE IF EXISTS syllabus_items",
]

# v3（WP-02 真题考频）：realexam_freq —— 自备真题考点频次（人工确认门，未确认不进权重）。
_V3_UP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS realexam_freq (
        id TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        subject TEXT, chapter TEXT, item TEXT,
        freq INTEGER, confirmed INTEGER, source TEXT, created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_re_subject ON realexam_freq(subject)",
    "CREATE INDEX IF NOT EXISTS idx_re_confirmed ON realexam_freq(confirmed)",
]

_V3_DOWN: list[str] = [
    "DROP TABLE IF EXISTS realexam_freq",
]

# v4（大纲二选一改造）：syllabus_items.source 归一 —— 历史 'paste'（用户粘贴）并入 'teacher'。
# 二选一模型下用户自供内容统一为「教师重点」标准；data JSON 与查询列须同步更新（row_to_dict
# 以 data 为权威，列仅作 WHERE 过滤）。此迁移幂等（仅命中仍为 paste 的行）。
_V4_UP: list[str] = [
    "UPDATE syllabus_items SET source='teacher', "
    "data=json_set(data, '$.source', 'teacher') WHERE source='paste'",
]

# v4 不可精确回滚：行 id 是 sha1 哈希（不含 source 标记），无法区分「历史 paste 行」与
# 「原生 teacher 行」。DOWN 仅恢复 user_version，数据保持归一结果——回滚兜底依赖升级前的
# 全量自动备份（ADR-005 既有机制）。
_V4_DOWN: list[str] = []

# v5（WP-05/NX-04）：cards —— 医学记忆卡（讲解产物 → 记忆卡，FSRS 默认 / SM-2 可切）。
_V5_UP: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS cards (
        id TEXT PRIMARY KEY,
        data TEXT NOT NULL,
        subject TEXT, source TEXT, kind TEXT,
        state TEXT, due TEXT, created_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_cards_subject ON cards(subject)",
    "CREATE INDEX IF NOT EXISTS idx_cards_due ON cards(due)",
    "CREATE INDEX IF NOT EXISTS idx_cards_source ON cards(source)",
]

_V5_DOWN: list[str] = [
    "DROP TABLE IF EXISTS cards",
]

# v6（2026-08-29 真题标注）：realexam_freq 增 year 列（真题文本提取的年份，可空；
# 供出题来源标注 source_year 使用）。SQLite ALTER ADD COLUMN 可回滚性差，
# DOWN 留空——回滚兜底依赖升级前全量自动备份（ADR-005 既有机制）。
_V6_UP: list[str] = [
    "ALTER TABLE realexam_freq ADD COLUMN year TEXT",
]

_V6_DOWN: list[str] = []

MIGRATIONS: list[int] = [1, 2, 3, 4, 5, 6]  # 版本列表（只增不改）


def _upgrade_to(cur: sqlite3.Cursor, ver: int) -> None:
    if ver == 1:
        for stmt in _V1_UP:
            cur.execute(stmt)
        return
    if ver == 2:
        for stmt in _V2_UP:
            cur.execute(stmt)
        return
    if ver == 3:
        for stmt in _V3_UP:
            cur.execute(stmt)
        return
    if ver == 4:
        for stmt in _V4_UP:
            cur.execute(stmt)
        return
    if ver == 5:
        for stmt in _V5_UP:
            cur.execute(stmt)
        return
    if ver == 6:
        # 幂等防御：重升级路径（如测试模拟旧库 / 手工降 user_version）下列可能已存在
        cur.execute("PRAGMA table_info(realexam_freq)")
        cols = {r[1] for r in cur.fetchall()}
        if "year" not in cols:
            cur.execute("ALTER TABLE realexam_freq ADD COLUMN year TEXT")
        return
    raise ValueError(f"未知迁移版本 {ver}")


def _downgrade_from(cur: sqlite3.Cursor, ver: int) -> None:
    if ver == 1:
        for stmt in _V1_DOWN:
            cur.execute(stmt)
        return
    if ver == 2:
        for stmt in _V2_DOWN:
            cur.execute(stmt)
        return
    if ver == 3:
        for stmt in _V3_DOWN:
            cur.execute(stmt)
        return
    if ver == 4:
        for stmt in _V4_DOWN:
            cur.execute(stmt)
        return
    if ver == 5:
        for stmt in _V5_DOWN:
            cur.execute(stmt)
        return
    if ver == 6:
        for stmt in _V6_DOWN:
            cur.execute(stmt)
        return
    raise ValueError(f"未知迁移版本 {ver}")


# ---------------------------------------------------------------- 连接与事务
_local = threading.local()


def get_conn() -> sqlite3.Connection:
    """每线程一个连接（SQLite 连接不可跨线程；WAL 下打开成本极低）。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(DB_PATH), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        _local.conn = conn
    return conn


def reset_conn() -> None:
    """丢弃当前线程的缓存连接（测试替换 DB_PATH 后用）。"""
    _local.conn = None


@contextmanager
def tx(write: bool = True) -> Iterator[sqlite3.Cursor]:
    """事务上下文。write=True 用 BEGIN IMMEDIATE（写锁先行）——并发读-改-写串行化，杜绝丢失更新。"""
    conn = get_conn()
    conn.execute("BEGIN IMMEDIATE" if write else "BEGIN")
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def user_version() -> int:
    return get_conn().execute("PRAGMA user_version").fetchone()[0]


def enabled() -> bool:
    """当前是否处于 SQL 模式（db 文件已建立）。"""
    return DB_PATH.exists()


def _backup_one(p: Path, tag: str, ts: str) -> Optional[Path]:
    """备份单个文件 → {原名}.{tag}-<ts>.bak；返回备份路径或 None。"""
    bak = p.with_name(f"{p.name}.{tag}-{ts}.bak")
    try:
        shutil.copy2(p, bak)
    except OSError:
        return None
    return bak


def _backup_before_migrate(include_db: bool) -> None:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    lib = LIBRARY_DIR
    if lib.is_dir():
        paths = sorted(lib.glob("*.json"))
        if include_db:  # 已有旧版本 db（升级既有库）才备份 db；首次迁移仅 JSON 有数据
            paths += sorted(lib.glob("medkit.db"))
        for p in paths:
            if p.exists():
                _backup_one(p, "pre-db", ts)


def migrate() -> int:
    """升级到最新 schema；返回最终版本。升级前自动备份；重复调用幂等。"""
    ver = user_version()
    target = MIGRATIONS[-1]
    if ver >= target:
        return ver
    if ver not in (0,) + tuple(MIGRATIONS):
        raise ValueError(f"未知数据库版本 {ver}（可能来自更高版本程序，拒绝降级）")
    _backup_before_migrate(include_db=ver > 0)
    with tx(write=True) as cur:
        for v in MIGRATIONS:
            if v <= ver:
                continue
            _upgrade_to(cur, v)
            cur.execute(f"PRAGMA user_version = {v}")
    return user_version()


def downgrade_to(ver: int) -> int:
    """回滚到指定版本（配回滚 SQL）。调用方应先确认备份。"""
    cur_ver = user_version()
    if cur_ver in (0,) + tuple(MIGRATIONS) and ver == 0 and cur_ver in MIGRATIONS:
        with tx(write=True) as cur:
            for v in reversed(MIGRATIONS):
                if v <= ver:
                    break
                _downgrade_from(cur, v)
                cur.execute(f"PRAGMA user_version = {v - 1}")
    else:
        raise ValueError(f"暂不支持回滚到 {ver}（当前 {cur_ver}）")
    return user_version()


# ---------------------------------------------------------------- 通用行存取
def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """query 列是冗余索引，权威数据在 data JSON（行→dict 无损）。"""
    d = dict(row)
    try:
        payload = json.loads(d.pop("data"))
    except Exception:  # noqa: BLE001
        payload = {}
    if isinstance(payload, dict):
        payload.setdefault("id", d.get("id"))
        return payload
    return {}


def list_rows(cur: sqlite3.Cursor, table: str,
              where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    cur.execute(f"SELECT * FROM {table} {where}", params)
    return [row_to_dict(r) for r in cur.fetchall()]


def put_row(cur: sqlite3.Cursor, table: str, rec: dict[str, Any],
            cols: tuple[str, ...] = ()) -> None:
    """按 id INSERT OR REPLACE；cols 为需冗余的查询列（自动从 rec 取值）。"""
    data = json.dumps(rec, ensure_ascii=False)
    extra = {c: json.dumps(rec.get(c), ensure_ascii=False) if isinstance(rec.get(c), (list, dict))
             else rec.get(c) for c in cols}
    names = ["id", "data"] + list(cols)
    values = [rec.get("id"), data] + [extra[c] for c in cols]
    cur.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(names)}) "
        f"VALUES ({', '.join('?' for _ in names)})", values)


def replace_all(cur: sqlite3.Cursor, table: str, recs: list[dict[str, Any]],
                cols: tuple[str, ...] = ()) -> None:
    """整组替换（同一事务内；配合 tx(write=True) 与 begin immediate 串行化）。"""
    cur.execute(f"DELETE FROM {table}")
    for rec in recs:
        put_row(cur, table, rec, cols)


# ---------------------------------------------------------------- 备份与导入
def backup_library(tag: str = "pre-db") -> list[str]:
    """复制 library 目录全部 JSON + medkit.db → {原名}.{tag}-<ts>.bak；返回备份路径列表。"""
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    lib = LIBRARY_DIR
    made: list[str] = []
    if not lib.is_dir():
        return made
    for p in sorted(lib.glob("*.json")) + sorted(lib.glob("medkit.db")):
        if not p.exists():
            continue
        bak = _backup_one(p, tag, ts)
        if bak:
            made.append(str(bak))
    return made


def _json_rows(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("id")]


IMPORT_MAP: dict[str, tuple[str, tuple[str, ...]]] = {
    "mistakes": ("mistakes.json", ("subject", "chapter", "topic", "state",
                                   "miss_count", "learned", "created_at")),
    "knowledge": ("knowledge.json", ("name", "subject", "chapter", "state",
                                     "priority", "score", "attempts", "last_tried")),
    "explains": ("explains.json", ("subject", "kp_name", "created_at")),
    "review_cards": ("review_queue.json", ("subject", "kp_name", "state", "due", "created_at")),
    "tutor_sessions": ("tutor_sessions.json", ("subject", "kp_name", "state", "updated_at")),
}


def import_from_json() -> dict[str, str]:
    """JSON → SQLite 幂等导入：以 id 为键；同时填充查询列（put_row）；成功后原 JSON 改名。

    返回 {表名: 状态}（"imported n" / "skip(no file)" / "skip(done)" / "skip(empty)"）。
    """
    migrate()
    lib = LIBRARY_DIR
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    result: dict[str, str] = {}
    with tx(write=True) as cur:
        for table, (fname, cols) in IMPORT_MAP.items():
            path = lib / fname
            if not path.exists():
                result[table] = "skip(no file)"
                continue
            rows = _json_rows(path)
            if not rows:
                result[table] = "skip(empty)"
                continue
            # 幂等性由「导入成功后原 JSON 改名」保证；meta 标记仅作账本。
            # 若导入后 JSON 又被写入（旧实例/导入源回流）→ 下次调用会按 id 幂等补导，
            # 避免「JSON 活数据永远进不了 DB」的丢失风险（一次性标记曾导致该问题）。
            for r in rows:
                put_row(cur, table, r, cols)
            cur.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (f"imported::{table}", ts),
            )
            # 导入成功后原 JSON 改名（不再被当成活数据；回滚走 .pre-db-*.bak）。
            # 标签与 migrate 备份的 .pre-db-<ts> 区分，避免与同秒备份名冲突（Windows rename 不覆盖）。
            bak = path.with_name(f"{path.name}.pre-db-import-{ts}-{uuid.uuid4().hex[:4]}.bak")
            try:
                path.rename(bak)
            except OSError:
                pass
            result[table] = f"imported {len(rows)}"
    return result


# ---------------------------------------------------------------- slices_fts 检索辅表（IMP-06）
# D2/ADR-002：FTS5 + jieba 预分词列（K1 已验证）。表在 v1 已建（user_version 不变），
# 本组函数只写数据不迁移；JSON 模式（未建库）reindex 返回 0 → 调用方自动回退 bigram top-k。
_CJK_SEG = re.compile(r"[\u4e00-\u9fff]{2,}")


def fts_tokens(text: str) -> list[str]:
    """FTS 预分词：jieba 词 + CJK 二元组（小写）。

    二元组兜底保证词典外的词组也能被检索命中（如「心衰」命中「心力衰竭」）；
    ASCII 统一小写（FTS5 unicode61 检索即小写，这里保持一致避免查询侧拼写漂移）。
    NX-02（R-3）：jieba 缺失/词典损坏（打包环境常见）→ 仅 bigram 兜底，绝不抛错。
    """
    toks: list[str] = []
    try:
        import jieba  # noqa: PLC0415 懒加载：JSON 模式（未装/未建库）不拖垮导入

        for t in jieba.cut(text or ""):
            t = t.strip().lower()
            if t:
                toks.append(t)
    except Exception:  # noqa: BLE001  NX-02：jieba 不可用 → 依赖下方 bigram 兜底
        pass
    for seg in _CJK_SEG.findall((text or "").lower()):
        toks.extend(seg[i:i + 2] for i in range(len(seg) - 1))
    return toks


def fts_match_expr(query: str) -> str:
    """查询串 → FTS5 MATCH 表达式：token 前缀式 OR 召回（去重，单字过滤，≤40 项）。

    前缀式保证「心衰*」能命中 token「心力衰竭」（FTS5 token 前缀匹配）。
    """
    toks: list[str] = []
    for t in fts_tokens(query or ""):
        if len(t) >= 2 and t not in toks:
            toks.append(t)
    return " OR ".join(f'"{t}"*' for t in toks[:40])


def reindex_slices(rows: list[dict[str, Any]]) -> int:
    """重建 slices_fts（FTS5）：rows = [{subject, text, title?}]，写入原文 + 预分词 tokens。

    SQL 模式（medkit.db 已建）全量重建；JSON 模式返回 0。title 并入 tokens 列
    （标题命中同样可召回），text 列保持与切片索引一致的截断文本（供结果回映射）。
    """
    if not DB_PATH.exists():
        return 0
    try:
        import jieba  # noqa: PLC0415, F401 仅探测可用性（fts_tokens 内再 import）
    except Exception:  # noqa: BLE001
        return 0
    n = 0
    with tx(write=True) as cur:
        cur.execute("DELETE FROM slices_fts")
        for r in rows:
            subject = str(r.get("subject") or "未分类")
            text = str(r.get("text") or "")
            if not text.strip():
                continue
            toks = fts_tokens(f"{r.get('title') or ''} {text}")
            cur.execute(
                "INSERT INTO slices_fts (subject, text, tokens) VALUES (?, ?, ?)",
                (subject, text[:2000], " ".join(toks)))
            n += 1
    return n
