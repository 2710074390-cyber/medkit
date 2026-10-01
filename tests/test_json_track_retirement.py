# -*- coding: utf-8 -*-
"""D6 迁移演练：JSON 轨 → SQLite 轨 → 回滚 → JSON 轨（**数据级**往返）。

## 为什么需要（2026-10-01）

ADR-006 §3/§4 定义了迁移路径与回滚方案，退役条件①还要求
「`db.import_from_json()` 已在启动路径幂等执行且全部表返回 `imported n` / `skip(done)`」。
但此前**只有**「脚本安全不变量」守卫（`test_rollback_json_track.py`：默认 dry-run / 不覆盖 /
用 rename / `_live_name` 解析），**没有**数据级往返演练——
「迁过去数据对不对」「滚回来字节等不等」「退役后回滚还可用吗」这三问**没有机械判据**。

本文件把 2026-10-01 的演练固化为回归用例，覆盖四问：
  ① 迁移无损？ ② 迁移幂等？ ③ 回滚真回 JSON 轨？ ④ 回滚字节无损？

## ⚠️ 一个实测踩到的前置条件（留档）

回滚脚本 docstring 写着「执行前必须关闭 MedKit」——**不是客套话**：本进程只要持有
`medkit.db` 句柄，`rename` 就报 `WinError 32`（2026-10-01 演练首跑即踩到，
脚本**安全失败**：`已完成的移动：[]`，什么都没动）。

且**当时 `db.reset_conn()` 只丢弃引用、不 `close`**（`_local.conn = None`）——
`sqlite3.Connection` 处在**引用环**里，引用计数不归零 ⇒ 句柄要等**循环 GC** 才释放，
`gc.collect()` 之前 `rename` 一律失败。**这已修**（`db.reset_conn()` 改为确定性关闭，
回归守卫 `tests/test_db.py::test_reset_conn_releases_db_file_handle`）。
本用例仍显式 `dbs.shutdown()`——它是**应用正常退出**走的那条路（`main._shutdown_runtime`），
与「执行前关闭 MedKit」这一运维前置最贴合。
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from medkit.core import db as dbs
from medkit.core import library as libmod

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pack" / "rollback-json-track.py"

# 夹具：按**表名**键（文件名从 `db.IMPORT_MAP` 反查，避免两处各写一份而漂移）。
# 记录同时覆盖「字段齐全」与「缺可选字段」两类，确保 `put_row` 的冗余列逻辑被真跑。
FIXTURES: dict[str, list[dict]] = {
    "mistakes": [
        {"id": "m_001", "subject": "儿科学", "chapter": "呼吸", "topic": "支气管肺炎",
         "state": "learning", "miss_count": 2, "learned": False,
         "created_at": "2026-09-01T10:00:00", "question": "患儿双肺闻及中细湿啰音",
         "my_reasoning": "只记得湿啰音", "error_tag": "机制混淆"},
        {"id": "m_002", "subject": "生理学", "chapter": "循环", "topic": "心输出量",
         "state": "relearning", "miss_count": 1, "learned": False,
         "created_at": "2026-09-02T11:30:00", "question": "前负荷增加时每搏量变化",
         "my_reasoning": "记反了", "error_tag": "记忆偏差"},
        {"id": "m_003", "subject": "药理学", "chapter": "总论", "topic": "首过效应",
         "state": "mastered", "miss_count": 0, "learned": True,
         "created_at": "2026-09-03T09:15:00", "question": "首过效应显著的给药途径"},
    ],
    "knowledge": [
        {"id": "k_001", "name": "支气管肺炎", "subject": "儿科学", "chapter": "呼吸",
         "state": "learning", "priority": 3, "score": 0.4, "attempts": 5,
         "last_tried": "2026-09-28T20:00:00"},
        {"id": "k_002", "name": "Frank-Starling 机制", "subject": "生理学", "chapter": "循环",
         "state": "relearning", "priority": 2, "score": 0.7, "attempts": 3,
         "last_tried": "2026-09-29T21:00:00"},
    ],
    "explains": [
        {"id": "e_001", "subject": "儿科学", "kp_name": "支气管肺炎",
         "created_at": "2026-09-20T14:00:00", "text": "固定湿啰音是肺炎体征"},
    ],
    "review_cards": [
        {"id": "r_001", "subject": "儿科学", "kp_name": "支气管肺炎", "state": "due",
         "due": "2026-10-02", "created_at": "2026-09-25T08:00:00"},
        {"id": "r_002", "subject": "生理学", "kp_name": "Frank-Starling 机制", "state": "new",
         "due": "2026-10-03", "created_at": "2026-09-26T08:00:00"},
    ],
    "tutor_sessions": [
        {"id": "t_001", "subject": "儿科学", "kp_name": "支气管肺炎", "state": "active",
         "updated_at": "2026-09-30T19:00:00"},
    ],
}


def _file_of(table: str) -> str:
    """表名 → JSON 文件名（真源 = `db.IMPORT_MAP`，不另写一份）。"""
    return dbs.IMPORT_MAP[table][0]


def _load_rollback():
    spec = importlib.util.spec_from_file_location("rollback_json_track_d6", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ 非空前提（防空转恒绿）

def test_fixtures_cover_every_import_map_table():
    """夹具必须覆盖 `db.IMPORT_MAP` 的**全部**表，且每表非空。

    两条都是**防空转**：
    - 判据用**结构相等**（`set(FIXTURES) == set(dbs.IMPORT_MAP)`），不是字面数字——
      `IMPORT_MAP` 新增表而夹具没跟上 ⇒ 红（否则新表静默漏测）；
    - 每表夹具非空 ⇒ 下面的往返断言不会在空集合上恒真。
    """
    assert set(FIXTURES) == set(dbs.IMPORT_MAP), (
        f"夹具与 db.IMPORT_MAP 的表集合不一致：夹具 {sorted(FIXTURES)} vs "
        f"真源 {sorted(dbs.IMPORT_MAP)}（新增/删除表了？）")
    for table, rows in FIXTURES.items():
        assert rows, f"{table} 夹具为空——往返断言会空转恒绿"


def test_import_map_filenames_are_distinct():
    """5 张表必须映射到 5 个**不同**的 JSON 文件（否则夹具会互相覆盖）。"""
    names = [_file_of(t) for t in FIXTURES]
    assert len(set(names)) == len(names), f"文件名重复：{names}"


# ------------------------------------------------------------------ 主演练

def test_json_track_round_trip_is_lossless(tmp_path, monkeypatch):
    """完整往返：JSON 轨 → 迁移 → SQLite 轨 → 回滚 → JSON 轨，逐表逐行 + 逐字节比对。"""
    store = tmp_path / "library"
    store.mkdir(parents=True)
    originals: dict[str, bytes] = {}
    for table, rows in FIXTURES.items():
        blob = json.dumps(rows, ensure_ascii=False, indent=2).encode("utf-8")
        (store / _file_of(table)).write_bytes(blob)
        originals[table] = blob

    # 起点：JSON 轨
    assert not dbs.enabled(), "起点应是 JSON 轨（db 不存在）"

    # ---------------------------------------------------------- ① 迁移
    assert dbs.migrate() >= 6, "user_version 应 >= 6（ADR-006 退役条件②）"
    assert dbs.enabled(), "迁移后应处于 SQL 轨"
    res = dbs.import_from_json()
    for table, rows in FIXTURES.items():
        assert res[table] == f"imported {len(rows)}", (
            f"{table} 导入状态异常：{res[table]!r}（期望 imported {len(rows)}）")

    # ---------------------------------------------------------- ① 无损（逐行比对 data 列）
    dbs.reset_conn()
    with dbs.tx(write=False) as cur:
        for table, rows in FIXTURES.items():
            want = {r["id"]: r for r in rows}
            got = {i: json.loads(d) for i, d in
                   cur.execute(f"SELECT id, data FROM {table}").fetchall()}
            assert set(got) == set(want), f"{table} id 集合不一致：{sorted(got)} vs {sorted(want)}"
            assert got == want, f"{table} 行内容不一致"

    # ---------------------------------------------------------- ② 幂等
    res2 = dbs.import_from_json()
    assert all(s == "skip(no file)" for s in res2.values()), (
        f"二次导入应全部 skip(no file)（原 JSON 已改名）：{res2}")
    with dbs.tx(write=False) as cur:
        counts = {t: cur.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in FIXTURES}
    assert counts == {t: len(r) for t, r in FIXTURES.items()}, (
        f"二次导入后行数变了（重复插入？）：{counts}")

    # ---------------------------------------------------------- ③ 回滚
    # ⚠️ 必须先释放 db 文件句柄（见模块 docstring）：`shutdown()` 是应用退出走的路。
    dbs.shutdown()
    mod = _load_rollback()
    monkeypatch.setattr(sys, "argv", ["prog", "--home", str(tmp_path), "--yes"])
    assert mod.main() == 0, "回滚脚本应成功返回 0"

    # ---------------------------------------------------------- ③ 盘面
    names = {p.name for p in store.iterdir()}
    assert "medkit.db" not in names, "medkit.db 未被移走（_store_is_sql 不会回落 JSON）"
    assert any(n.startswith("medkit.db.rollback-") for n in names), (
        f"db 文件未保留为 .rollback-<ts>（删除就不可反悔）：{sorted(names)}")

    # ---------------------------------------------------------- ④ 字节无损
    for table, blob in originals.items():
        p = store / _file_of(table)
        assert p.exists(), f"{table} 的 JSON 未恢复成活文件"
        got = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        exp = blob.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        assert got == exp, f"{table} 恢复出的内容与原始不一致（{len(got)} vs {len(exp)} 字节）"

    # ---------------------------------------------------------- ③ 轨判定
    dbs.reset_conn()
    assert not dbs.enabled(), "回滚后不应再处于 SQL 轨"
    assert not libmod._store_is_sql(libmod.MISTAKES_FILE), (
        "_store_is_sql 仍为真——域模块没回到 JSON 轨")
    assert len(libmod.list_mistakes()) == len(FIXTURES["mistakes"]), (
        "回滚后错题条数与原始不符")
