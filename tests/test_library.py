"""M1/M2 个人学习库测试：掌握度纯逻辑 + 错题/知识点落盘 + 路由 TestClient。

隔离：monkeypatch lib.MISTAKES_FILE / KNOWLEDGE_FILE 到临时目录，不污染真实 ~/.medkit。
不发起真实 OCR / LLM。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(lib, "MISTAKES_FILE", tmp_path / "mistakes.json")
    monkeypatch.setattr(lib, "KNOWLEDGE_FILE", tmp_path / "knowledge.json")
    return temp_dir(tmp_path)


def temp_dir(p):
    return p


# ---------------------------------------------------------------- 导入解析
def test_import_bom_stripped_and_official_json():
    """D-06：BOM CSV 首列不静默过滤；README 官方结构 JSON（options:[{label,text}]）可解析。"""
    import json as _json

    csv_text = "\ufeff题干,选项,答案\n什么是肺炎？,A|B|C,D"
    rows = lib.parse_import_text(csv_text, "csv")
    assert rows and rows[0]["question"] == "什么是肺炎？", f"BOM 首列不应静默过滤：{rows}"
    assert rows[0]["answer"] == "D"

    js = _json.dumps([{"stem": "题？",
                       "options": [{"label": "A", "text": "甲"}, {"label": "B", "text": "乙"}],
                       "answer": "A", "explanation": "解析", "module_name": "呼吸"}])
    rows2 = lib.parse_import_text(js, "json")
    assert rows2 and rows2[0]["options"] == ["甲", "乙"]
    assert rows2[0]["topic"] == "呼吸"
    assert rows2[0]["analysis"] == "解析"


# ---------------------------------------------------------------- 掌握度纯逻辑
def test_compute_state_thresholds():
    assert lib.compute_state(1.0) == "mastered"
    assert lib.compute_state(0.96) == "mastered"
    assert lib.compute_state(0.90) == "solid"
    assert lib.compute_state(0.70) == "shaky"
    assert lib.compute_state(0.55) == "weak"
    assert lib.compute_state(0.0) == "weak"


def test_compute_score_all_miss_low():
    s = lib.compute_score(correct=0, total=5, last_tried=None)
    assert s < 0.60  # 全错 → 薄弱


def test_compute_score_all_correct_high():
    s = lib.compute_score(correct=8, total=8, last_tried="2026-08-27")
    assert s >= 0.80  # 近窗口全对 → 掌握


def test_compute_score_small_sample_discounted():
    # 只有 1 条全对 → 置信打折，不应直接判 mastered（score < 0.95 而非 1.0）
    s = lib.compute_score(correct=1, total=1, last_tried="2026-08-27")
    assert s < 0.95


def test_compute_priority_weak_hot_beats_solid():
    weak = lib.compute_priority(score=0.3, miss_count=5, last_tried="2026-08-27")
    solid = lib.compute_priority(score=0.9, miss_count=1, last_tried="2026-08-01")
    assert weak > solid


# ---------------------------------------------------------------- 错题 + 知识点落盘
def test_add_mistake_derives_knowledge(isolated):
    rec = lib.add_mistake({
        "source": "manual", "subject": "儿科学", "chapter": "呼吸系统",
        "topic": "支气管肺炎", "question": "首选抗生素是？", "answer": "B",
        "know_tags": ["支气管肺炎首选治疗"], "error_reason": "concept_gap",
    })
    assert rec["id"].startswith("m_")
    kps = lib.list_knowledge()
    assert any(k["name"] == "支气管肺炎首选治疗" for k in kps)
    kp = next(k for k in kps if k["name"] == "支气管肺炎首选治疗")
    assert kp["miss"] == 1 and kp["state"] == "weak"


def test_mark_learned_updates_mastery(isolated):
    rec = lib.add_mistake({"question": "q1", "know_tags": ["tagA"]})
    lib.mark_learned(rec["id"], learned=True)
    kp = next(k for k in lib.list_knowledge() if k["name"] == "tagA")
    # 新统计口径：add_mistake 已经记过一次 miss；mark_learned 不再新增 attempts/correct
    assert kp["correct"] == 0 and kp["miss"] == 1
    assert kp["attempts"] == 1
    # 标记 learned 不改变掌握度统计（统计只来自真实作答），所以 score 仍为全错水平


def test_sync_from_paper_dedup(isolated):
    qs = [{"id": "Q01", "subject": "儿科学", "sid": "s_1", "question": "A 题内容足够长",
           "options": ["x", "y", "z"], "answer": "A", "analysis": "a",
           "know_tags": ["儿科呼吸"], "user_answer": "B"},
          {"id": "Q02", "subject": "儿科学", "question": "B 题内容足够长",
           "options": ["x", "y"], "answer": "B", "analysis": "b",
           "know_tags": ["儿科呼吸"], "user_answer": "C"}]
    assert lib.sync_from_paper(qs, pid="p1") == 2
    assert len(lib.list_mistakes()) == 2
    # 重复同步（同题干前40字）→ 去重为 0
    assert lib.sync_from_paper(qs, pid="p1") == 0
    assert len(lib.list_mistakes()) == 2
    kp = next(k for k in lib.list_knowledge() if k["name"] == "儿科呼吸")
    assert kp["miss"] == 2


def test_delete_removes_from_knowledge(isolated):
    rec = lib.add_mistake({"question": "del", "know_tags": ["delTag"]})
    lib.delete_mistake(rec["id"])
    assert all(k["name"] != "delTag" or rec["id"] not in k["mistakes"]
               for k in lib.list_knowledge())


def test_delete_mistake_decrements_miss_and_attempts(isolated):
    # 未掌握的错题样本入账为 miss → 删除需同步回退 miss/attempts，否则统计虚高
    lib.add_mistake({"question": "delA", "know_tags": ["口径A"], "learned": False})
    kp = next(k for k in lib.list_knowledge() if k["name"] == "口径A")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (1, 0, 1)
    mids = list(kp["mistakes"])
    lib.delete_mistake(mids[0])
    kp = next(k for k in lib.list_knowledge() if k["name"] == "口径A")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (0, 0, 0)


def test_delete_mistake_decrements_correct_sample(isolated):
    # 已掌握（入账即 correct 样本）的错题删除 → 同步回退 correct/attempts
    rec = lib.add_mistake({"question": "delB", "know_tags": ["口径B"], "learned": True})
    kp = next(k for k in lib.list_knowledge() if k["name"] == "口径B")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (1, 1, 0)
    lib.delete_mistake(rec["id"])
    kp = next(k for k in lib.list_knowledge() if k["name"] == "口径B")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (0, 0, 0)


def test_record_review_flows_back_to_mastery(isolated):
    lib.add_mistake({"question": "q", "know_tags": ["回流点"], "learned": False})
    # 复习通过（quality=4≥3）→ 记一次 correct，刷新 last_reviewed/last_tried
    lib.record_review("回流点", 4)
    kp = next(k for k in lib.list_knowledge() if k["name"] == "回流点")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (2, 1, 1)
    assert kp.get("last_reviewed") and kp.get("last_tried")
    # 复习失败（quality=1<3）→ 记一次 miss；不影响 prior correct
    lib.record_review("回流点", 1)
    kp = next(k for k in lib.list_knowledge() if k["name"] == "回流点")
    assert (kp["attempts"], kp["correct"], kp["miss"]) == (3, 1, 2)


# ---------------------------------------------------------------- 文本结构化
def test_parse_question_text_options_answer_analysis():
    txt = """某患儿 5 岁，阵发性喘憋。
A. 支原体肺炎
B. 哮喘
C. 毛细支气管炎
D. 支气管肺炎
答案：B
解析：喘憋伴双肺哮鸣音，首选考虑哮喘。"""
    r = lib.parse_question_text(txt)
    assert r["answer"] == "B"
    assert len(r["options"]) == 4
    assert "哮喘" in r["options"][1]
    assert "支气管肺炎" in r["options"][3]
    assert "哮喘" in r["analysis"]
    assert "喘憋" in r["question"]


def test_parse_question_text_inline_answer():
    r = lib.parse_question_text("题干文本【答案】A\n解析：机制说明")
    assert r["answer"] == "A"
    assert "机制说明" in r["analysis"]


def test_parse_question_text_fallback():
    r = lib.parse_question_text("完全没结构的纯题干")
    assert r["question"] == "完全没结构的纯题干"
    assert r["answer"] == ""


# ---------------------------------------------------------------- 掌握度视图 / 推荐
def test_mastery_view_stats(isolated):
    lib.add_mistake({"question": "a", "know_tags": ["儿科甲"], "learned": False})
    lib.add_mistake({"question": "b", "know_tags": ["儿科甲"]})
    lib.add_mistake({"question": "c", "know_tags": ["儿科乙"]})
    v = lib.get_mastery_view()
    assert v["stats"]["total_knowledge"] == 2
    assert v["stats"]["total_mistakes"] == 3
    assert v["stats"]["weak"] == 2


def test_recommend_sorted_by_priority(isolated):
    # kpA：薄弱，错得多且刚错过 → priority 高
    for i in range(4):
        lib.add_mistake({"question": f"Aw{i}", "know_tags": ["弱项A"], "learned": False})
    # kpB：连续答对抬到 solid → priority 低
    lib.add_mistake({"question": "B0", "know_tags": ["强项B"], "learned": False})
    for _ in range(8):
        lib.record_quiz("强项B", 3)   # 连续 pass 抬 score
    recs = lib.recommend(10)
    names = [k["name"] for k in recs]
    assert "弱项A" in names and "强项B" in names
    # 修复3：纯按 priority 降序；薄弱但 priority 更高者必须排在前，weak 不再一票否决
    assert names.index("弱项A") < names.index("强项B")
    by_name = {k["name"]: k["priority"] for k in recs}
    assert names == sorted(names, key=lambda n: by_name[n], reverse=True)


# ---------------------------------------------------------------- 路由（TestClient）
def test_router_mistakes_crud(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/mistakes", json={"question": "首选药是？", "know_tags": ["首选药"]})
    assert r.status_code == 200
    mid = r.json()["mistake"]["id"]

    r = c.get("/api/library/mistakes")
    assert r.status_code == 200 and len(r.json()["mistakes"]) == 1

    r = c.post(f"/api/library/mistakes/{mid}/learn", json={"learned": True})
    assert r.status_code == 200 and r.json()["mistake"]["learned"] is True

    r = c.delete(f"/api/library/mistakes/{mid}")
    assert r.status_code == 200
    assert c.get("/api/library/mistakes").json()["mistakes"] == []


def test_router_import_text_parsed(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    txt = "题干。\nA. 甲\nB. 乙\n答案：B\n解析：选乙。"
    r = c.post("/api/library/mistakes/import-text", json={"question": txt, "know_tags": ["文本"]})
    assert r.status_code == 200
    j = r.json()
    assert j["parsed"] is True
    assert j["mistake"]["answer"] == "B"
    assert len(j["mistake"]["options"]) == 2


def test_router_sync_paper(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/mistakes/sync-paper", json={
        "pid": "p9",
        "questions": [{"id": "Q1", "question": "同步题", "answer": "A",
                       "analysis": "a", "know_tags": ["同步"], "user_answer": "C"}]})
    assert r.status_code == 200 and r.json()["added"] == 1


def test_router_mastery_recommend(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    c.post("/api/library/mistakes", json={"question": "q", "know_tags": ["A"], "learned": False})
    rm = c.get("/api/library/mastery")
    assert rm.status_code == 200 and rm.json()["stats"]["total_knowledge"] == 1
    rr = c.get("/api/library/recommend?limit=5")
    assert rr.status_code == 200 and rr.json()["recommend"]


def test_router_import_text_empty_raises(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    assert c.post("/api/library/mistakes/import-text", json={"question": ""}).status_code == 400


# ---------------------------------------------------------------- 数据卫生（v0.7.1）
def test_heal_mojibake_roundtrip():
    moji = "儿科学".encode("utf-8").decode("cp1252")   # UTF-8 字节被 cp1252 误读
    assert lib.heal_mojibake(moji) == "儿科学"
    assert lib.heal_mojibake("正常中文") is None          # 纯中文无需修复
    assert lib.heal_mojibake("") is None


def test_heal_encoding_recover_and_flag(isolated):
    moji = "儿科学".encode("utf-8").decode("cp1252")   # UTF-8 字节被 cp1252 误读（字节均位于 cp1252 表内）
    m1 = {"id": "m1", "source": "manual", "subject": moji, "chapter": "????",
          "question": "题干？", "know_tags": [moji], "miss_count": 1, "learned": False}
    k1 = {"id": "kp1", "name": moji, "subject": "?????", "chapter": "", "score": 0.3}
    lib._save(lib.MISTAKES_FILE, [m1])
    lib._save(lib.KNOWLEDGE_FILE, [k1])
    assert lib.scan_corrupted()["corrupted"] == 2, "可逆+不可逆均应被扫描到"

    res = lib.heal_encoding()
    assert res["healed"] >= 1 and res["flagged"] >= 1, "应修复可逆项并标记不可逆项"
    assert res["backups"], "修复前应有备份"

    ms = lib.list_mistakes()[0]
    assert ms["subject"] == "儿科学" and ms["know_tags"] == ["儿科学"]
    assert ms["data_broken"] is True, "chapter '????' 不可逆 → 标记不删除"
    kps = lib.list_knowledge()
    assert kps[0]["name"] == "儿科学"
    assert kps[0]["data_broken"] is True


def test_router_heal_and_dashboard_corrupted(isolated):
    c = TestClient(m.app, base_url="http://127.0.0.1")
    moji = "儿科学".encode("utf-8").decode("cp1252")
    lib._save(lib.MISTAKES_FILE, [{"id": "m1", "source": "manual", "subject": moji,
                                   "question": "q?", "know_tags": [moji], "miss_count": 1}])
    d = c.get("/api/library/dashboard").json()
    assert d["corrupted"] == 1, "dashboard 应带 corrupted 计数"
    r = c.post("/api/library/maintenance/heal")
    assert r.status_code == 200 and r.json()["healed"] >= 1
    assert c.get("/api/library/dashboard").json()["corrupted"] == 0
    assert c.get("/api/library/maintenance/scan").json()["corrupted"] == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
