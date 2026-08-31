"""WP-11：外部站点做题数据导入（幂等去重 / 更新 / 跳过 / 路由）单元测试。"""

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import db as dbs
from medkit.core import library as lib


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(lib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(lib, "MISTAKES_FILE", libd / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", libd / "knowledge.json")
    monkeypatch.setattr(lib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", libd)
    monkeypatch.setattr(dbs, "DB_PATH", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    dbs.reset_conn()
    return libd


def _items():
    return [
        {"subject": "儿科学", "chapter": "呼吸", "question": "支气管炎首选治疗？",
         "options": ["A", "B"], "answer": "A", "analysis": "解析一", "tags": ["支气管炎"]},
        {"subject": "儿科学", "chapter": "呼吸", "question": "哮喘首选用药？",
         "options": ["A", "B"], "answer": "B", "analysis": "解析二"},
        {"subject": "", "chapter": "", "question": "", "answer": "A"},
    ]


def test_import_site_idempotent_and_update(iso):
    st = lib.import_site_items(_items())
    assert st["added"] == 2, st
    assert st["skipped"] == 1, st
    assert len(lib.list_mistakes()) == 2
    # 重复导入同 question+subject+chapter → 更新（不新增）
    st2 = lib.import_site_items([{**_items()[0], "answer": "C"}])
    assert st2["added"] == 0 and st2["updated"] >= 1, st2
    rec = next(mm for mm in lib.list_mistakes() if "支气管炎" in mm["question"])
    assert rec["answer"] == "C"
    assert rec["source"] == "site"


def test_import_export_route(iso):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/mistakes/import-export", json={"items": _items()})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True and data["added"] == 2 and data["skipped"] == 1
    assert len(data["errors"]) == 1
    # 空 items → 400
    assert c.post("/api/library/mistakes/import-export", json={"items": []}).status_code == 400
