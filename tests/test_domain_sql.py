"""review/explain/tutor 三域模块 SQL 模式端到端（S0·方案 §2.3）。

验证：公共 API 在 SQL 模式下行为与 JSON 完全一致（签名零改动、routers 零改动），
并发 enqueue 无丢失更新（K5 修复覆盖到全部学习库域模块）。
隔离：monkeypatch core.db + 各模块文件常量到临时目录 + reset_conn。
"""
from __future__ import annotations

import threading

import pytest

from medkit.core import db as dbs
from medkit.core import explain as expl
from medkit.core import library as lib
from medkit.core import review as rev
from medkit.core import tutor as tut


@pytest.fixture
def sql_iso(tmp_path, monkeypatch):
    monkeypatch.setattr(dbs, "LIBRARY_DIR", tmp_path)
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "DB_FILE", tmp_path / "medkit.db")
    monkeypatch.setattr(lib, "MISTAKES_FILE", tmp_path / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", tmp_path / "knowledge.json")
    monkeypatch.setattr(rev, "DB_FILE", tmp_path / "medkit.db")
    monkeypatch.setattr(rev, "REVIEW_QUEUE_FILE", tmp_path / "review_queue.json")
    monkeypatch.setattr(expl, "DB_FILE", tmp_path / "medkit.db")
    monkeypatch.setattr(expl, "EXPLAINS_FILE", tmp_path / "explains.json")
    monkeypatch.setattr(expl, "SLICE_INDEX_FILE", tmp_path / "slice_index.json")
    monkeypatch.setattr(tut, "DB_FILE", tmp_path / "medkit.db")
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", tmp_path / "tutor_sessions.json")
    dbs.reset_conn()
    dbs.migrate()
    return tmp_path


def test_review_sql_enqueue_grade_flow(sql_iso):
    card = rev.enqueue("肺炎", "儿科", kp_id="kp1")
    assert card["state"] == "new" and card["due"]
    got = rev.grade(card["id"], 4)
    assert got["reps"] == 1 and got["state"] == "learning"
    assert rev.get_card(card["id"])["reps"] == 1
    assert rev.stats()["total"] == 1
    assert rev.list_cards("儿科")[0]["id"] == card["id"]
    assert rev.delete_card(card["id"]) and rev.stats()["total"] == 0


def test_review_sql_concurrent_enqueue_no_loss(sql_iso):
    barrier = threading.Barrier(2)

    def flood(tag: int):
        barrier.wait()
        for j in range(50):
            rev.enqueue(f"kp_{tag}_{j}", "内科")

    ts = [threading.Thread(target=flood, args=(t,)) for t in (0, 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert rev.stats()["total"] == 100           # 无一丢失


def test_explain_sql_crud(sql_iso):
    rec = {"id": "e1", "kp_name": "肺炎", "subject": "儿科",
           "content": "# 肺炎讲解", "created_at": "2026-08-27T10:00:00"}
    assert expl.save_explain(rec)["id"] == "e1"
    assert expl.list_explains()[0]["content"] == "# 肺炎讲解"
    assert expl.list_explains("儿科")[0]["id"] == "e1"
    assert expl.list_explains("外科") == []
    assert expl.get_explain("e1")["kp_name"] == "肺炎"
    assert expl.delete_explain("e1") and expl.list_explains() == []
    assert not expl.delete_explain("e1")


def test_tutor_sql_session_flow(sql_iso):
    s = tut.start_session("儿科", "肺炎")
    assert s["id"].startswith("tu_")
    assert tut.seed_first(s["id"], "explain", "什么是肺炎？")
    s2 = tut.get_session(s["id"])
    assert s2["current"]["text"] == "什么是肺炎？"
    s3 = tut.record_answer(s["id"], "由病原体引起的…", 3, "", "继续追问")
    assert s3["rounds"][0]["score"] == 3 and s3["streak"] == 1
    assert tut.list_sessions("儿科")[0]["id"] == s["id"]
    assert tut.delete_session(s["id"]) and tut.list_sessions() == []
