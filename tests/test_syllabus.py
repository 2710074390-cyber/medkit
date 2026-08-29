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


def test_coverage_loads_knowledge_once(subject_seed, monkeypatch):
    """D-12：覆盖判定每条目不再重载全表——一次加载缓存到局部变量，循环内复用。"""
    lib.add_mistake({"question": "肺通气题？", "answer": "A", "options": ["A", "B"],
                     "subject": "内科学", "chapter": "呼吸系统疾病", "know_tags": ["肺通气"]})
    calls = {"n": 0}
    orig = lib.list_knowledge
    def counting():
        calls["n"] += 1
        return orig()
    monkeypatch.setattr(lib, "list_knowledge", counting)
    syl.coverage("内科学")
    # 3 条目场景：原实现每条目 1 次 → 5 次；现在 = 1（一次加载后池与匹配复用）
    assert calls["n"] == 1, f"应只加载 1 次（循环内复用），实际 {calls['n']}"


def test_confirm_upsert_and_replace(subject_seed):
    from medkit.routers.syllabus import ConfirmBody, ConfirmItem
    body = ConfirmBody(items=[ConfirmItem(subject="内科学", chapter="呼吸系统疾病",
                                          item="支气管哮喘（教师）")])
    r = syl_confirm_helper(body)
    assert r["added"] == 1
    cov = syl.coverage("内科学")
    assert cov["totals"]["items"] == 4 and cov["totals"]["pending"] == 4
    # 重复确认不重复加
    assert syl_confirm_helper(body)["added"] == 0
    # replace 订正（v4：仅删该章 teacher 行，seed 行不受影响）
    body2 = ConfirmBody(items=[ConfirmItem(subject="内科学", chapter="呼吸系统疾病",
                                           item="COPD")], replace=True)
    r2 = syl_confirm_helper(body2)
    assert r2["replaced_rows"] > 0
    cov_all = syl.coverage("内科学")
    assert cov_all["totals"]["items"] == 4      # 3 seed + 1 teacher
    cov_t = syl.coverage("内科学", "teacher")
    assert cov_t["totals"]["items"] == 1 and cov_t["totals"]["pending"] == 1


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
    r4 = c.post("/api/syllabus/parse", json={"text": "一、呼吸系统\n1、肺通气\n2、肺换气",
                                             "subject": "内科学"})
    assert r4.status_code == 200 and r4.json()["mode"] == "structured"
    assert r4.json()["drafts"][0]["chapter"] == "呼吸系统"
    assert r4.json()["drafts"][0]["item"] == "肺通气"


def test_route_syllabus_item_delete(subject_seed):
    """删除大纲条目：DELETE /api/syllabus/items/{id} → 覆盖总数 -1；重复删除 → 404。"""
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    cov = c.get("/api/syllabus/coverage", params={"subject": "内科学"}).json()
    before = cov["totals"]["items"]
    item_id = next(it["id"] for ch in cov["chapters"] for it in ch["items"] if it.get("id"))
    r = c.delete(f"/api/syllabus/items/{item_id}")
    assert r.status_code == 200 and r.json()["ok"] is True
    after = c.get("/api/syllabus/coverage", params={"subject": "内科学"}).json()["totals"]["items"]
    assert after == before - 1
    assert c.delete(f"/api/syllabus/items/{item_id}").status_code == 404


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


# ---------------------------------------------------------------- K3/IMP-13：官方大纲文件输入路径 + 契约抽取
def test_split_subjects_anchor_and_md_headers():
    # 「考查内容」锚点 + Markdown 标题前缀都识别；锚点前的非考点文本不参与。
    text = ("# 大纲\n\n## 第四部分 考查内容\n### 一、生理学\n#### （一）绪论\n\n体液及其组成。\n"
            "### 二、生物化学\n#### （一）生物大分子\n\n氨基酸结构。\n")
    subs = syl.split_subjects(text)
    assert [n for n, _ in subs] == ["生理学", "生物化学"]
    assert "体液及其组成。" in subs[0][1]
    assert "第四部分" not in subs[0][1] and "考查内容" not in subs[0][1]


def test_extract_outline_fake_client_merge_and_errors():
    # 一科契约成功、一科异常 → 合并成功科 + errors 记录（不整体失败）。
    class FakeClient:
        def __init__(self):
            self.n = 0

        def chat_json(self, messages, **kw):
            self.n += 1
            from medkit.core.schema import OutlineSubject
            if self.n == 2:
                return OutlineSubject.model_validate(
                    {"name": "生物化学", "chapters": [{"name": "生物大分子", "items": ["氨基酸结构"]}]})
            raise RuntimeError("boom")

    text = ("考查内容\n一、生理学\n绪论\n体液。\n二、生物化学\n生物大分子\n氨基酸结构。\n")
    out = syl.extract_outline(text, client=FakeClient())
    assert out is not None
    assert [s["name"] for s in out["subjects"]] == ["生物化学"]
    assert any("生理学" in e for e in out["errors"])
    # 全部失败 → None（调用方走本地规则兜底）
    class FailClient:
        def chat_json(self, messages, **kw):
            raise RuntimeError("boom")

    assert syl.extract_outline(text, client=FailClient()) is None


def test_outline_drafts_shape():
    outline = {"exam": "306", "subjects": [
        {"name": "生理学", "chapters": [
            {"name": "绪论", "items": ["体液", "稳态"]}]}]}
    drafts = syl.outline_drafts(outline)
    assert drafts == [{"subject": "生理学", "chapter": "绪论", "item": "体液"},
                      {"subject": "生理学", "chapter": "绪论", "item": "稳态"}]


def test_add_seed_items_idempotent():
    r1 = syl.add_seed_items([{"subject": "生理学", "chapter": "绪论", "item": "体液"}])
    assert r1["added"] == 1 and r1["total"] == 1 and r1["subjects"] == ["生理学"]
    r2 = syl.add_seed_items([{"subject": "生理学", "chapter": "绪论", "item": "体液"}])
    assert r2["added"] == 0  # 幂等
    cov = syl.coverage("生理学", "seed")
    assert cov["totals"]["items"] == 1


def test_route_seed_parse_file_llm_path(monkeypatch):
    from fastapi.testclient import TestClient

    from medkit import main as m
    outline = {"exam": "306", "subjects": [
        {"name": "内科学", "chapters": [{"name": "呼吸", "items": ["肺通气", "肺炎"]}]}]}
    monkeypatch.setattr(syl, "extract_outline", lambda text, **kw: outline)
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/syllabus/seed/parse-file",
               files={"file": ("大纲.md", "# 306\n\n考查内容\n".encode("utf-8"), "text/markdown")})
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "llm" and j["count"] == 2
    assert j["drafts"][0] == {"subject": "内科学", "chapter": "呼吸", "item": "肺通气"}


def test_route_seed_import_file_persists(monkeypatch):
    from fastapi.testclient import TestClient

    from medkit import main as m
    outline = {"exam": "306", "subjects": [
        {"name": "内科学", "chapters": [{"name": "呼吸", "items": ["肺通气"]}]}]}
    monkeypatch.setattr(syl, "extract_outline", lambda text, **kw: outline)
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/syllabus/seed/import-file",
               files={"file": ("大纲.md", "考查内容\n一、内科学\n".encode("utf-8"), "text/markdown")})
    assert r.status_code == 200
    j = r.json()
    assert j["source"] == "seed" and j["added"] == 1
    cov = syl.coverage("内科学", "seed")
    assert cov["totals"]["items"] == 1


def test_route_seed_parse_file_rejects_bad_ext():
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/syllabus/seed/parse-file",
               files={"file": ("大纲.pdf", b"x", "application/pdf")})
    assert r.status_code == 400


# ---------------------------------------------------------------- 教师重点 v4：知识点提取 + 文件通道
def test_extract_teacher_kps_normalize_and_dedupe():
    drafts = [
        {"subject": "内科学", "chapter": "呼吸系统疾病", "item": "重点掌握：肺通气与换气"},
        {"subject": "内科学", "chapter": "呼吸系统疾病", "item": "肺通气与换气"},
        {"subject": "内科学", "chapter": "循环系统疾病", "item": "心衰的 NYHA 分级"},
        {"subject": "内科学", "chapter": "循环", "item": "超长条目" + "、依次罗列入院指征" * 8},
        {"subject": "", "chapter": "", "item": ""},
    ]
    kps = syl.extract_teacher_kps(drafts)
    names = [k["name"] for k in kps]
    assert names[0] == "肺通气与换气"          # 前缀「重点掌握：」剔除 + 去重
    assert len(names) == len(set(names))       # 保序去重
    assert all(len(k["name"]) <= 40 for k in kps)   # 超长条目截断到 ≤40
    assert kps[0]["subject"] == "内科学" and kps[0]["chapter"] == "呼吸系统疾病"


def test_import_teacher_text_knowledge_field():
    # structured：章/条目 → drafts + knowledge 同生
    r1 = syl.import_teacher_text("一、呼吸系统\n1、肺通气\n2、肺炎", subject="内科学")
    assert r1["mode"] == "structured" and r1["drafts"][0]["item"] == "肺通气"
    assert len(r1["knowledge"]) == 2 and r1["knowledge"][0]["name"] == "肺通气"
    # flat：无编号要点行
    r2 = syl.import_teacher_text("肺通气与换气\n肺炎链球菌首选青霉素", subject="内科学")
    assert r2["mode"] == "flat" and len(r2["knowledge"]) == 2
    # none：空文本
    r3 = syl.import_teacher_text("", subject="内科学")
    assert r3["mode"] == "none" and r3["knowledge"] == []


def test_route_teacher_import_file_md():
    from fastapi.testclient import TestClient

    from medkit import main as m
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/syllabus/teacher/import-file",
               files={"file": ("教师重点.md",
                               "一、呼吸系统\n1、肺通气\n2、肺炎\n3、肺结核\n4、呼吸衰竭\n".encode("utf-8"),
                               "text/markdown")},
               data={"subject": "内科学"})
    assert r.status_code == 200
    j = r.json()
    assert j["mode"] == "structured" and j["added"] == 4
    assert j["knowledge"] and j["knowledge"][0]["name"] == "肺通气"
    cov = syl.coverage("内科学", "teacher")
    assert cov["totals"]["items"] == 4

    # R3-25：preview=True 只解析不落库（草稿→确认两段式的后端支撑）
    rp = c.post("/api/syllabus/teacher/import-file",
                files={"file": ("教师重点2.md",
                                "一、呼吸系统\n1、肺通气\n2、肺炎\n3、肺结核\n4、呼吸衰竭\n".encode("utf-8"),
                                "text/markdown")},
                data={"subject": "内科学", "preview": "1"})
    assert rp.status_code == 200
    jp = rp.json()
    assert jp.get("preview") is True and jp["added"] == 0 and len(jp["drafts"]) == 4
    cov2 = syl.coverage("内科学", "teacher")
    assert cov2["totals"]["items"] == 4, "preview 模式不应落库"
