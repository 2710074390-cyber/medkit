"""R5-04：从历史备份目录精确恢复用户学习数据（一次性维护脚本，非应用功能）。

背景（docs/reviews/ux-audit-r5-2026-09-01.md §2 R5-04 + 本轮取证修订）：
08-27 晚 conftest 隔离缺口导致测试写入真实库（污染窗口 2026-08-27T20:43:53 起），
随后 08-28 00:41 的 import 又把污染 JSON 导入 SQL 库；9 月 1 日发现四个学习表 0 行。
取证结论（2026-09-01 实机）：
- 备份目录顶层 mistakes.json（113KB/182 行）与 knowledge.json（6.3KB/3 行）**全部是测试垃圾**
  （subject 全空、题目=测试题甲/乙/肺通气题/心绞痛题、时间 20:43:53→08-28 00:27）——不能用作恢复源；
- 真实用户数据存在于**污染前（≤2026-08-27T20:40:00）**的产物：
  * mistakes：`mistakes.json.pre-db-20260827-201247.bak`（1 条：m_1787812464960，儿科学·支气管肺炎，14:34）
  * knowledge：WAL 回放库（backup 目录 medkit.db + medkit.db-wal 3.2MB 重放）中的
    kp_1787812464969（支气管肺炎首选治疗）/ kp_1787827117317 / kp_1787827117385 / kp_1787827117414（DKA）
    （18:38/14:34 批次；带 `kp_1787834*` 前缀的为污染行，排除）
  * review_cards：WAL 回放库 rev_17654507（支气管肺炎首选治疗，2026-08-28 到期）
  * tutor_sessions：`tutor_sessions.json.pre-db-import-20260827-201705-577e.bak`（tu_27481545，18:44）
  * 已完好无需恢复：realexam_freq（10 行）、syllabus_items（2582 行）、slices_fts
- 已完好而无数据的需另寻：explains（备份 explains.json 均为空 []）、cards（无）
本脚本即按上述来源做「缺失才插入、绝不过覆盖、以 id 幂等」的恢复，并回查验证。

用法：
    python pack/recover-user-data.py [--dry-run] [--backup DIR]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DEFAULT_BACKUP = Path.home() / ".medkit" / "library-backup-20260828-004033"
LIB = Path.home() / ".medkit" / "library"
# 污染窗口起点：第一次污染写入发生在 2026-08-27T20:43:53（测试题甲/乙批次）
POLLUTION_START = "2026-08-27T20:40:00"
# 带该前缀的 id 均为污染批次（同一次 pytest 运行生成的知识/错题 id 前缀即毫秒时间戳）
POLLUTION_ID_PREFIX = ("kp_1787834", "m_1787834")

# 非污染「时间戳业务字段」：mistakes 用 created_at；knowledge 无 created_at 用 last_tried；
# 无任何时间戳的行（如测试章 kp_1787834839551_1）一律视为可疑（真实行都带 14:34/18:38 时间）。
_PRE_POLLUTION_FILES = {
    "mistakes": ("mistakes.json.pre-db-20260827-201247.bak", "created_at"),
    "knowledge": ("knowledge.json.pre-db-20260827-201247.bak", "last_tried"),
}


def _load_json_rows(path: Path) -> list[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict) and r.get("id")]


def _is_pre_pollution(rec: dict, ts_field: str) -> bool:
    ts = str(rec.get(ts_field) or "")
    if not ts:
        return False  # 无时间戳 → 不可信
    return ts[:19] < POLLUTION_START and not str(rec.get("id", "")).startswith(POLLUTION_ID_PREFIX)


def _replay_wal(backup: Path, tmp: Path) -> sqlite3.Connection:
    """WAL 回放：把备份 db+wal+shm 拷到临时目录打开（WAL 未 checkpoint 时数据在 -wal 里）。"""
    for f in ("medkit.db", "medkit.db-wal", "medkit.db-shm"):
        src = backup / f
        if src.exists():
            shutil.copy2(src, tmp / f)
    return sqlite3.connect(str(tmp / "medkit.db"))


def _collect_sources(backup: Path) -> dict[str, list[dict]]:
    """按取证结论收集可信恢复源（id 去重，先到先得）。"""
    out: dict[str, list[dict]] = {"mistakes": [], "knowledge": [], "review_cards": [], "tutor_sessions": []}

    # 1) mistakes / knowledge：污染前的 JSON 快照（18:45）
    for table, (fname, ts_field) in _PRE_POLLUTION_FILES.items():
        for rec in _load_json_rows(backup / fname):
            if _is_pre_pollution(rec, ts_field):
                out[table].append(rec)

    # 2) review_cards / knowledge 的库内权威版本：WAL 回放（导入后的规范化列）
    with tempfile.TemporaryDirectory(prefix="r5wal-") as td:
        conn = _replay_wal(backup, Path(td))
        try:
            rows = conn.execute("SELECT data FROM review_cards").fetchall()
            for (data,) in rows:
                try:
                    rec = json.loads(data)
                    if isinstance(rec, dict) and rec.get("id"):
                        out["review_cards"].append(rec)
                except Exception:  # noqa: BLE001
                    continue
            rows = conn.execute("SELECT data FROM knowledge").fetchall()
            for (data,) in rows:
                try:
                    rec = json.loads(data)
                except Exception:  # noqa: BLE001
                    continue
                rid = rec.get("id") if isinstance(rec, dict) else ""
                # WAL 里的规范版本优先于 JSON：同 id 时替换
                if rid:
                    out["knowledge"] = [r for r in out["knowledge"] if r.get("id") != rid]
                    if isinstance(rec, dict) and (_is_pre_pollution(rec, "last_tried") or rec.get("history")):
                        out["knowledge"].append(rec)
        finally:
            conn.close()

    # 3) tutor_sessions：污染前导入快照（20:17，仅剩此一份，463B）
    for rec in _load_json_rows(backup / "tutor_sessions.json.pre-db-import-20260827-201705-577e.bak"):
        if _is_pre_pollution(rec, "created_at"):
            out["tutor_sessions"].append(rec)
    return out


_TABLE_COLS: dict[str, list[str]] = {
    "mistakes": ["subject", "chapter", "topic", "state", "miss_count", "learned", "created_at"],
    "knowledge": ["name", "subject", "chapter", "state", "priority", "score", "attempts", "last_tried"],
    "review_cards": ["subject", "kp_name", "state", "due", "created_at"],
    "tutor_sessions": ["subject", "kp_name", "state", "updated_at"],
}


def _live_count(conn: sqlite3.Connection, table: str) -> dict[int, int]:
    try:
        conn.execute("SELECT COUNT(*) FROM " + table)
    except Exception:  # noqa: BLE001
        return {}
    return {rid: 1 for rid in (r[0] for r in conn.execute("SELECT id FROM " + table))}


def main() -> int:
    ap = argparse.ArgumentParser(description="R5-04 修正版：仅恢复污染前的真实用户数据（幂等）")
    ap.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    backup: Path = args.backup.expanduser()

    print(f"== MedKit R5-04 修正版恢复 ==\n备份目录：{backup}")
    if not backup.exists():
        print("[错误] 备份目录不存在，中止。")
        return 2

    live = sqlite3.connect(str(LIB / "medkit.db"))
    live.execute("PRAGMA busy_timeout=30000")
    sources = _collect_sources(backup)

    plan: list[tuple[str, dict]] = []
    for table in ("mistakes", "knowledge", "review_cards", "tutor_sessions"):
        existing = {r[0] for r in live.execute(f"SELECT id FROM {table}")} if _table_exists(live, table) else set()
        for rec in sources[table]:
            is_new = rec.get("id") not in existing
            plan.append((table, rec))
            if is_new:
                print(f"  待恢复 [{table}] {rec.get('id')} "
                      f"（{str(rec.get('subject') or rec.get('kp_name') or '')[:14]}）")
            else:
                print(f"  已存在   [{table}] {rec.get('id')} ——跳过（不覆盖）")
    print(f"共计划 {len([1 for t, r in plan if t])} 行；真实库当前行数："
          f"mistakes={_cnt(live, 'mistakes')} knowledge={_cnt(live, 'knowledge')} "
          f"review_cards={_cnt(live, 'review_cards')} tutor_sessions={_cnt(live, 'tutor_sessions')}")

    if args.dry_run:
        print("== dry-run：未做任何写入 ==")
        live.close()
        return 0

    # 安全快照
    snap = Path.home() / f".medkit-backup-r5recover-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    print(f"[1/2] 全量快照 ~/.medkit → {snap} …")
    try:
        shutil.copytree(Path.home() / ".medkit", snap)
    except Exception as e:  # noqa: BLE001
        print(f"[错误] 快照失败：{e}；中止。")
        live.close()
        return 2
    print("  快照完成。")

    print("[2/2] INSERT OR IGNORE（以 id 幂等；覆盖现库 0 行数据）…")
    for table, rec in plan:
        cols = _TABLE_COLS[table]
        names = ["id", "data"] + cols
        values = [str(rec.get("id")), json.dumps(rec, ensure_ascii=False)] + \
                 [json.dumps(rec.get(c), ensure_ascii=False) if isinstance(rec.get(c), (list, dict))
                  else rec.get(c) for c in cols]
        live.execute(
            f"INSERT OR IGNORE INTO {table} ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})",
            values)
    live.commit()
    print(f"  完成。回查：mistakes={_cnt(live, 'mistakes')} knowledge={_cnt(live, 'knowledge')} "
          f"review_cards={_cnt(live, 'review_cards')} tutor_sessions={_cnt(live, 'tutor_sessions')}")
    live.close()
    print(f"== 完成。如恢复不合预期，可从快照 {snap} 无损回退 ==")
    return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute("SELECT 1 FROM " + table + " LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def _cnt(conn: sqlite3.Connection, table: str) -> int:
    if not _table_exists(conn, table):
        return 0
    return conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]


if __name__ == "__main__":
    sys.exit(main())
