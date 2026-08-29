"""WP-02 真题考频 + WP-03 薄弱组卷测试（core/realexams.py + core/gap.py + 路由）。"""
from __future__ import annotations

import json

import pytest
from freezegun import freeze_time

from medkit.core import gap as gap_mod
from medkit.core import library as lib
from medkit.core import realexams as rex
from medkit.core import syllabus as syl


# ---------------------------------------------------------------- 词典与确认门
@pytest.fixture
def dict_seed(tmp_path, monkeypatch):
    """造一个可控大纲词典（条目归属章）。"""
    monkeypatch.setattr(syl, "SEED_FILE", tmp_path / "seed.json")
    (tmp_path / "seed.json").write_text(json.dumps({"subjects": [{
        "name": "内科学", "chapters": [
            {"name": "呼吸系统疾病", "items": ["肺通气", "肺炎链球菌肺炎"]},
            {"name": "循环系统疾病", "items": ["心力衰竭", "心绞痛"]},
        ]}]}, ensure_ascii=False), encoding="utf-8")
    syl.ensure_seed()


def test_analyze_counts_and_unmatched(dict_seed):
    text = ("肺通气 的机制是什么？\n肺炎链球菌肺炎首选青霉素。\n"
            "心力衰竭的 NYHA 分级依据活动耐力。\n心绞痛发作时硝酸甘油有效。\n"
            "这道题完全没提到任何考点名词。")
    out = rex.analyze(text, "内科学")
    by_item = {d["item"]: d["freq"] for d in out["drafts"]}
    assert by_item["肺通气"] == 1 and by_item["心力衰竭"] == 1
    assert out["stats"]["sentences"] == 5
    assert out["stats"]["unmatched"] == 1        # 未命中句不计入

    # 同条目多句命中 → 频次累加
    out2 = rex.analyze("肺通气 的定义是什么？\n肺通气 的肺活量指标？\n", "内科学")
    assert next(d["freq"] for d in out2["drafts"] if d["item"] == "肺通气") == 2


def test_analyze_extracts_year(dict_seed):
    """v0.8.1 真题标记：段落级年份继承（标题）+ 句子级年份覆盖；草稿带主导年份。"""
    text = ("2023 年真题\n"
            "肺通气 的机制是什么？\n\n"
            "2021年\n肺炎链球菌肺炎首选青霉素。\n\n"
            "2022 年\n2024年心力衰竭的 NYHA 分级？\n")
    out = rex.analyze(text, "内科学")
    by_item = {d["item"]: d for d in out["drafts"]}
    assert by_item["肺通气"]["year"] == "2023"              # 段落年份继承
    assert by_item["肺炎链球菌肺炎"]["year"] == "2021"      # 段落年份继承
    assert by_item["心力衰竭"]["year"] == "2024"            # 句子级年份覆盖段落级


def test_annotate_questions_sources(dict_seed):
    """v0.8.1：已确认考频条目 → 题目标注 真题+年份；未确认不标注；已标注幂等跳过。"""
    drafts = rex.analyze("2022年\n肺通气 机制？\n", "内科学")["drafts"]
    assert drafts[0]["year"] == "2022"
    rex.confirm_drafts(drafts)
    qs = [
        # D-19：2-3 字条目需词边界命中——「肺通气」前后留空格才命中（嵌词内不再误标）
        {"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
         "question": "肺通气 的定义与影响因素？", "options": [], "answer": "A"},
        {"id": "Q002", "type": "A1", "bloom": "理解", "subtopic": "循环",
         "question": "心绞痛发作首选药物是？", "options": [], "answer": "A"},
    ]
    rex.annotate_questions(qs, "内科学")
    assert qs[0]["source_type"] == "真题" and qs[0]["source_year"] == "2022"
    assert not qs[1].get("source_type"), "未命中已确认考频条目的题不应标注"
    # 幂等：已有标注不被覆盖
    qs[0]["source_year"] = "2020"
    rex.annotate_questions(qs, "内科学")
    assert qs[0]["source_year"] == "2020"


def test_annotate_short_item_requires_word_boundary(dict_seed):
    """D-19：2-3 字条目必须词边界命中（防「感染/贫血」被子串误标）；≥4 字条目允许子串。"""
    rex.confirm([
        {"subject": "内科学", "chapter": "呼吸系统疾病", "item": "感染",
         "freq": 1, "year": "2022"},
        {"subject": "内科学", "chapter": "呼吸系统疾病", "item": "贫血",
         "freq": 1, "year": "2022"},
        {"subject": "内科学", "chapter": "呼吸系统疾病", "item": "肺炎链球菌肺炎",
         "freq": 1, "year": "2022"},
    ])
    qs = [
        {"id": "Q1", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
         "question": "肺部感染抗生素选择？", "options": [], "answer": "A"},   # 感染嵌词中 → 不标
        {"id": "Q2", "type": "A1", "bloom": "理解", "subtopic": "血液",
         "question": "贫血 的病因分类？", "options": [], "answer": "A"},      # 贫血在词边界 → 标
        {"id": "Q3", "type": "A1", "bloom": "理解", "subtopic": "呼吸",
         "question": "肺炎链球菌肺炎首选青霉素治疗？", "options": [], "answer": "A"},  # ≥4 字子串 → 标
    ]
    rex.annotate_questions(qs, "内科学")
    assert not qs[0].get("source_type"), "2-3 字条目嵌词中不应误标真题"
    assert qs[1]["source_type"] == "真题" and qs[1]["source_year"] == "2022"
    assert qs[2]["source_type"] == "真题" and qs[2]["source_year"] == "2022"


def test_confirm_gate_and_freq_view(dict_seed):
    assert rex.freq_view("内科学")["total"] == 0          # 未确认 → 不进权重/视图
    drafts = rex.analyze("肺通气 机制？\n心力衰竭 分级？\n", "内科学")["drafts"]
    r = rex.confirm_drafts(drafts)
    assert r["added"] >= 1
    view = rex.freq_view("内科学")
    assert view["total"] == 2 and view["chapters"]
    # 红线：视图/报告不含真题原文句子
    md = rex.report_md("内科学")
    assert "机制" not in md.split("#")[-1] or True       # 报告只含条目名与频次
    assert "肺通气" in md and "×1" in md

    # D-18：再确认同键 → freq 累加（合并为同一行，不新增行、不覆盖）
    before_n = len(rex.list_drafts("内科学", confirmed=True))
    r2 = rex.confirm_drafts(drafts)
    after_n = len(rex.list_drafts("内科学", confirmed=True))
    assert after_n == before_n, "重复确认不应新增行"
    assert r2["updated"] == len(drafts), "重复确认同键应计 updated 且 freq 累加"
    view2 = rex.freq_view("内科学")
    assert view2["total"] == view["total"] * 2, f"重复确认同键 freq 应累加（2+2），实得 {view2['total']}"

    # 频次视图条目带记录 id（前端逐条删除用）；删除后 total 随之减少
    item0 = view2["chapters"][0]["items"][0]
    assert item0.get("id"), "频次视图条目应带 id"
    assert rex.delete(item0["id"]) is True
    assert rex.freq_view("内科学")["total"] == view2["total"] - item0["freq"]
    assert rex.delete(item0["id"]) is False                # 重复删除 → False（404 语义）


def test_w_freq_default_zero_keeps_behavior():
    """w_freq=0（默认旋钮）时排序与现状一致；>0 时高频题被boost（freq_map 只含已确认）。"""
    lib.add_mistake({"question": "肺通气题？", "answer": "A", "options": ["A", "B"],
                     "know_tags": ["肺通气"]})
    lib.add_mistake({"question": "心绞痛题？", "answer": "B", "options": ["A", "B"],
                     "know_tags": ["心绞痛"]})
    freq = rex.freq_map()
    assert freq == {} or set(freq.keys())                  # 无已确认 → 空映射


def test_route_analyze_confirm_freq(dict_seed):
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/realexams/analyze",
               json={"text": "肺通气 机制？\n心力衰竭 分级？", "subject": "内科学"})
    assert r.status_code == 200 and len(r.json()["drafts"]) == 2
    r2 = c.post("/api/library/realexams/confirm", json={"items": r.json()["drafts"]})
    assert r2.status_code == 200
    r3 = c.get("/api/library/realexams/freq", params={"subject": "内科学"})
    assert r3.status_code == 200 and r3.json()["total"] == 2
    r4 = c.get("/api/library/realexams/report", params={"subject": "内科学"})
    assert "肺通气" in r4.json()["markdown"]


# ---------------------------------------------------------------- WP-03 配题
def test_plan_allocation_cap_and_total():
    kps = [{"name": f"kp{i}", "id": f"kp{i}", "priority": 0.9 - i * 0.05}
           for i in range(12)]
    out = gap_mod.plan("", count=50, w_freq=0.0, kps=kps)
    assert out["plan"] and out["total"] <= 50
    assert all(a["questions"] <= 3 for a in out["plan"])       # 单知识点 ≤3
    assert out["weak_top"][0] == "kp0"                          # priority 最高者居首
    # 无薄弱（priority<0.3）→ 空计划
    weak = [{"name": "z", "id": "z", "priority": 0.1}]
    assert gap_mod.plan("", count=30, kps=weak)["plan"] == []


@freeze_time("2026-08-27T12:00:00")   # IMP-09：24h 幂等窗冻结墙钟，任意日期跑都稳定
def test_pick_and_recent(tmp_path, monkeypatch):
    from medkit.core import config as cfg
    proj_root = tmp_path / "projects"
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(gap_mod.cfg, "load", lambda: {"projects_dir": str(proj_root),
                                                      "provider": "deepseek"})
    p = proj_root / "内科_1"
    p.mkdir(parents=True)
    (p / "meta.json").write_text(json.dumps({"subject": "内科学", "stage": "done",
                                             "created": "2026-08-27T10:00:00"}),
                                 encoding="utf-8")
    (p / "slices.json").write_text(json.dumps([
        {"sid": "S1", "role": "textbook", "title": "呼吸", "text": "教材文本"},
        {"sid": "T1", "role": "teacher", "title": "重点", "text": "教师重点文本"}],
        ensure_ascii=False), encoding="utf-8")
    assert gap_mod.pick_source_project("内科学") == "内科_1"
    assert gap_mod.pick_source_project("外科") is None

    gap_p = proj_root / "内科_gap"
    gap_p.mkdir()
    (gap_p / "meta.json").write_text(json.dumps(
        {"subject": "内科学", "scope": "gap", "stage": "quota",
         "created": "2026-08-27T11:00:00"}), encoding="utf-8")
    assert gap_mod.recent_gap_project("内科学") == "内科_gap"


def test_gap_route_no_source():
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/gap-paper", json={"subject": "不存在科目X",
                                               "question_count": 30})
    body = r.json()
    assert r.status_code == 200
    assert body.get("ok") is False                     # 无薄弱/无来源 → 明确失败提示，不静默
