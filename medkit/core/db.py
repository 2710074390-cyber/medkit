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

MIGRATIONS: list[int] = [1]  # 版本列表（只增不改）


def _upgrade_to(cur: sqlite3.Cursor, ver: int) -> None:
    if ver == 1:
        for stmt in _V1_UP:
            cur.execute(stmt)
        return
    raise ValueError(f"未知迁移版本 {ver}")


def _downgrade_from(cur: sqlite3.Cursor, ver: int) -> None:
    if ver == 1:
        for stmt in _V1_DOWN:
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


IMPORT_MAP: dict[str, str] = {
    "mistakes": "mistakes.json",
    "knowledge": "knowledge.json",
    "explains": "explains.json",
    "review_cards": "review_queue.json",
    "tutor_sessions": "tutor_sessions.json",
}


def import_from_json() -> dict[str, str]:
    """JSON → SQLite 幂等导入：以 id 为键 INSERT OR REPLACE；成功后原 JSON 改名 .pre-db-<ts>.bak。

    返回 {表名: 状态}（"imported n" / "skip(no file)" / "skip(done)" / "skip(empty)"）。
    """
    migrate()
    lib = LIBRARY_DIR
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    result: dict[str, str] = {}
    with tx(write=True) as cur:
        for table, fname in IMPORT_MAP.items():
            path = lib / fname
            if not path.exists():
                result[table] = "skip(no file)"
                continue
            done = cur.execute(
                "SELECT value FROM meta WHERE key = ?", (f"imported::{table}",)
            ).fetchone()
            if done:
                result[table] = "skip(done)"
                continue
            rows = _json_rows(path)
            if not rows:
                result[table] = "skip(empty)"
                continue
            cur.executemany(
                f"INSERT OR REPLACE INTO {table} (id, data) VALUES (?, ?)",
                [(r["id"], json.dumps(r, ensure_ascii=False)) for r in rows],
            )
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
