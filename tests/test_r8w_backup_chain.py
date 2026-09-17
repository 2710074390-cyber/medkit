"""B2 回归（R8+W 修复批次）：备份/恢复链必须真实可用。

覆盖：
- **S2-15 / S2-26**：迁移前 DB 备份必须是**一致性快照**且**校验可读**——
  WAL 未合并时直接 `copy2` 主库会得到「缺表」的库（W1 E2E §C 实证），改用 SQLite backup API。
- **S2-27**：任一关键文件备份失败 → `migrate()` 必须**中止**，不得裸奔执行不可逆结构变更。
- **S2-13**：清空数据不得恒报成功——删不掉的项要如实回 `failed`；`exports/backups`（清空前
  刚生成的自动备份）必须保留，否则等于亲手毁掉安全网。
- **S2-14**：备份必须包含 `sessions/`。
- **S3-18**：核心备份必须带上 `.pre-db-*.bak` 回滚点，但跳过 `.corrupt-*` 与 `-wal/-shm`。
"""

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest

from medkit.core import db
from medkit.routers import data as data_router


@pytest.fixture
def iso(tmp_path, monkeypatch):
    """把 db 的库目录指到 tmp（与 tests/test_db.py 同款隔离）。"""
    monkeypatch.setattr(db, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "medkit.db")
    db.reset_conn()
    return tmp_path


# ---------------------------------------------------------------- S2-15 / S2-26

def test_backup_snapshot_reads_data_still_in_wal(tmp_path):
    """WAL 未合并时：copy2 主库读不回数据，backup API 快照必须读得回。"""
    live = tmp_path / "medkit.db"
    conn = sqlite3.connect(str(live))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")   # 关键：禁止自动合并，数据留在 -wal
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()

        # 反例：只拷主库（连接仍开着 → WAL 未回写）→ 快照缺表
        naive = tmp_path / "naive.db"
        shutil.copy2(live, naive)
        with pytest.raises(sqlite3.Error):
            sqlite3.connect(str(naive)).execute("SELECT x FROM t").fetchone()

        # 正解：backup API 快照必须完整可读
        snap = tmp_path / "snap.db"
        db._backup_db_snapshot(live, snap)
        got = sqlite3.connect(str(snap)).execute("SELECT x FROM t").fetchone()
        assert got is not None and got[0] == 1, "一致性快照必须包含 WAL 中的已提交数据"
    finally:
        conn.close()


def test_backup_snapshot_rejects_non_database(tmp_path):
    """非 SQLite 文件不得产出「看起来成功」的备份。"""
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a sqlite database" * 10)
    with pytest.raises(sqlite3.Error):
        db._backup_db_snapshot(junk, tmp_path / "out.db")


def test_migrate_backup_is_consistent_snapshot(iso):
    """**接线级**：走真实迁移路径产出的 `.pre-db-*.bak` 必须是可读的一致快照。

    这条守的是「`_backup_before_migrate` 真的用了快照通道」，而不只是「快照函数本身能跑」——
    只测 helper 会漏掉「调用点被换回 copy2」这类回归（2026-09-17 反向验证实测踩到过）。
    """
    live = iso / "medkit.db"
    (iso / "mistakes.json").write_text(json.dumps([]), encoding="utf-8")
    conn = sqlite3.connect(str(live))
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")   # 数据留在 -wal，主库不含
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (7)")
        conn.commit()

        db._backup_before_migrate(include_db=True)

        baks = list(iso.glob("medkit.db.pre-db-*.bak"))
        assert baks, "迁移前必须留下 db 回滚点"
        got = sqlite3.connect(str(baks[0])).execute("SELECT x FROM t").fetchone()
        assert got is not None and got[0] == 7, \
            "回滚点必须包含 WAL 中的已提交数据（copy2 主库会缺表）"
    finally:
        conn.close()


# ---------------------------------------------------------------- S2-27

def test_migrate_aborts_when_backup_fails(iso, monkeypatch):
    """备份失败 → migrate() 抛 BackupError，且**不执行**任何迁移。"""
    (iso / "mistakes.json").write_text(json.dumps([]), encoding="utf-8")

    def _boom(p, tag, ts):
        db._errs.record("db.backup", f"模拟备份 {p.name} 失败")
        return None

    monkeypatch.setattr(db, "_backup_one", _boom)
    with pytest.raises(db.BackupError):
        db.migrate()
    # 迁移确实没跑：版本仍是 0，且没有建出任何表
    conn = sqlite3.connect(str(iso / "medkit.db"))
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "mistakes" not in names, "备份失败却仍然建了表 = 裸奔升级"
    finally:
        conn.close()


def test_migrate_succeeds_when_backup_ok(iso):
    """对照组：备份正常时迁移照常完成（确认上面的中止不是「永久坏掉」）。"""
    (iso / "mistakes.json").write_text(json.dumps([]), encoding="utf-8")
    assert db.migrate() == db.MIGRATIONS[-1]
    assert list(iso.glob("mistakes.json.pre-db-*.bak")), "首次迁移应备份活 JSON"


# ---------------------------------------------------------------- S2-13

def test_clear_reports_partial_when_unlink_fails(tmp_path, monkeypatch):
    """删不掉的项必须如实回报，不得恒报成功；且 exports/backups 必须保留。"""
    monkeypatch.setattr(data_router.cfg, "CONFIG_DIR", tmp_path, raising=False)
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "mistakes.json").write_text("[]", encoding="utf-8")
    (tmp_path / "library" / "keepme.json").write_text("[]", encoding="utf-8")
    bdir = tmp_path / "exports" / "backups"
    bdir.mkdir(parents=True)
    (bdir / "medkit-backup-20260917-120000-full.zip").write_bytes(b"zip")

    real_unlink = Path.unlink

    def _flaky(self, *a, **kw):
        if self.name == "keepme.json":
            raise OSError(32, "另一个程序正在使用此文件")   # 模拟 WinError 32 占用
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _flaky)
    removed, failed = data_router._clear_all_data()

    assert failed, "被占用的文件必须出现在 failed 里"
    assert any("keepme.json" in f for f in failed)
    assert not (tmp_path / "library" / "mistakes.json").exists(), "能删的应当删掉"
    assert (bdir / "medkit-backup-20260917-120000-full.zip").exists(), \
        "exports/backups 是清空前的安全网，必须保留"


def test_clear_endpoint_reports_partial(tmp_path, monkeypatch):
    """端到端：删不掉的项必须让接口回 `ok=false`——不得恒 `ok=true`（S2-13 的可见契约）。"""
    from fastapi.testclient import TestClient

    from medkit.main import app

    monkeypatch.setattr(data_router.cfg, "CONFIG_DIR", tmp_path, raising=False)
    (tmp_path / "library").mkdir()
    (tmp_path / "library" / "keepme.json").write_text("[]", encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps({"projects_dir": str(tmp_path / "projects")}), encoding="utf-8")

    real_unlink = Path.unlink

    def _flaky(self, *a, **kw):
        if self.name == "keepme.json":
            raise OSError(32, "另一个程序正在使用此文件")
        return real_unlink(self, *a, **kw)

    monkeypatch.setattr(Path, "unlink", _flaky)
    r = TestClient(app, base_url="http://127.0.0.1").post(
        "/api/data/clear", json={"confirm": "清空全部数据"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is False, "删不掉却回 ok=true = 谎报成功（S2-13 回归）"
    assert body["failed"], "必须列出未能删除的项"
    assert "keepme.json" in "".join(body["failed"])


# ---------------------------------------------------------------- S2-14 / S3-18

def _fake_backup_inputs(tmp_path, monkeypatch):
    monkeypatch.setattr(data_router.cfg, "CONFIG_DIR", tmp_path, raising=False)
    (tmp_path / "library").mkdir(parents=True, exist_ok=True)
    (tmp_path / "library" / "mistakes.json").write_text("[]", encoding="utf-8")
    (tmp_path / "library" / "medkit.db.pre-db-20260917-120655.bak").write_bytes(b"rollback")
    (tmp_path / "library" / "medkit.db.corrupt-1758000000.bak").write_bytes(b"corrupt")
    (tmp_path / "library" / "medkit.db-wal").write_bytes(b"wal")
    (tmp_path / "sessions").mkdir(exist_ok=True)
    (tmp_path / "sessions" / "s1.json").write_text("{}", encoding="utf-8")


def test_core_backup_includes_sessions_and_rollback_points(tmp_path, monkeypatch):
    _fake_backup_inputs(tmp_path, monkeypatch)
    path, _size = data_router._build_backup_zip(include_projects=False)
    with zipfile.ZipFile(path) as zf:
        names = set(zf.namelist())
    assert "sessions/s1.json" in names, "S2-14：核心备份必须含 sessions/"
    assert "library/medkit.db.pre-db-20260917-120655.bak" in names, \
        "S3-18：核心备份必须带回滚点"
    assert not any(".corrupt-" in n for n in names), "损坏改名 bak 无恢复价值，应跳过"
    assert not any(n.endswith(("-wal", "-shm")) for n in names), "WAL/SHM 是产生中的文件，应跳过"
