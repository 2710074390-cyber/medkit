# -*- coding: utf-8 -*-
"""讲解产物存储与批量删除（`core/explain.py` + `POST /api/library/explains/batch-delete`）。

覆盖：
- **轨切换保护**（2026-10-10 补）：JSON 轨存了讲解后，别的动作建库（改判 SQL 轨）
  不得让讲解「刚存就查不到」——与 `library._backfill_json_once`（V-10）同一类缺陷；
- 批量删除：只删选中的；返回删除数 + 备份路径（删除可追溯，不静默不可恢复）；
- 级联清理由这些讲解派生的**医学记忆卡**（与单条删除 `explains_delete` 同口径）；
- 空选择 400；不存在的 id 不报错但 deleted=0；单次上限 500。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.core.cards as cardlib  # noqa: E402
import medkit.core.db as dbs  # noqa: E402
import medkit.core.explain as expl  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def _seed_explain(eid: str, kp: str = "支气管肺炎"):
    return expl.save_explain({
        "id": eid, "subject": "儿科学", "kp_name": kp, "kp_id": "",
        "created_at": "2026-10-10T00:00:00", "content": f"# {kp}\n正文",
        "sources": [], "via_web": False, "grounded": True,
    })


def _seed_card(source_eid: str):
    added, _ = cardlib.create_from_drafts(
        [{"kind": "concept", "front": "正面", "back": "背面"}],
        subject="儿科学", kp_name="支气管肺炎", source=source_eid)
    return added


def test_track_flip_does_not_hide_explains(client):
    """**轨切换保护**：JSON 轨存了讲解 → 别的动作建库（轨判定翻转）→ 讲解不得消失。

    这正是 `library._backfill_json_once`（V-10）那一类缺陷，2026-10-10 补到讲解域。

    证伪：把 `explain._load/_save/_store` 里的 `_sql_ready()` 换回 `_store_is_sql()`
    （即去掉 JSON→SQL 补导）→ 本用例必红（`list_explains()` 由 1 条变 0 条）。
    """
    _seed_explain("ex_flip")
    assert [e["id"] for e in expl.list_explains()] == ["ex_flip"]   # JSON 轨（db 尚不存在）

    dbs.migrate()          # 别的域按需建库 → `_store_is_sql()` 由 False 翻 True
    assert dbs.user_version() == dbs.MIGRATIONS[-1]
    assert [e["id"] for e in expl.list_explains()] == ["ex_flip"], \
        "轨切换后讲解必须仍可见（补导把 JSON 轨的活数据带进库）"
    assert [e["id"] for e in client.get("/api/library/explains").json()["explains"]] == ["ex_flip"]


def test_track_flip_then_new_write_and_delete_still_consistent(client):
    """轨切换后再写/删，读写仍一致（补导后的 SQL 轨是唯一真相）。"""
    _seed_explain("ex_a")
    dbs.migrate()
    _seed_explain("ex_b")
    ids = {e["id"] for e in expl.list_explains()}
    assert ids == {"ex_a", "ex_b"}, ids
    assert client.post("/api/library/explains/batch-delete", json={"ids": ["ex_a"]}).json()["deleted"] == 1
    assert [e["id"] for e in expl.list_explains()] == ["ex_b"]


def test_batch_delete_removes_selected_and_writes_backup(client):
    _seed_explain("ex_1")
    _seed_explain("ex_2", kp="心衰")
    _seed_explain("ex_3", kp="慢阻肺")

    r = client.post("/api/library/explains/batch-delete", json={"ids": ["ex_1", "ex_3"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["deleted"] == 2
    assert j["backup"] and Path(j["backup"]).is_file(), "批量删除必须先导出备份"

    left = [e["id"] for e in client.get("/api/library/explains").json()["explains"]]
    assert left == ["ex_2"]


def test_batch_delete_cascades_derived_memory_cards(client):
    _seed_explain("ex_a")
    _seed_explain("ex_b")
    assert len(_seed_card("ex_a")) == 1
    assert len(_seed_card("ex_b")) == 1
    assert len(cardlib.list_cards()) == 2

    j = client.post("/api/library/explains/batch-delete", json={"ids": ["ex_a"]}).json()
    assert j["deleted"] == 1 and j["cards_removed"] == 1
    remaining = [c["id"] for c in cardlib.list_cards()]
    assert len(remaining) == 1, "只应级联删除被删讲解派生的卡"


def test_batch_delete_empty_ids_is_400(client):
    assert client.post("/api/library/explains/batch-delete", json={"ids": []}).status_code == 400


def test_batch_delete_over_500_is_400(client):
    ids = [f"ex_{i}" for i in range(501)]
    assert client.post("/api/library/explains/batch-delete", json={"ids": ids}).status_code == 400


def test_batch_delete_unknown_ids_is_noop(client):
    _seed_explain("ex_keep")
    j = client.post("/api/library/explains/batch-delete", json={"ids": ["nope"]}).json()
    assert j["deleted"] == 0 and j["backup"] == ""
    assert [e["id"] for e in client.get("/api/library/explains").json()["explains"]] == ["ex_keep"]


def test_core_batch_delete_reports_counts_without_cards():
    """core 层只管自己的存储（不级联卡）——级联是 routers 的职责（core 之间不互相依赖）。"""
    _seed_explain("ex_x")
    _seed_explain("ex_y")
    res = expl.batch_delete_with_backup(["ex_x"])
    assert res["deleted"] == 1 and Path(res["backup"]).is_file()
    assert [e["id"] for e in expl.list_explains()] == ["ex_y"]
