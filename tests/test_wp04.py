"""WP-04 医学图像/表格题测试：字段解析 / 渲染（base64 图 + 安全表格）/ 资产上传闭环。"""
from __future__ import annotations

import base64

from medkit.agents.medgen import _parse_questions
from medkit.render import qbank_html as qb

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def test_parse_questions_keeps_image_fields():
    data = {"questions": [{"question": "q", "type": "A1", "image_ref": "IMG1",
                           "data_table": "| a | b |\n|---|---|\n| 1 | 2 |"}]}
    out = _parse_questions(data, {"sid": "S001", "title": "t"})
    assert out[0]["image_ref"] == "IMG1"
    assert "| a | b |" in out[0]["data_table"]


def test_render_media_image_base64(tmp_path):
    p = tmp_path / "fig.png"
    p.write_bytes(PNG)
    html_ = qb.render_media({"image_ref": "IMG1"},
                            {"IMG1": {"path": str(p), "caption": "心电图"}})
    assert "data:image/png;base64," in html_
    assert "<figure" in html_ and "心电图" in html_


def test_render_media_missing_or_empty_ref_graceful(tmp_path):
    assert qb.render_media({"image_ref": "IMG9"}, {"IMG1": {"path": str(tmp_path / "x")}}) == ""
    assert qb.render_media({"image_ref": ""}, {"IMG1": {"path": str(tmp_path / "x")}}) == ""


def test_render_media_table_and_sanitize():
    q = {"data_table": "| 项目 | 数值 |\n|---|---|\n| PaO2 | 60 |"}
    out = qb.render_media(q, {})
    assert "<table" in out and "PaO2" in out
    out2 = qb.render_media(
        {"data_table": "| a |\n|---|\n| <script>alert(1)</script> |"}, {})
    assert "<script" not in out2.lower()


def test_export_html_with_image_and_table(tmp_path):
    p = tmp_path / "fig.png"
    p.write_bytes(PNG)
    qs = [{"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "心梗",
           "question": "如图所示，最可能的诊断？", "options": ["A", "B", "C", "D", "E"],
           "answer": "A", "analysis": "解析 [源:切片S001]",
           "image_ref": "IMG1", "data_table": "| a | b |\n|---|---|\n| 1 | 2 |"}]
    html_ = qb.export_html(qs, "测试题库",
                           image_index={"IMG1": {"path": str(p), "caption": "心电图"}})
    assert "data:image/png;base64," in html_
    assert "<table" in html_ and "图 IMG1" in html_


def test_export_paper_contains_media(tmp_path):
    p = tmp_path / "fig.png"
    p.write_bytes(PNG)
    qs = [{"id": "Q1", "type": "A1", "bloom": "记忆", "subtopic": "x",
           "question": "如图所示？", "options": ["A", "B", "C", "D", "E"],
           "answer": "A", "analysis": "a", "image_ref": "IMG1"}]
    html_ = qb.export_paper_html(qs, "测试卷",
                                 image_index={"IMG1": {"path": str(p), "caption": "c"}})
    assert "data:image/png;base64," in html_
    assert "q.media" in html_          # JS 渲染接入点存在


def test_asset_upload_list_delete(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from medkit import main as m
    from medkit.core import config as cfg

    p_root = tmp_path / "projects"
    (p_root / "demo").mkdir(parents=True)
    real = cfg.load()
    monkeypatch.setattr(cfg, "load",
                        lambda: {**real, "projects_dir": str(p_root)})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/projects/demo/assets",
               files={"file": ("ecg.png", PNG, "image/png")},
               data={"caption": "心电图 样例"})
    assert r.status_code == 200 and r.json()["sid"] == "IMG1"
    r2 = c.get("/api/projects/demo/assets")
    assert r2.status_code == 200 and r2.json()["assets"][0]["sid"] == "IMG1"
    assert (p_root / "demo" / "assets" / "fig_1.png").exists()
    r3 = c.get("/api/projects/demo/assets/IMG1")
    assert r3.status_code == 200 and r3.headers["content-type"].startswith("image/png")
    r4 = c.delete("/api/projects/demo/assets/IMG1")
    assert r4.status_code == 200 and not (p_root / "demo" / "assets" / "fig_1.png").exists()
