"""V-10：JSON 轨 → SQL 轨切换时的数据补导（P0 数据可见性守卫）。

**缺陷（实测必现）**：`_store_is_sql()` 只判断「`medkit.db` 是否存在」，而该 db 由**别的域**
（大纲管理 / 真题考频 / 记忆卡）按需 `migrate()` 建立；库域自己从不建库、也从不补导。
于是：

    新装 → 用错题本（JSON 轨）→ 去点一次「大纲管理」（建库）
         → 库域改判 SQL 轨 → 错题本/掌握度读空库 = 界面全空

数据没丢（仍在 `mistakes.json`），但界面完全不可见；此后新写入进 DB，形成真正的双轨分叉。

`db.import_from_json()`（按 id 幂等 + 导入后原 JSON 改名留档）本就是为这一步准备的，
只是从未被接线。本文件把「切换即补导」钉成回归网。
"""
from __future__ import annotations

import json

import pytest

from medkit.core import db as dbs
from medkit.core import library as lib


@pytest.fixture
def json_then_db(tmp_path, monkeypatch):
    """先把库域跑在 JSON 轨，再建库——复现真实的轨切换时序。"""
    monkeypatch.setattr(dbs, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(lib, "MISTAKES_FILE", tmp_path / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", tmp_path / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    dbs.reset_conn()
    return tmp_path


def _write_json_track(n: int = 3) -> None:
    for i in range(n):
        lib.add_mistake({"subject": "内科学", "chapter": "第1章",
                         "question": f"JSON 轨错题 {i}", "answer": "A",
                         "know_tags": [f"JSON轨知识点{i}"]})


def test_json_track_data_visible_after_db_created(json_then_db):
    """核心守卫：JSON 轨写下的数据，在别处建库之后**必须仍然可见**。"""
    assert not lib._store_is_sql(lib.MISTAKES_FILE), "前置：应处于 JSON 轨"
    _write_json_track(3)
    assert len(lib.list_mistakes()) == 3
    assert len(lib.list_knowledge()) == 3

    dbs.migrate()          # 模拟用户去点了「大纲管理 / 真题考频 / 记忆卡」
    assert lib._store_is_sql(lib.MISTAKES_FILE), "前置：建库后应切到 SQL 轨"

    assert len(lib.list_mistakes()) == 3, "切轨后 JSON 轨旧数据不可见（V-10 回归）"
    assert len(lib.list_knowledge()) == 3, "切轨后派生知识点不可见（V-10 回归）"
    assert lib.count_mistakes() == 3, "count_mistakes 也必须先补导再计数"


def test_backfill_is_idempotent(json_then_db):
    """反复访问不得重复导入（幂等由 id 键保证）。"""
    _write_json_track(2)
    dbs.migrate()
    for _ in range(3):
        assert len(lib.list_mistakes()) == 2
    with dbs.tx(write=False) as cur:
        assert cur.execute("SELECT COUNT(*) FROM mistakes").fetchone()[0] == 2


def test_backfill_keeps_json_as_backup(json_then_db):
    """补导成功后原 JSON 改名留档（内容原样保留，可回滚）。"""
    _write_json_track(2)
    dbs.migrate()
    lib.list_mistakes()          # 触发补导

    assert not (json_then_db / "mistakes.json").exists(), "原 JSON 应已改名"
    baks = list(json_then_db.glob("mistakes.json.pre-db-import-*.bak"))
    assert baks, f"未留下导入备份：{list(json_then_db.iterdir())}"
    assert len(json.loads(baks[0].read_text(encoding="utf-8"))) == 2, "备份内容应完整"


def test_backfill_failure_falls_back_to_json_track(json_then_db, monkeypatch):
    """补导失败时**退回 JSON 轨**——宁可暂时双轨，也不能让用户数据隐身。"""
    _write_json_track(2)
    dbs.migrate()

    def boom():
        raise RuntimeError("模拟补导失败")

    monkeypatch.setattr(dbs, "import_from_json", boom)
    assert len(lib.list_mistakes()) == 2, "补导失败后数据仍应可见（退回 JSON 轨）"
    assert not lib._sql_ready(lib.MISTAKES_FILE), "补导失败时不应判定为可走 SQL 轨"


def test_no_backfill_when_no_json(json_then_db):
    """无 JSON 轨数据时补导是零开销 no-op（新装/已补导后的常态路径）。"""
    dbs.migrate()
    assert lib._backfill_json_once() is True
    lib.add_mistake({"subject": "内科学", "question": "SQL 轨新题", "answer": "A",
                     "know_tags": ["SQL轨知识点"]})
    assert len(lib.list_mistakes()) == 1
    assert len(list(json_then_db.glob("*.json"))) == 0, "SQL 轨不应产生 JSON 活数据"


def test_wiring_guard():
    """结构守卫：库域的轨判定必须统一走 `_sql_ready`（不得再裸用 `_store_is_sql` 判定读写）。"""
    import inspect
    for fn in (lib._load, lib._save, lib._store, lib.count_mistakes):
        src = inspect.getsource(fn)
        assert "_sql_ready(" in src, f"{fn.__name__} 未走 _sql_ready（切轨会漏补导）"
    assert "import_from_json" in inspect.getsource(lib._backfill_json_once), \
        "_backfill_json_once 未接 db.import_from_json"
