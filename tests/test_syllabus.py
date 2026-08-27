"""WP-01 大纲覆盖度引擎测试（core/syllabus.py + routers/syllabus.py）。

隔离：conftest 已把 dbs.DB_PATH / 域模块 DB_FILE 指向 tmp（SQL 模式同库）；
library 模块 JSON/SQL 两种模式均被覆盖（tests/test_library_sql.py 模式）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from medkit.core import library as lib
from medkit.core import syllabus as syl


# ---------------------------------------------------------------- 规则解析（零 LLM）
def test_parse_text_rules():
    text = """
    生理学
    一、绪论
    1、生理学的任务和研究手段
    2、机体的内环境和稳态
    二、细胞的基本功能
    1、物质跨细胞膜转运方式
    2、静息电位的概念及产生机制
    三、血液
    1、血液的理化特性
    """
    drafts = syl.parse_text(text)
    assert len(drafts) == 5
    assert drafts[0]["item"] == "生理学的任务和研究手段"
    assert drafts[0]["chapter"] == "绪论"
    assert drafts[0]["subject"] == "生理学"
    assert drafts[4]["chapter"] == "血液"


def test_parse_text_subject_param_and_junk():
    drafts = syl.parse_text("一、呼吸系统\n1、肺通气\n\n\n", subject="内科学")
    assert len(drafts) == 1 and drafts[0]["subject"] == "内科学"
    assert syl.parse_text("完全没结构的自由文本", "内科学") == []


# ---------------------------------------------------------------- 种子导入（幂等）
def test_ensure_seed_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(syl, "SEED_FILE", tmp_path / "seed.json")  # 假种子
    import json
    (tmp_path / "seed.json").write_text(json.dumps({"exam": "测试", "subjects": [
        {"name": "内科学", "chapters": [{"name": "呼吸系统疾病",
                                         "items": ["肺通气", "肺炎"]}]}],
        "note": "n"}, ensure_ascii=False), encoding="utf-8")
    r1 = syl.ensure_seed()
    assert r1["imported"] == 3           # 1 章行 + 2 条目行
    r2 = syl.ensure_seed()
    assert r2["imported"] == 0           # 幂等
    subs = {s["subject"] for s in syl.list_subjects()}
    assert "内科学" in subs


# ---------------------------------------------------------------- 确认 + 覆盖判定
@pytest.fixture
def subject_seed(tmp_path, monkeypatch):
    monkeypatch.setattr(syl, "SEED_FILE", tmp_path / "seed.json")
    import json
    (tmp_path / "seed.json").write_text(
        json.dumps({"subjects": [{"name": "内科学", "chapters": [
            {"name": "呼吸系统疾病", "items": ["肺通气", "肺炎", "肺结核"]}]}]},
            ensure_ascii=False), encoding="utf-8")
    syl.ensure_seed()


def test_coverage_pending_covered_mastered(subject_seed):
    # 初始：全部 pending
    cov = syl.coverage("内科学")
    assert cov["totals"]["pending"] == 3 and cov["totals"]["covered"] == 0

    # 错题触及「肺通气」 → covered（状态机 weak）
    lib.add_mistake({"question": "肺通气题？", "answer": "A", "options": ["A", "B"],
                     "subject": "内科学", "chapter": "呼吸系统疾病",
                     "know_tags": ["肺通气"]})
    cov = syl.coverage("内科学")
    assert cov["totals"]["covered"] == 1 and cov["totals"]["pending"] == 2

    # 「肺炎」反复答对 → mastered
    lib.add_mistake({"question": "肺炎题？", "answer": "B", "options": ["A", "B"],
                     "know_tags": ["肺炎"]})
    for _ in range(25):
        assert lib.record_quiz("肺炎", 2) is not None
    cov = syl.coverage("内科学")
    assert cov["totals"]["mastered"] == 1
    # 未覆盖清单只剩 肺结核
    md = syl.report_md("内科学")
    assert "肺通气" not in md and "肺结核" in md


def test_confirm_upsert_and_replace(subject_seed):
    from medkit.routers.syllabus import ConfirmBody, ConfirmItem
    body = ConfirmBody(items=[ConfirmItem(subject="内科学", chapter="呼吸系统疾病",
                                          item="支气管哮喘（粘贴）")])
    r = syl_confirm_helper(body)
    assert r["added"] == 1
    cov = syl.coverage("内科学")
    assert cov["totals"]["items"] == 4 and cov["totals"]["pending"] == 4
    # 重复确认不重复加
    assert syl_confirm_helper(body)["added"] == 0
    # replace 订正（删旧 ch 全部条目重新插入）
    body2 = ConfirmBody(items=[ConfirmItem(subject="内科学", chapter="呼吸系统疾病",
                                           item="COPD")], replace=True)
    r2 = syl_confirm_helper(body2)
    assert r2["replaced_rows"] > 0
    cov = syl.coverage("内科学")
    assert cov["totals"]["items"] == 1 and cov["totals"]["pending"] == 1


def syl_confirm_helper(body):
    from medkit.routers.syllabus import syllabus_confirm
    return syllabus_confirm(body)


def test_router_tree_and_report_api(subject_seed):
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.get("/api/syllabus/status")
    assert r.status_code == 200
    assert any(s["subject"] == "内科学" for s in r.json()["subjects"])
    r2 = c.get("/api/syllabus/coverage", params={"subject": "内科学"})
    assert r2.status_code == 200 and r2.json()["totals"]["items"] == 3
    r3 = c.get("/api/syllabus/report", params={"subject": "内科学"})
    assert "肺通气" in r3.json()["markdown"] and "未覆盖" in r3.json()["markdown"]
    r4 = c.post("/api/syllabus/parse", json={"text": "一、消化系统\n1、食管癌", "subject": "内科学"})
    assert r4.status_code == 200 and r4.json()["drafts"][0]["chapter"] == "消化系统"


# ---------------------------------------------------------------- 教师重点为纲（域内默认标准）
def _mk_teacher_project(tmp_path, subject="内科学"):
    from medkit.core import config as cfg
    proj = Path(cfg.load().get("projects_dir", "")) / "t_proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "meta.json").write_text(json.dumps({"subject": subject}), encoding="utf-8")
    (proj / "slices.json").write_text(json.dumps([
        {"sid": "T01", "role": "teacher", "title": "呼吸系统重点",
         "text": "肺通气与换气\n肺炎链球菌肺炎首选青霉素\nCOPD 肺功能分级\n肺结核化疗方案 2HRZE/4HR"},
        {"sid": "T02", "role": "teacher", "title": "循环系统重点",
         "text": "心衰的 NYHA 分级\n急性心肌梗死心电图定位"},
    ]), encoding="utf-8")
    return subject


def test_teacher_sync_and_source_filter(tmp_path, monkeypatch):
    from medkit.core import config as cfg
    from medkit.core import syllabus as syl2
    proj_root = tmp_path / "projects"
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(syl2, "_proj_dir", lambda: proj_root)
    # 用真实项目目录太绕 → 直接构造：把 cfg 指向 tmp 后按同步逻辑建项目
    proj = proj_root / "t_proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "meta.json").write_text(json.dumps({"subject": "内科学"}), encoding="utf-8")
    (proj / "slices.json").write_text(json.dumps([
        {"sid": "T01", "role": "teacher", "title": "呼吸系统重点",
         "text": "肺通气与换气\n肺炎链球菌肺炎首选青霉素\nCOPD 肺功能分级\n肺结核化疗方案 2HRZE/4HR"},
    ]), encoding="utf-8")
    stats = syl2.sync_teacher()
    assert stats["slices"] == 1 and stats["items"] == 4
    assert stats["subjects"] == ["内科学"]
    # 幂等：再同步不重复
    stats2 = syl2.sync_teacher()
    assert stats2["items"] == 4
    # source 过滤：teacher 有、seed 无
    cov_t = syl2.coverage("内科学", "teacher")
    assert cov_t["totals"]["items"] == 4 and cov_t["totals"]["pending"] == 4
    cov_s = syl2.coverage("内科学", "seed")
    assert cov_s["totals"]["items"] == 0
    # 匹配：错题知识标签「肺炎链球菌」命中 teacher 条目 → covered
    lib.add_mistake({"question": "肺炎链球菌首选药？", "answer": "A", "options": ["A", "B"],
                     "know_tags": ["肺炎链球菌肺炎首选青霉素"]})
    cov_t2 = syl2.coverage("内科学", "teacher")
    assert cov_t2["totals"]["covered"] == 1 and cov_t2["totals"]["pending"] == 3
    # report 按教师重点标准
    md = syl2.report_md("内科学", "teacher")
    assert "教师重点" in md and "COPD 肺功能分级" in md


# ---------------------------------------------------------------- 多格式导入解析
def test_parse_import_json_normalize():
    rows = lib.parse_import_text(json.dumps([
        {"stem": "心衰分级依据？", "options": [{"label": "A", "text": "NYHA"},
                                              {"label": "B", "text": "Killip"}],
         "answer": "A", "explanation": "NYHA 依据活动耐力", "module_name": "心力衰竭"},
        {"question": "第二题？", "options": ["A", "B"], "answer": "B", "know_tags": ["x"]},
    ]), "json")
    assert len(rows) == 2
    assert rows[0]["question"] == "心衰分级依据？" and rows[0]["options"] == ["NYHA", "Killip"]
    assert rows[0]["topic"] == "心力衰竭" and rows[0]["know_tags"] == ["心力衰竭"]
    assert rows[1]["options"] == ["A", "B"]


def test_parse_import_csv():
    text = ("题干,选项,答案,解析,科目,章节,知识点\n"
            "心衰分级依据？,NYHA；Killip,A,按活动耐力,内科学,循环系统,心力衰竭\n"
            "二尖瓣狭窄最常见的病因？,风湿热；感染性心内膜炎,A,风心病史,内科学,循环系统,瓣膜病\n")
    rows = lib.parse_import_text(text, "csv")
    assert len(rows) == 2
    assert rows[0]["options"] == ["NYHA", "Killip"] and rows[0]["answer"] == "A"
    assert rows[0]["chapter"] == "循环系统" and rows[0]["know_tags"] == ["心力衰竭"]


def test_parse_import_md_blocks():
    text = ("1、患儿男 3 岁发热咳嗽 3 天\nA、支原体肺炎\nB、细菌性肺炎\n答案：B\n"
            "2、心电图 QRS 增宽最常见于？\nA、室速\nB、室上速\n答案：A")
    rows = lib.parse_import_text(text, "md")
    assert len(rows) == 2
    assert rows[0]["answer"] == "B" and len(rows[0]["options"]) == 2
    assert rows[1]["answer"] == "A"


def test_route_import_file(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    csv_text = ("题干,选项,答案,章节\n测试题甲？,A|B,A,测试章\n测试题乙？,C|D,C,测试章\n")
    r = c.post("/api/library/mistakes/import-file",
               files={"file": ("错题.csv", csv_text.encode("utf-8"), "text/csv")})
    assert r.status_code == 200 and r.json()["added"] == 2
