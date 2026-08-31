"""WP-10：大纲管理重构（AI 结构化 round-trip / 失败保留原文 / 角色过滤）单元测试。"""

import pytest
from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import config as cfgmod
from medkit.core import db as dbs
from medkit.core import syllabus as syl

TEXT = """一、生理学
1、细胞的基本功能
二、生物化学
1、蛋白质的结构与功能
"""


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(dbs, "LIBRARY_DIR", tmp_path / "library")
    monkeypatch.setattr(dbs, "DB_PATH", tmp_path / "library" / "medkit.db")
    monkeypatch.setattr(syl, "SEED_FILE", tmp_path / "missing_seed.json")
    dbs.reset_conn()
    return tmp_path


def _client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def _outline():
    return {"exam": "", "subjects": [
        {"name": "生理学", "chapters": [
            {"name": "细胞的基本功能", "items": ["细胞的基本功能"]}]},
        {"name": "生物化学", "chapters": [
            {"name": "蛋白质的结构与功能", "items": ["蛋白质的结构与功能"]}]},
    ], "errors": []}


def test_structurize_roundtrip_and_original_store(iso, monkeypatch):
    """R4-05：完整性通过 → 幂等确立为官方大纲（source=seed）并有可回读出口。"""
    monkeypatch.setattr(syl, "extract_outline", lambda text, client=None: _outline())
    c = _client()
    r = c.post("/api/syllabus/outline/structurize",
               json={"text": TEXT, "subject": ""})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True, data
    assert data["diff"]["structured_items"] == 2
    assert data["original_path"] and __import__("pathlib").Path(data["original_path"]).exists()
    assert "通过完整性校验" in data["note"]
    # R4-05：付费产物自动落库为官方大纲，可回读；幂等再 structurize 不重复加
    assert data["source"] == "seed" and data["added"] == 2, data
    assert "细胞的基本功能" in syl.chapter_items_text("生理学", source="seed")
    r2 = c.post("/api/syllabus/outline/structurize",
                json={"text": TEXT, "subject": ""})
    assert r2.json()["added"] == 0, "重复确立官方大纲应幂等（不重复新增）"


def test_structurize_failure_keeps_original(iso, monkeypatch):
    monkeypatch.setattr(syl, "extract_outline", lambda text, client=None: None)
    c = _client()
    r = c.post("/api/syllabus/outline/structurize",
               json={"text": TEXT, "subject": ""})
    data = r.json()
    assert data["ok"] is False and data["structured"] is None
    assert __import__("pathlib").Path(data["original_path"]).exists(), "失败也应保留原文"


def test_chapter_items_text_source_filter(iso):
    dbs.migrate()
    drafts = [{"subject": "生理学", "chapter": "细胞", "item": "细胞功能"}]
    syl.add_seed_items(drafts)
    syl.add_teacher_items(drafts)
    teacher = syl.chapter_items_text("生理学", source="teacher")
    seed = syl.chapter_items_text("生理学", source="seed")
    allt = syl.chapter_items_text("生理学", source="all")
    assert "细胞功能" in teacher and "细胞功能" in seed and "细胞功能" in allt
