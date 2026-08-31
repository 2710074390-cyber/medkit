"""WP-5：错题本批量删除/标记/导出（后端单元测试）。

覆盖：批量删除前自动备份 + 幂等、批量标记已掌握、导出 json/md、
路由参数校验（空 ids / 非法格式）与正常返回。
"""

import json
from pathlib import Path

import pytest


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """JSON 态隔离：mistakes/knowledge 与配置全部指向 tmp。"""
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
    return tmp_path


def _mk(lib, subject: str, qid: str) -> str:
    return lib.add_mistake({
        "source": "manual", "subject": subject, "chapter": "呼吸系统",
        "question": qid + "题干", "options": ["甲", "乙", "丙", "丁", "戊"],
        "answer": "A", "analysis": qid + "解析", "know_tags": [qid], "miss_count": 1,
    })["id"]


def test_batch_delete_backup_and_idempotent(iso):
    from medkit.core import library as lib

    a = _mk(lib, "内科学", "A")
    b = _mk(lib, "内科学", "B")
    c = _mk(lib, "外科学", "C")
    res = lib.batch_delete_with_backup([a, b])
    assert res["deleted"] == 2
    assert Path(res["backup"]).exists()
    data = json.loads(Path(res["backup"]).read_text(encoding="utf-8"))
    assert len(data["mistakes"]) == 2 and set(data["ids"]) == {a, b}
    assert [m["id"] for m in lib.list_mistakes()] == [c]
    # 幂等：重复删返回 0 且不再产生新备份
    assert lib.batch_delete_with_backup([a])["deleted"] == 0


def test_batch_mark_learned(iso):
    from medkit.core import library as lib

    a = _mk(lib, "内科学", "A")
    b = _mk(lib, "内科学", "B")
    assert lib.batch_mark_learned([a, b], True) == 2
    by_id = {m["id"]: m for m in lib.list_mistakes()}
    assert by_id[a]["learned"] is True and by_id[b]["learned"] is True
    assert lib.batch_mark_learned([a], False) == 1
    by_id = {m["id"]: m for m in lib.list_mistakes()}
    assert by_id[a]["learned"] is False


def test_batch_export_json_and_md(iso):
    from medkit.core import library as lib

    a = _mk(lib, "内科学", "A")
    b = _mk(lib, "外科学", "B")
    j = lib.export_mistakes([a, b], "json")
    assert j["filename"].endswith(".json")
    parsed = json.loads(j["data"])
    assert len(parsed["mistakes"]) == 2
    md = lib.export_mistakes([a], "md")
    assert md["filename"].endswith(".md")
    assert "A题干" in md["data"] and "答案" in md["data"]


def test_batch_endpoints_validation(iso):
    from fastapi.testclient import TestClient

    import medkit.main as m
    from medkit.core import library as lib

    c = TestClient(m.app, base_url="http://127.0.0.1")
    assert c.post("/api/library/mistakes/batch-delete", json={"ids": []}).status_code == 400
    assert c.post("/api/library/mistakes/batch-export",
                  json={"ids": ["x"], "format": "doc"}).status_code == 400
    a = _mk(lib, "内科学", "A")
    r = c.post("/api/library/mistakes/batch-learn", json={"ids": [a], "learned": True})
    assert r.status_code == 200 and r.json()["updated"] == 1
