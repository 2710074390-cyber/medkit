"""S3-3 素材库复用回归测试：

会话 CRUD（隔离目录）/ 跨项目复用会话创建课题 / 多教材合并出题 quota 跨 session 加权。
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.main as m  # noqa: E402
from medkit.core import config as cfgmod  # noqa: E402

SLICES_A = [
    {"sid": "S001", "title": "第一章 生长发育", "text": "生长发育有三个高峰，出生体重3.25kg，1岁10kg。" * 20},
    {"sid": "S002", "title": "第二章 儿童营养", "text": "能量需求110kcal/kg，母乳SIgA。" * 15},
]
SLICES_B = [
    {"sid": "S001", "title": "第三章 儿童保健", "text": "计划免疫程序，接种禁忌。" * 18},
    {"sid": "S002", "title": "第四章 急性传染病", "text": "麻疹早期表现，Koplik斑。" * 12},
]
TEACHER = [{"sid": "T001", "title": "教师重点", "text": "生长发育 3.25kg 计划免疫 麻疹 辅食由少到多"}]


@pytest.fixture()
def iso(monkeypatch, tmp_path):
    saved = dict(cfgmod.DEFAULTS)
    saved["projects_dir"] = str(tmp_path / "projects")
    saved["api_key"] = "sk-test"
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(cfgmod, "PROMPTS_DIR_USER", tmp_path / "prompts")
    monkeypatch.setattr(cfgmod, "PRESETS_DIR", tmp_path / "presets")
    monkeypatch.setattr(m.cfg, "load", lambda: dict(saved))
    return saved


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_session_crud_roundtrip(iso):
    c = _client()
    r = c.post("/api/sessions", json={"name": "儿科学教材A", "role": "textbook",
                                      "source_name": "儿科学.pdf", "slices": SLICES_A})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert r.json()["slice_count"] == 2 and r.json()["chars"] > 0
    lst = c.get("/api/sessions").json()["sessions"]
    assert any(x["id"] == sid and x["role"] == "textbook" for x in lst)
    full = c.get(f"/api/sessions/{sid}").json()
    assert full["slices"][0]["text"].startswith("生长发育有三个高峰")
    assert c.delete(f"/api/sessions/{sid}").status_code == 200
    assert not any(x["id"] == sid for x in c.get("/api/sessions").json()["sessions"])
    # 路径穿越防护
    assert c.get("/api/sessions/..%2Fx").status_code in (400, 404)


def test_cross_project_reuse_session_create(iso):
    """跨项目复用：会话保存 → 取回切片 → 用其创建课题（quota 按章加权求和 == target）。"""
    c = _client()
    sid = c.post("/api/sessions", json={"name": "教材A", "role": "textbook",
                                        "slices": SLICES_A}).json()["id"]
    sess = c.get(f"/api/sessions/{sid}").json()
    body = {
        "subject": "儿科复用", "exam": "期末", "target": 20,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "teacher_text": TEACHER[0]["text"],
        "textbook_slices": sess["slices"],
        "teacher_slices": TEACHER,
        "exam_slices": [],
    }
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    quotalist = r.json()["quota"]
    assert sum(q["count"] for q in quotalist) == 20, "会话切片配额应合计 == target（按章加权）"
    assert {q["sid"] for q in quotalist} == {"S001", "S002"}, "跨项目复用不丢切片"


def test_multi_textbook_merge_quota(iso):
    """多教材合并：两个会话切片合并 → 创建课题，quota 跨 session 按章加权（总计 == target）。"""
    c = _client()
    merged = []
    for name, sl in (("教材A", SLICES_A), ("教材B", SLICES_B)):
        sid = c.post("/api/sessions", json={"name": name, "role": "textbook",
                                            "slices": sl}).json()["id"]
        merged += c.get(f"/api/sessions/{sid}").json()["slices"]
    assert len(merged) == 4
    body = {
        "subject": "多教材合并", "exam": "期末", "target": 30,
        "ratios": {"A1": 40, "A2": 30, "B1": 20, "X": 10},
        "toggles": {"qbank": True, "paper": True, "review": True},
        "teacher_text": TEACHER[0]["text"],
        "textbook_slices": merged,
        "teacher_slices": TEACHER,
        "exam_slices": [],
    }
    r = c.post("/api/projects", json=body)
    assert r.status_code == 200, r.text
    quotalist = r.json()["quota"]
    assert sum(q["count"] for q in quotalist) == 30
    assert len(quotalist) >= 3, "跨 session 的章节都应获得配额（按章加权）"
