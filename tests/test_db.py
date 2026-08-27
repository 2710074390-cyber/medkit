"""core/db.py 存储底座测试（S0）：迁移幂等 / 回滚 / 备份 / 导入幂等 / 事务并发 / 行往返。

隔离：monkeypatch db.LIBRARY_DIR / db.DB_PATH 到临时目录 + reset_conn，不污染真实 ~/.medkit。
"""
from __future__ import annotations

import json
import threading

import pytest

from medkit.core import db

TABLES = ("mistakes", "knowledge", "explains", "review_cards",
          "tutor_sessions", "meta", "slices_fts")


@pytest.fixture
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "medkit.db")
    db.reset_conn()
    return tmp_path


def _tables() -> set[str]:
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','virtual')").fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_schema_and_version(iso):
    assert db.migrate() == 1
    assert db.user_version() == 1
    assert set(TABLES) <= _tables()
    assert db.enabled()
    # 幂等：重复迁移不报错、版本不变
    assert db.migrate() == 1


def test_migrate_backs_up_existing_library(iso):
    (iso / "mistakes.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
    (iso / "knowledge.json").write_text(json.dumps([], ensure_ascii=False), encoding="utf-8")
    db.migrate()
    baks = list(iso.glob("*.pre-db-*.bak"))
    assert len(baks) == 2  # 两个 JSON 均备份（首次迁移不备份空 db）
    assert all(b.name.endswith(".bak") for b in baks)


def test_downgrade_rollback(iso):
    db.migrate()
    assert db.user_version() == 1
    assert db.downgrade_to(0) == 0
    assert not (_tables() & set(TABLES))


def test_tx_rollback_on_error(iso):
    db.migrate()
    with pytest.raises(RuntimeError):
        with db.tx(write=True) as cur:
            db.put_row(cur, "mistakes", {"id": "m1", "question": "q"})
            raise RuntimeError("boom")
    with db.tx(write=True) as cur:
        assert db.list_rows(cur, "mistakes") == []


def test_row_roundtrip_dict(iso):
    db.migrate()
    rec = {"id": "m1", "subject": "儿科", "options": ["A", "B"],
           "know_tags": ["肺炎"], "score": 0.5, "nested": {"a": [1, 2]}}
    with db.tx(write=True) as cur:
        db.put_row(cur, "mistakes", rec, cols=("subject",))
    with db.tx(write=True) as cur:
        rows = db.list_rows(cur, "mistakes")
    assert rows[0] == rec
    assert rows[0]["options"] == ["A", "B"]        # JSON 嵌套无损


def test_concurrent_writes_no_lost_update(iso):
    db.migrate()
    with db.tx(write=True) as cur:
        cur.execute("CREATE TABLE cnt(name TEXT PRIMARY KEY, ev INTEGER NOT NULL)")
        cur.execute("INSERT INTO cnt VALUES ('kp', 0)")

    barrier = threading.Barrier(2)

    def increment():
        barrier.wait()
        for _ in range(50):
            with db.tx(write=True) as cur:
                cur.execute("UPDATE cnt SET ev = ev + 1 WHERE name = 'kp'")

    ts = [threading.Thread(target=increment) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    with db.tx(write=True) as cur:
        assert cur.execute("SELECT ev FROM cnt").fetchone()[0] == 100


def test_import_idempotent_and_renames(iso):
    db.migrate()
    recs = [{"id": "m1", "subject": "儿科", "question": "患儿 3 岁发热？"},
            {"id": "m2", "subject": "儿科", "question": "第二题"}]
    (iso / "mistakes.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    (iso / "knowledge.json").write_text(
        json.dumps([{"id": "kp1", "name": "肺炎", "score": 0.4}], ensure_ascii=False),
        encoding="utf-8")

    result = db.import_from_json()
    assert result["mistakes"].startswith("imported 2")
    assert result["knowledge"].startswith("imported 1")
    assert result["explains"] == "skip(no file)"

    # 原 JSON 已改名 → 不再当活数据；重跑不重复
    assert not (iso / "mistakes.json").exists()
    baks = list(iso.glob("mistakes.json.pre-db-import-*.bak"))
    assert len(baks) == 1                          # 导入改名（独立标签，不与迁移备份撞名）
    with db.tx(write=True) as cur:
        assert len(db.list_rows(cur, "mistakes")) == 2
        # 查询列随导入填充（S1 聚合依赖；data JSON 无损在 row_to_dict）
        assert [tuple(r) for r in cur.execute("SELECT DISTINCT subject FROM mistakes")] == [("儿科",)]
        assert db.list_rows(cur, "mistakes")[0]["question"] == "患儿 3 岁发热？"

    # meta 标记：即使 JSON 又出现也只跳过一次（幂等）
    (iso / "mistakes.json").write_text(json.dumps(recs, ensure_ascii=False), encoding="utf-8")
    result2 = db.import_from_json()
    assert result2["mistakes"] == "skip(done)"
    with db.tx(write=True) as cur:
        assert len(db.list_rows(cur, "mistakes")) == 2


def test_import_rejects_missing_ids(iso):
    db.migrate()
    (iso / "mistakes.json").write_text(
        json.dumps([{"question": "无 id 的记录"}], ensure_ascii=False), encoding="utf-8")
    result = db.import_from_json()
    assert result["mistakes"] == "skip(empty)"       # 无 id 不入库（保主键完整性）
