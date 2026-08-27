"""WP-01 大纲覆盖度引擎测试（core/syllabus.py + routers/syllabus.py）。

隔离：conftest 已把 dbs.DB_PATH / 域模块 DB_FILE 指向 tmp（SQL 模式同库）；
library 模块 JSON/SQL 两种模式均被覆盖（tests/test_library_sql.py 模式）。
"""
from __future__ import annotations

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
