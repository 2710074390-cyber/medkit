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


def test_export_paper_xss_escaped_all_fields():
    """B-11：押题卷全字段统一转义——LLM 产出/审核台改题文本不可注入：
    ① DOM 拼接层出现转义形态（noscript 列表 / casebar data-case）；
    ② 脚本数据层不得出现「</script> 逃逸序列」（`</` → `<\\/` 替换生效）。"""
    payload = '<script>alert(1)</script>'
    brk = '</script><script>alert(9)</script>'
    q = {"id": "Q001", "type": "A1", "bloom": "记忆",
         "question": "最可能的诊断是？" + brk,
         "options": ["<b>甲</b>" + payload, "乙", "丙", "丁", "戊"],
         "answer": "A", "analysis": "解析" + payload,
         "case_id": 'c"><img src=x onerror=alert(2)>', "case_label": "案例" + payload,
         "source_year": "20" + payload, "subtopic": "考点" + payload}
    html_ = qb.export_paper_html([q], "押题卷Test", pid="p1")
    # ① noscript 静态列表：题干/答案转义形态（DOM 层安全；运行时渲染层同样走 esc()）
    assert "&lt;/script&gt;&lt;script&gt;alert(9)&lt;/script&gt;" in html_, "noscript 应保留转义后的题干"
    # ② 脚本数据层：'</script>' 逃逸序列（`</` → `<\/`）——原始序列不得完整出现
    assert "</script><script>alert(9)" not in html_, "脚本数据层不得出现可逃逸的 </script> 序列"
    assert "<\\/script>" in html_, "`</` 应替换为 `<\\/`（JSON 内嵌防御）"
    # ③ 首页 title 转义
    assert "<title>押题卷Test · MedKit</title>" in html_


def test_export_paper_filters_no_option_questions():
    """B-17：判分后改题/重渲染带入无选项题 → 页面数据剔除（JS 侧另有防御过滤），
    仅提示区保留题号说明。"""
    bad = {"id": "Q009", "type": "A1", "bloom": "记忆", "subtopic": "x",
           "question": "无选项题？", "options": [], "answer": "A", "analysis": "a"}
    html_ = qb.export_paper_html([bad, {"id": "Q001", "type": "A1", "bloom": "记忆", "subtopic": "x",
                                        "question": "正常题？", "options": ["A", "B", "C", "D", "E"],
                                        "answer": "A", "analysis": "a"}],
                                 "押题卷", pid="p1")
    data_chunk = html_.split('let QUESTIONS = (', 1)[-1].split('];', 1)[0]
    assert '"Q009"' not in data_chunk, "无选项题不应出现在页面数据（Q009 仅在提示区）"
    assert '"Q001"' in data_chunk, "正常题应在页面数据中"
    assert "已从本卷剔除" in html_ and "Q009" in html_, "应提示剔除了无选项题（含题号）"


def test_asset_upload_size_limit(tmp_path, monkeypatch):
    """R4-06：上传超限 → 400，且不落盘/不进切片索引（体积限界前置）。"""
    from fastapi.testclient import TestClient

    from medkit import main as m
    from medkit.core import config as cfg
    from medkit.routers import projects as proj

    p_root = tmp_path / "projects"
    (p_root / "demo").mkdir(parents=True)
    real = cfg.load()
    monkeypatch.setattr(cfg, "load", lambda: {**real, "projects_dir": str(p_root)})
    monkeypatch.setattr(proj, "_MAX_ASSET_BYTES", 8)  # 压到 8 字节模拟超限
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/projects/demo/assets",
               files={"file": ("big.png", PNG, "image/png")},
               data={"caption": "超限图"})
    assert r.status_code == 400, r.text
    assert "图片过大" in r.json()["detail"], r.text
    assert not (p_root / "demo" / "assets").exists(), "超限上传不应落盘"
    assert not (p_root / "demo" / "slices.json").exists(), "超限上传不应进切片索引"


def test_asset_dir_total_quota(tmp_path, monkeypatch):
    """R5-B-01：assets 累计容量超限 → 400 + 附当前占用/上限；且零磁盘副作用（不建目录/不改索引）。"""
    from fastapi.testclient import TestClient

    from medkit import main as m
    from medkit.core import config as cfg
    from medkit.routers import projects as proj

    p_root = tmp_path / "projects"
    (p_root / "demo").mkdir(parents=True)
    real = cfg.load()
    monkeypatch.setattr(cfg, "load", lambda: {**real, "projects_dir": str(p_root)})
    # 累计上限压到 PNG 大小 + 2（第一张可入，第二张累计超限）
    monkeypatch.setattr(proj, "_MAX_ASSETS_TOTAL", len(PNG) + 2)
    c = TestClient(m.app, base_url="http://127.0.0.1")

    r1 = c.post("/api/projects/demo/assets",
                files={"file": ("a.png", PNG, "image.png")}, data={"caption": "图1"})
    assert r1.status_code == 200, r1.text
    assert (p_root / "demo" / "assets" / "fig_1.png").exists()
    before = (p_root / "demo" / "slices.json").read_text(encoding="utf-8")

    r2 = c.post("/api/projects/demo/assets",
                files={"file": ("b.png", PNG, "image.png")}, data={"caption": "图2"})
    assert r2.status_code == 400, r2.text
    detail = r2.json()["detail"]
    assert "总容量超限" in detail and "上限" in detail, detail
    assert (p_root / "demo" / "assets" / "fig_2.png").exists() is False, "超累计配额不应落盘"
    assert (p_root / "demo" / "slices.json").read_text(encoding="utf-8") == before, \
        "超累计配额不应改切片索引"


def test_project_delete_failure_is_explicit(tmp_path, monkeypatch):
    """R5-B-05/15：删除失败（目录残留）→ 显式 500 报错 + 手动清理提示，绝不静默 ok。"""
    import shutil as sh

    from fastapi.testclient import TestClient

    from medkit import main as m
    from medkit.core import config as cfg
    from medkit.routers import projects as proj

    p_root = tmp_path / "projects"
    (p_root / "p1").mkdir(parents=True)
    (p_root / "p1" / "meta.json").write_text(
        '{"pid": "p1", "subject": "儿科"}', encoding="utf-8")
    real = cfg.load()
    monkeypatch.setattr(cfg, "load", lambda: {**real, "projects_dir": str(p_root)})
    c = TestClient(m.app, base_url="http://127.0.0.1")

    real_rmtree = sh.rmtree

    def failing_rmtree(path, ignore_errors=False):  # 模拟删除被打断但未删除
        raise OSError("拒绝访问")

    monkeypatch.setattr(proj.shutil, "rmtree", failing_rmtree)
    r = c.delete("/api/projects/p1")
    assert r.status_code == 500, f"删除失败应显式报错（实为 {r.status_code}: {r.text}）"
    assert "手动清理" in r.json()["detail"] or "删除失败" in r.json()["detail"], r.json()
    assert (p_root / "p1").exists(), "失败场景目录应保留（未误删）"
    monkeypatch.setattr(proj.shutil, "rmtree", real_rmtree)
    r2 = c.delete("/api/projects/p1")
    assert r2.status_code == 200 and r2.json()["ok"] is True
    assert not (p_root / "p1").exists()
