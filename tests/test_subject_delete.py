"""WP-4：删除科目（自动备份 + 清理错题/知识点/复习卡/记忆卡/提问会话/讲解）单元测试。

覆盖 JSON 模式与 SQL 模式：删除 1 科后另两科保留、备份文件存在、计数正确、
路由端点返回 ok + deleted + backup。
"""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """全模块存储常量隔离到 tmp（JSON 模式；SQL 模式由测试自行 migrate）。"""
    libd = tmp_path / "library"
    libd.mkdir()
    from medkit.core import cards as cardlib
    from medkit.core import config as cfgmod
    from medkit.core import db as dbs
    from medkit.core import explain as expl
    from medkit.core import library as lib
    from medkit.core import review as rev
    from medkit.core import tutor as tut

    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(rev, "LIBRARY_DIR", libd)
    monkeypatch.setattr(rev, "REVIEW_QUEUE_FILE", libd / "review_queue.json")
    monkeypatch.setattr(rev, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(cardlib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(cardlib, "CARDS_FILE", libd / "memory_cards.json")
    monkeypatch.setattr(cardlib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(tut, "LIBRARY_DIR", libd)
    monkeypatch.setattr(tut, "TUTOR_SESSIONS_FILE", libd / "tutor_sessions.json")
    monkeypatch.setattr(tut, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(expl, "LIBRARY_DIR", libd)
    monkeypatch.setattr(expl, "EXPLAINS_FILE", libd / "explains.json")
    monkeypatch.setattr(expl, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", libd)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    dbs.reset_conn()
    return {"tmp": tmp_path, "libd": libd}


def _seed() -> None:
    """三科各造：错题 + 知识点 + 复习卡 + 记忆卡 + 提问会话 + 讲解产物。"""
    from medkit.core import cards as cardlib
    from medkit.core import explain as expl
    from medkit.core import library as lib
    from medkit.core import review as rev
    from medkit.core import tutor as tut

    for i, subj in enumerate(["内科学", "外科学", "儿科学"], 1):
        lib.add_mistake({
            "source": "manual", "subject": subj, "chapter": "章", "question": f"{subj}错题{i}",
            "options": ["甲", "乙", "丙", "丁", "戊"], "answer": "A",
            "analysis": f"{subj}解析", "know_tags": [f"{subj}考点"], "miss_count": 1,
        })
        rev.enqueue(f"{subj}考点", subject=subj)
        cardlib.create_from_drafts(
            [{"kind": "value", "front": f"{subj}卡", "back": "k"}],
            subj, f"{subj}考点", f"src_{subj}")
        tut.start_session(subj, f"{subj}考点")
        expl.save_explain({
            "id": f"exp_{subj}", "subject": subj, "kp_name": f"{subj}考点",
            "content": f"{subj}讲解", "created_at": "2026-01-01T00:00:00",
        })


def _assert_deleted(subj: str) -> dict:
    from medkit.core import cards as cardlib
    from medkit.core import explain as expl
    from medkit.core import library as lib
    from medkit.core import review as rev
    from medkit.core import tutor as tut

    assert all(m.get("subject") != subj for m in lib.list_mistakes())
    assert all(k.get("subject") != subj for k in lib.list_knowledge())
    assert all(c.get("subject") != subj for c in rev.list_cards())
    assert all(c.get("subject") != subj for c in cardlib.list_cards())
    assert all(s.get("subject") != subj for s in tut.list_sessions())
    assert all(e.get("subject") != subj for e in expl.list_explains())


def test_delete_subject_json_backup_and_counts(iso):
    from medkit.core import library as lib

    _seed()
    res = lib.delete_subject_with_backup("内科学")
    d = res["deleted"]
    assert d["mistakes"] == 1
    assert d["knowledge"] >= 1
    assert d["review_cards"] == 1
    assert d["memory_cards"] == 1
    assert d["sessions"] == 1
    assert d["explains"] == 1
    backup = Path(res["backup"])
    assert backup.exists(), res
    data = json.loads(backup.read_text(encoding="utf-8"))
    assert data["subject"] == "内科学"
    assert len(data["mistakes"]) == 1 and len(data["tutor_sessions"]) == 1
    # 另两科保留
    subs = {m["subject"] for m in lib.list_mistakes()}
    assert "内科学" not in subs and {"外科学", "儿科学"} <= subs
    _assert_deleted("内科学")


def test_delete_subject_sql_branch(iso):
    from medkit.core import db as dbs
    from medkit.core import library as lib

    dbs.migrate()
    _seed()
    res = lib.delete_subject_with_backup("外科学")
    d = res["deleted"]
    assert d["mistakes"] == 1 and d["knowledge"] >= 1
    assert d["review_cards"] == 1 and d["memory_cards"] == 1
    assert d["sessions"] == 1 and d["explains"] == 1
    assert Path(res["backup"]).exists()
    _assert_deleted("外科学")
    assert {m["subject"] for m in lib.list_mistakes()} == {"内科学", "儿科学"}


def test_subject_delete_route(iso):
    from fastapi.testclient import TestClient

    import medkit.main as m

    _seed()
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/subjects/delete", json={"subject": "儿科学"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["deleted"]["mistakes"] == 1
    assert "backup" in data and Path(data["backup"]).exists()
    _assert_deleted("儿科学")
