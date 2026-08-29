"""WP-04 医学图像/表格题测试：字段解析 / 渲染（base64 图 + 安全表格）/ 资产上传闭环。"""
from __future__ import annotations

import base64

from medkit.agents.medgen import _parse_questions
from medkit.core.orchestrator import _gate_image_refs  # noqa: E402
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


def test_gate_image_refs_drops_hallucinated_without_images():
    """B28：未传图项目（image_sids 为空）的幻觉 image_ref 也要剔除并返回 id——不再放行。"""
    qs = [
        {"id": "Q1", "image_ref": "IMG1", "question": "q"},
        {"id": "Q2", "image_ref": "", "question": "q"},
        {"id": "Q3", "question": "q"},
    ]
    kept, dropped = _gate_image_refs(qs, set())
    assert dropped == ["Q1"], f"幻觉 image_ref 应被剔除：{dropped}"
    assert [q["id"] for q in kept] == ["Q2", "Q3"]


def test_gate_image_refs_keeps_valid_refs():
    """B28：有素材时，指向素材清单的 image_ref 放行，不匹配的仍剔除。"""
    qs = [{"id": "Q1", "image_ref": "IMG1"}, {"id": "Q2", "image_ref": "IMG9"}]
    kept, dropped = _gate_image_refs(qs, {"IMG1"})
    assert dropped == ["Q2"] and [q["id"] for q in kept] == ["Q1"]


def test_render_media_missing_or_empty_ref_graceful(tmp_path):
    # R3S-02：有 image_ref 但索引缺图 → 明确占位（不再静默消失）；空 ref 仍输出空
    out = qb.render_media({"image_ref": "IMG9"}, {"IMG1": {"path": str(tmp_path / "x")}})
    assert "图片索引缺失" in out and "IMG9" in out
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


def test_render_media_oversize_image_placeholder(tmp_path):
    """D9：超过内嵌上限且无法降采样的图 → 不塞进页面，给可读占位提示（题面保留）。"""
    p = tmp_path / "big.png"
    # 伪造超限 PNG（解码必失败 → 保持原图 → 仍超限 → 占位）
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * (1_301_000))
    out = qb.render_media({"image_ref": "IMG1"},
                          {"IMG1": {"path": str(p), "caption": "大图"}})
    assert "体积过大未嵌入本页" in out and "IMG1" in out
    assert "data:image" not in out


def test_qbank_pagination_by_questions_and_project_key():
    """D7：分页按题目数（每页 ≤50；案例组整组归属单页）；D8：筛选 key 按 pid 隔离。"""
    singles = [{"id": f"Q{i}", "type": "A1", "bloom": "理解", "subtopic": "章",
                "question": f"题{i}？", "options": ["A", "B", "C", "D", "E"],
                "answer": "A", "analysis": "解析 [源:切片S001]"} for i in range(40)]
    case = [{"id": "Qx", "type": "A4", "bloom": "应用", "subtopic": "案例",
             "group_kind": "case", "case_id": "C1", "case_stem": "一例发热患者…",
             "question": f"子题{i}？", "options": ["A", "B", "C", "D", "E"],
             "answer": "A", "analysis": "a"} for i in range(30)]
    html_ = qb.export_html(singles + case, "题库", pid="p1")
    # 40 单题 + 案例组 30 子题 → 40+30>50 → 案例组整组进第 2 页（共 2 页，组不拆散）
    assert html_.count('class="qpage"') == 2
    assert 'QB_PID="p1"' in html_
    assert '"medkitQbFilter-"' in html_          # key 前缀（运行时拼接 pid）
    html2 = qb.export_html(singles + case, "题库", pid="p2")
    assert 'QB_PID="p2"' in html2


def test_paper_answers_declaration_note():
    """D21：押题卷页顶明示答案内嵌源码、请勿用于正式考试。"""
    q = {"id": "Q1", "type": "A1", "bloom": "记忆", "subtopic": "x",
         "question": "q？", "options": ["A", "B", "C", "D", "E"],
         "answer": "A", "analysis": "a"}
    out = qb.export_paper_html([q], "押题卷", pid="p1", subject="儿科学")
    assert "请勿用于正式考试" in out
    assert "答案内嵌于本页源码" in out
