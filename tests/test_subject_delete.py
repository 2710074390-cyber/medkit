"""WP-4：删除科目（自动备份 + 清理该科本地数据）单元测试。

覆盖 JSON 模式与 SQL 模式：删除 1 科后另两科保留、备份文件存在、计数正确、
路由端点返回 ok + deleted + backup。

2026-10-15（奥卡姆精简 T2）：复习卡 / 记忆卡 / 提问会话 / 讲解产物四域零使用被删，
本文件随之去掉这四路的造数与断言（`delete_subject_with_backup` 也已同步瘦身）；
保留错题 + 知识点两路，以及仍带 subject 列的大纲/真题考频表。
"""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """全模块存储常量隔离到 tmp（JSON 模式；SQL 模式由测试自行 migrate）。"""
    libd = tmp_path / "library"
    libd.mkdir()
    from medkit.core import config as cfgmod
    from medkit.core import db as dbs
    from medkit.core import library as lib

    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", libd)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    dbs.reset_conn()
    return {"tmp": tmp_path, "libd": libd}


def _seed() -> None:
    """三科各造：错题 + 知识点（知识点由错题 know_tags 派生）。"""
    from medkit.core import library as lib

    for i, subj in enumerate(["内科学", "外科学", "儿科学"], 1):
        lib.add_mistake({
            "source": "manual", "subject": subj, "chapter": "章", "question": f"{subj}错题{i}",
            "options": ["甲", "乙", "丙", "丁", "戊"], "answer": "A",
            "analysis": f"{subj}解析", "know_tags": [f"{subj}考点"], "miss_count": 1,
        })


def _assert_deleted(subj: str) -> None:
    from medkit.core import library as lib

    assert all(m.get("subject") != subj for m in lib.list_mistakes())
    assert all(k.get("subject") != subj for k in lib.list_knowledge())


def test_delete_subject_json_backup_and_counts(iso):
    from medkit.core import library as lib

    _seed()
    res = lib.delete_subject_with_backup("内科学")
    d = res["deleted"]
    assert d["mistakes"] == 1
    assert d["knowledge"] >= 1
    backup = Path(res["backup"])
    assert backup.exists(), res
    data = json.loads(backup.read_text(encoding="utf-8"))
    assert data["subject"] == "内科学"
    assert len(data["mistakes"]) == 1
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
