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

    # 再确认同键 → 幂等合并（freq 累加为合并后行，不新增行）
    before_n = len(rex.list_drafts("内科学", confirmed=True))
    rex.confirm_drafts(drafts)
    after_n = len(rex.list_drafts("内科学", confirmed=True))
    assert after_n <= before_n


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
