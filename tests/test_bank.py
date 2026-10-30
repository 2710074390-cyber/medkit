# -*- coding: utf-8 -*-
"""题库域（`core/bank.py` + `routers/bank.py`）测试：迁移 / CRUD / 导入 / 作答判分 / 判错回流 / 统计。

隔离：`tests/conftest.py` 的 autouse fixture 已把 `dbs.DB_PATH` / `LIBRARY_DIR` 与各域文件常量
重定向到本用例的 tmp_path；本文件不触碰真实 `~/.medkit`。

判分口径（与导出的押题卷同源）：**集合比较**——`"B D"` == `"BD"` == `"D,B"`。
"""

from __future__ import annotations

import ast
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.core.bank as bank  # noqa: E402
import medkit.core.config as cfg  # noqa: E402
import medkit.core.db as dbs  # noqa: E402
import medkit.core.library as lib  # noqa: E402
import medkit.main as m  # noqa: E402

_MD = """# 呼吸系统题库
### q1 · A1 型 · 单选 · 理解
**慢阻肺的诊断**
慢阻肺最主要的病因是
- A. 吸烟
- B. 感染
**✅ 答案：A**
💡 吸烟是主要危险因素

### q2 · X 型 · 多选 · 应用
**心衰治疗**
下列属于治疗要点的是
- A. 限盐
- B. 利尿
- C. 绝对卧床
**✅ 答案：AC**
💡 限盐与利尿是基石
"""


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


def _make_bank(client, name="呼吸系统", items=None):
    r = client.post("/api/bank/import/commit", json={
        "name": name, "subject": "内科学",
        "items": items if items is not None else _parsed(client),
    })
    assert r.status_code == 200, r.text
    return r.json()["bank_id"]


def _parsed(client):
    r = client.post("/api/bank/import/preview", json={"text": _MD, "fmt": "md"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ---------------------------------------------------------------- 迁移
def test_migration_creates_bank_tables_and_is_idempotent():
    assert dbs.migrate() == dbs.MIGRATIONS[-1]
    conn = dbs.get_conn()
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"banks", "bank_items", "bank_attempts"} <= names
    # 幂等：重复调用不再前进版本、不报错
    assert dbs.migrate() == dbs.MIGRATIONS[-1]
    assert dbs.user_version() == dbs.MIGRATIONS[-1]


# ---------------------------------------------------------------- 题库 CRUD
def test_create_list_get_rename_delete_bank(client):
    bid = _make_bank(client)
    lst = client.get("/api/bank/banks").json()["banks"]
    assert [b["id"] for b in lst] == [bid]
    assert lst[0]["items"] == 2 and lst[0]["name"] == "呼吸系统"

    detail = client.get(f"/api/bank/banks/{bid}").json()
    assert len(detail["items"]) == 2
    assert detail["stats"]["items"] == 2

    assert client.put(f"/api/bank/banks/{bid}", json={"name": "呼吸系统(改)"}).json()["bank"]["name"] == "呼吸系统(改)"

    r = client.delete(f"/api/bank/banks/{bid}")
    assert r.status_code == 200
    j = r.json()
    assert j["deleted"] is True and j["items"] == 2
    assert j["backup"], "删除题库必须先导出备份（可追溯）"
    assert Path(j["backup"]).is_file()
    assert client.get("/api/bank/banks").json()["banks"] == []
    assert client.get(f"/api/bank/banks/{bid}").status_code == 404


def test_create_bank_requires_name(client):
    assert client.post("/api/bank/banks", json={"name": "  "}).status_code == 400


# ---------------------------------------------------------------- 导入
def test_preview_does_not_persist(client):
    r = client.post("/api/bank/import/preview", json={"text": _MD, "fmt": "md"})
    assert r.status_code == 200 and r.json()["total"] == 2
    assert client.get("/api/bank/banks").json()["banks"] == [], "预览不得落库"


def test_preview_empty_text_is_400(client):
    assert client.post("/api/bank/import/preview", json={"text": "  ", "fmt": "md"}).status_code == 400


def test_import_file_preview(client):
    r = client.post("/api/bank/import/file",
                    files={"file": ("bank.md", _MD.encode("utf-8"), "text/markdown")})
    assert r.status_code == 200 and r.json()["total"] == 2


@pytest.mark.parametrize("dedupe,expect", [
    ("update", {"added": 0, "updated": 2, "skipped": 0}),
    ("skip", {"added": 0, "updated": 0, "skipped": 2}),
    ("new", {"added": 2, "updated": 0, "skipped": 0}),
])
def test_import_dedupe_strategies(client, dedupe, expect):
    """重复题三选一（对齐 Anki 文本导入的「更新 / 忽略 / 新建」）。"""
    bid = _make_bank(client)
    r = client.post("/api/bank/import/commit", json={
        "bank_id": bid, "items": _parsed(client), "dedupe": dedupe})
    assert r.status_code == 200
    j = r.json()
    assert {k: j[k] for k in ("added", "updated", "skipped")} == expect
    n = len(client.get(f"/api/bank/banks/{bid}").json()["items"])
    assert n == (2 if dedupe != "new" else 4)


def test_import_commit_without_items_is_400(client):
    assert client.post("/api/bank/import/commit", json={"items": []}).status_code == 400


# ---------------------------------------------------------------- 练习与判分
def test_drill_never_leaks_answers(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    assert len(items) == 2
    for it in items:
        assert "answer" not in it and "analysis" not in it, "练习题目不得带答案/解析"


@pytest.mark.parametrize("user,correct", [
    ("A", True), ("a", True), (" B ", False), ("", False),
])
def test_grade_single_choice(client, user, correct):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    q1 = next(i for i in items if "慢阻肺" in i["question"])
    j = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": q1["id"], "user_answer": user}).json()
    assert j["correct"] is correct, (user, j)


@pytest.mark.parametrize("user,correct", [
    ("AC", True), ("A,C", True), ("CA", True), ("c a", True),
    ("A", False), ("ABC", False), ("", False),
])
def test_grade_multi_choice_is_set_semantics(client, user, correct):
    """X 型判分 = 集合比较（顺序/分隔符无关）——与 `answersEqual()` 同口径。"""
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    q2 = next(i for i in items if i.get("subtopic") == "心衰治疗")     # answer AC
    j = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": q2["id"], "user_answer": user}).json()
    assert j["correct"] is correct, (user, j)
    assert j["answer"] == "AC" and "限盐" in j["analysis"]


def test_grade_returns_analysis_and_syncs_wrong_answer_to_mistakes(client):
    """判错 → 自动回流错题本（复用 `library.sync_from_paper`，含 user_answer）。"""
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    q1 = next(i for i in items if "慢阻肺" in i["question"])
    assert lib.list_mistakes() == []

    j = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": q1["id"], "user_answer": "B"}).json()
    assert j["correct"] is False and j["synced_mistakes"] == 1
    ms = lib.list_mistakes()
    assert len(ms) == 1
    assert ms[0]["question"] == "慢阻肺最主要的病因是"
    assert ms[0]["user_answer"] == "B", "回流必须带上用户作答（否则错题本无法复盘）"
    assert ms[0]["answer"] == "A"
    # 回流必须带**题库科目**：否则错题与派生知识点落进「未分类」，不便按科目检索，
    # 也会让「铺卡（薄弱点全部入队）」把别处的卡铺进来。
    assert ms[0]["subject"] == "内科学", ms[0]["subject"]
    # P1-3：题库练习回流的 source 必须是 "bank"，不是 "paper"
    assert ms[0].get("source") == "bank", f"来源应为 bank，实得 {ms[0].get('source')}"

    # 再判错同一题 → 不重复入库（sync_from_paper 按 question_id 去重）
    j2 = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": q1["id"], "user_answer": "C"}).json()
    assert j2["synced_mistakes"] == 0
    assert len(lib.list_mistakes()) == 1

    # 判对 → 不回流
    j3 = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": q1["id"], "user_answer": "A"}).json()
    assert j3["correct"] is True and j3["synced_mistakes"] == 0


def test_source_fields_flow_to_grade_and_mistake(client):
    """A-1 出处：题库题的 `source_type`/`source_year` 必须出现在**判分结果**与**回流错题**里。

    证伪：删掉 `bank.grade()` 返回里的 source_* 两行 / 删掉 `library.add_mistake` 白名单里的
    source_* 两行 → 本用例必红（后者正是「白名单式固定形状字典静默丢字段」的典型陷阱）。
    """
    bid = _make_bank(client, name="出处题库", items=[{
        "question": "慢阻肺最主要的病因是", "options": ["吸烟", "感染"],
        "answer": "A", "source_type": "真题", "source_year": "2024",
    }])
    it = client.get(f"/api/bank/banks/{bid}/drill").json()["items"][0]
    j = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": it["id"], "user_answer": "B"}).json()
    assert j["source_type"] == "真题", j
    assert j["source_year"] == "2024", j
    ms = lib.list_mistakes()
    assert len(ms) == 1, ms
    assert ms[0]["source_type"] == "真题", ms[0]
    assert ms[0]["source_year"] == "2024", ms[0]


def test_grade_rejects_item_outside_bank(client):
    bid = _make_bank(client)
    other = _make_bank(client, name="别的题库")
    items = client.get(f"/api/bank/banks/{other}/drill").json()["items"]
    r = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": items[0]["id"], "user_answer": "A"})
    assert r.status_code == 404, "跨题库判分必须拒绝（否则统计会被污染）"


def test_grade_missing_item_is_400(client):
    assert client.post("/api/bank/grade", json={"bank_id": "b", "item_id": ""}).status_code == 400


# ---------------------------------------------------------------- 统计（黄金值）
def test_stats_golden_values(client):
    """统计量必须与手算常量对齐（禁止「端点 == 另一个端点」式恒真判据）。"""
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    q1 = next(i for i in items if "慢阻肺" in i["question"])   # answer A
    q2 = next(i for i in items if i.get("subtopic") == "心衰治疗")     # answer AC     # answer AC
    # q1: 错一次 → 对一次（最后一次对 ⇒ 掌握）；q2: 对一次
    for body in ({"item_id": q1["id"], "user_answer": "B"},
                 {"item_id": q1["id"], "user_answer": "A"},
                 {"item_id": q2["id"], "user_answer": "AC"}):
        assert client.post("/api/bank/grade", json={"bank_id": bid, **body}).status_code == 200
    s = client.get(f"/api/bank/banks/{bid}/stats").json()["stats"]
    assert s["items"] == 2
    assert s["attempts"] == 3
    assert s["correct"] == 2
    assert s["attempted"] == 2
    assert s["mastered"] == 2
    assert s["accuracy"] == round(2 / 3, 4)
    assert s["mastery"] == 1.0
    weak = {t["topic"]: t for t in s["weak_topics"]}
    assert weak["慢阻肺的诊断"]["attempts"] == 2 and weak["慢阻肺的诊断"]["correct"] == 1
    assert weak["心衰治疗"]["accuracy"] == 1.0


def test_drill_modes_wrong_and_undone(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    q1 = next(i for i in items if "慢阻肺" in i["question"])
    q2 = next(i for i in items if i.get("subtopic") == "心衰治疗")     # answer AC
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": q1["id"], "user_answer": "B"})
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": q2["id"], "user_answer": "AC"})

    wrong = client.get(f"/api/bank/banks/{bid}/drill?mode=wrong").json()["items"]
    assert [i["id"] for i in wrong] == [q1["id"]]
    undone = client.get(f"/api/bank/banks/{bid}/drill?mode=undone").json()["items"]
    assert undone == []


def test_list_banks_reports_mastery_and_accuracy(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": items[0]["id"], "user_answer": "A"})
    # 按 id 取**本题库**（不用 `banks[0]`：列表顺序不该被本用例假设为「只有一个题库」）
    b = next(x for x in client.get("/api/bank/banks").json()["banks"] if x["id"] == bid)
    assert b["attempted"] == 1 and b["mastery"] == 0.5 and b["accuracy"] == 1.0


def test_new_id_is_lexicographically_monotonic_across_digit_boundary(monkeypatch):
    """`_new_id` 的序号必须**定宽补零**——`ORDER BY created_at, id` 靠它定序。

    证伪：把 `:06d` 去掉 ⇒ 序号 9 → 10 时 `"…_9" > "…_10"`（字符串比较）⇒ 本用例必红。
    实测影响：题目顺序会翻（练习第 1 题变第 2 题），且用例结果随「文件执行顺序」变化
    （`test_bank_iq.py` 先跑会消耗 `_ID_SEQ`，把边界推到 9/10）。
    """
    import itertools
    monkeypatch.setattr(bank, "_ID_SEQ", itertools.count(8))   # 起点 8 ⇒ 覆盖 9 → 10 边界
    ids = [bank._new_id("qi") for _ in range(5)]
    assert ids == sorted(ids), f"id 跨位数后不再字典序单调：{ids}"


def test_list_items_keeps_insertion_order_across_id_digit_boundary(monkeypatch, client):
    """入库原序必须稳定：`drill` 的第 1 题就是导入时的第 1 题（跨 id 位数边界也一样）。"""
    import itertools
    monkeypatch.setattr(bank, "_ID_SEQ", itertools.count(8))
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    assert items[0]["question"] == "慢阻肺最主要的病因是", \
        f"第 1 题应是导入时的第 1 题，实得 {items[0]['question']!r}"
    assert items[1]["question"] == "下列属于治疗要点的是"


# 案例组（A3）：两道子题共用同一段案例题干，只有提问与选项不同。
# ⚠️ 案例题干必须**长于 80 个归一字符**（否则前 80 字已含提问 ⇒ 测不到宽松键误判）。
_CASE_STEM = ("男性,30岁。自服农药约100ml后30分钟被家人送来急诊。查体:意识模糊,全身湿冷,"
              "双侧瞳孔针尖样缩小,口鼻大量分泌物,呼吸急促,双肺满布湿啰音,可见颜面及四肢肌纤维颤动。")
_CASE_MD = (
    "# 案例组题库\n\n"
    "### 1. [A3型题｜应用] c-1\n\n"
    f"**{_CASE_STEM}该患者最可能的诊断是**\n\n"
    "- A. 急性有机磷杀虫剂中毒\n- B. 急性百草枯中毒\n\n**答案：A**\n\n---\n\n"
    "### 2. [A3型题｜应用] c-2\n\n"
    f"**{_CASE_STEM}对该患者应首先采取的治疗措施是**\n\n"
    "- A. 清水洗胃\n- B. 立即行血液灌流治疗\n\n**答案：A**\n\n---\n"
)


def test_import_does_not_merge_case_series_subquestions(client):
    """案例组子题必须**各自入库**——不得被宽松去重键覆盖丢失。

    证伪：`bank_iq.loose_key` 去掉选项分量 ⇒ 两道子题同键 ⇒ `added` 变 1、丢 1 题。
    实测 batch035：4 组 9 题被合并，220 题导入后只剩 215。
    """
    pv = client.post("/api/bank/import/preview", json={"text": _CASE_MD, "fmt": "md"})
    assert pv.status_code == 200, pv.text
    assert pv.json()["total"] == 2, pv.json()
    # 前提守卫（与 `bank_iq.loose_key` 同口径：**先截断 raw[:80] 再归一**）：
    # 两道子题的前 80 字必须相同，否则本用例测不到「宽松键误判」。
    from medkit.core import bank_iq as _iq
    assert _iq._norm_q((_CASE_STEM + "该患者最可能的诊断是")[:80]) == \
        _iq._norm_q((_CASE_STEM + "对该患者应首先采取的治疗措施是")[:80]), \
        "本用例前提不成立：案例题干太短 ⇒ 测不到宽松键误判"
    r = client.post("/api/bank/import/commit", json={
        "name": "案例组", "subject": "急诊", "items": pv.json()["items"]})
    assert r.status_code == 200, r.text
    j = r.json()
    assert (j["added"], j["updated"], j["skipped"]) == (2, 0, 0), j
    items = client.get(f"/api/bank/banks/{j['bank_id']}/drill").json()["items"]
    assert len(items) == 2, f"两道子题都应入库，实得 {len(items)}"
    assert {i["question"] for i in items} == {
        _CASE_STEM + "该患者最可能的诊断是",
        _CASE_STEM + "对该患者应首先采取的治疗措施是"}, [i["question"] for i in items]


def test_delete_item_removes_attempts(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    iid = items[0]["id"]
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": iid, "user_answer": "A"})
    assert client.delete(f"/api/bank/items/{iid}").status_code == 200
    assert client.delete(f"/api/bank/items/{iid}").status_code == 404
    s = client.get(f"/api/bank/banks/{bid}/stats").json()["stats"]
    assert s["items"] == 1 and s["attempts"] == 0, "删题必须连带删掉它的作答流水"


# ---------------------------------------------------------------- 题目编辑 / 收藏笔记（F2 / F9）
def _first_item(client, bid):
    return client.get(f"/api/bank/banks/{bid}/drill").json()["items"][0]


def test_add_item_manual_and_reject_empty(client):
    bid = _make_bank(client)
    r = client.post(f"/api/bank/banks/{bid}/items", json={
        "question": "手加题", "options": ["甲", "乙"], "answer": "b", "analysis": "解",
        "type": "A1", "subtopic": "考点"})
    assert r.status_code == 200 and r.json()["added"] == 1
    items = client.get(f"/api/bank/banks/{bid}").json()["items"]
    added = next(i for i in items if i["question"] == "手加题")
    assert added["answer"] == "B", "答案必须归一为大写（与导入同一条通道）"
    assert added["origin"] == "manual"
    assert client.post(f"/api/bank/banks/{bid}/items", json={"question": "  "}).status_code == 400
    assert client.post("/api/bank/banks/nope/items", json={"question": "题"}).status_code == 404


def test_update_item_partial_patch_normalizes_and_keeps_id(client):
    bid = _make_bank(client)
    it = next(i for i in client.get(f"/api/bank/banks/{bid}").json()["items"]
              if "慢阻肺" in i["question"])
    r = client.put(f"/api/bank/items/{it['id']}", json={"answer": "b", "options": ["A. 吸烟", "乙"]})
    assert r.status_code == 200
    rec = r.json()["item"]
    assert rec["id"] == it["id"], "id 不可改（作答流水按它关联）"
    assert rec["answer"] == "B"
    assert rec["options"] == ["吸烟", "乙"], "编辑同样要剥字母前缀"
    assert rec["question"] == it["question"], "局部更新不得动未提交的字段"
    assert rec["created_at"] == it["created_at"]
    # 落库生效（不是只改了返回值）
    again = next(i for i in client.get(f"/api/bank/banks/{bid}").json()["items"]
                 if i["id"] == it["id"])
    assert again["answer"] == "B" and again["options"] == ["吸烟", "乙"]


def test_update_item_errors(client):
    bid = _make_bank(client)
    it = _first_item(client, bid)
    assert client.put(f"/api/bank/items/{it['id']}", json={"question": "   "}).status_code == 400
    assert client.put("/api/bank/items/nope", json={"answer": "A"}).status_code == 404


def test_update_item_does_not_touch_attempts(client):
    bid = _make_bank(client)
    it = _first_item(client, bid)
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": it["id"], "user_answer": "A"})
    assert client.get(f"/api/bank/banks/{bid}/stats").json()["stats"]["attempts"] == 1
    client.put(f"/api/bank/items/{it['id']}", json={"analysis": "补充解析"})
    s = client.get(f"/api/bank/banks/{bid}/stats").json()["stats"]
    assert s["attempts"] == 1, "改题不得清掉作答历史"


def test_star_item_and_starred_drill_mode(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}").json()["items"]
    a, b = items[0], items[1]
    r = client.post(f"/api/bank/items/{a['id']}/star", json={"starred": True})
    assert r.status_code == 200 and r.json()["item"]["starred"] is True
    assert client.post("/api/bank/items/nope/star", json={"starred": True}).status_code == 404

    starred = client.get(f"/api/bank/banks/{bid}/drill?mode=starred").json()["items"]
    assert [i["id"] for i in starred] == [a["id"]]
    assert starred[0]["starred"] is True
    # 取消收藏 → 集合变空
    client.post(f"/api/bank/items/{a['id']}/star", json={"starred": False})
    assert client.get(f"/api/bank/banks/{bid}/drill?mode=starred").json()["items"] == []
    assert b["starred"] is False


def test_note_is_hidden_in_drill_but_returned_after_grading(client):
    """笔记可能写着答案 ⇒ 判分前不下发；判分后随结果回传。"""
    bid = _make_bank(client)
    it = _first_item(client, bid)
    client.put(f"/api/bank/items/{it['id']}", json={"note": "这题答案是 A，别再错"})
    drill = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    assert all("note" not in d for d in drill), "练习题目不得带笔记"
    j = client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": it["id"], "user_answer": "A"}).json()
    assert j["note"] == "这题答案是 A，别再错"


def test_edit_imported_answer_takes_effect_in_grading(client):
    """改完答案，判分口径立刻跟着变（改题不是「显示层」的事）。"""
    bid = _make_bank(client)
    it = next(i for i in client.get(f"/api/bank/banks/{bid}").json()["items"]
              if "慢阻肺" in i["question"])
    assert client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": it["id"], "user_answer": "A"}).json()["correct"] is True
    client.put(f"/api/bank/items/{it['id']}", json={"answer": "B"})
    assert client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": it["id"], "user_answer": "A"}).json()["correct"] is False
    assert client.post("/api/bank/grade", json={
        "bank_id": bid, "item_id": it["id"], "user_answer": "B"}).json()["correct"] is True


# ---------------------------------------------------------------- 每日目标（B7）
def test_today_attempts_counts_today_only(client):
    bid = _make_bank(client)
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": items[0]["id"], "user_answer": "A"})
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": items[1]["id"], "user_answer": "A"})
    today = client.get("/api/bank/today").json()
    assert today["attempts"] == 2 and today["correct"] == 1 and today["items"] == 2
    # 指定日期（过去）→ 0（证明真的按日期过滤，不是恒返回总数）
    past = client.get("/api/bank/today?date=1999-01-01").json()
    assert past["attempts"] == 0 and past["items"] == 0
    assert past["date"] == "1999-01-01"
    # 题被删掉后流水一并消失
    client.delete(f"/api/bank/items/{items[0]['id']}")
    assert client.get("/api/bank/today").json()["attempts"] == 1


# ---------------------------------------------------------------- 分层契约
def test_routers_bank_never_touches_sql_directly():
    """`routers → core → db` 单向：routers 不得直接 `cur.execute` / `dbs.tx` / `dbs.get_conn`。"""
    tree = ast.parse((ROOT / "medkit" / "routers" / "bank.py").read_text(encoding="utf-8"))
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("execute", "executemany", "tx", "get_conn"):
            bad.append(f"{node.attr} @ line {node.lineno}")
    assert not bad, "routers/bank.py 直接碰了 SQL 层：" + ", ".join(bad)


def test_core_bank_does_not_import_routers():
    """`core` 不得 import `routers`（跨层反向依赖）。"""
    tree = ast.parse((ROOT / "medkit" / "core" / "bank.py").read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("..routers"):
            hits.append(node.module)
        if isinstance(node, ast.Import):
            hits += [a.name for a in node.names if a.name.startswith("medkit.routers")]
    assert not hits, f"core/bank.py 反向依赖 routers：{hits}"


def test_answers_equal_matches_frontend_semantics():
    """判分口径与前端 `answersEqual()` 同源（集合比较）。"""
    assert bank.answers_equal("B D", "BD")
    assert bank.answers_equal("d,b", "BD")
    assert not bank.answers_equal("", "BD")
    assert not bank.answers_equal("A", "BD")


# ---------------------------------------------------------------- 从出题工坊导入（F11）
@pytest.fixture()
def projects_root(tmp_path, monkeypatch):
    """临时项目根。

    ⚠️ `cfg.DEFAULTS["projects_dir"]` 是**导入时**由真实 `CONFIG_DIR` 算出的常量，
    `tests/conftest.py` 只 patch 了 `CONFIG_DIR`（不影响已算好的默认值）⇒ 不显式改写这里，
    读 `cfg.load()["projects_dir"]` 会落到**真实** `~/.medkit/projects`（R5-01 哨兵会红）。
    """
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setitem(cfg.DEFAULTS, "projects_dir", str(root))
    return root


def _seed_project(root: Path, pid: str, subject: str, questions: list[dict]) -> None:
    d = root / pid / "最终产物"
    d.mkdir(parents=True)
    (root / pid / "meta.json").write_text(json.dumps({"subject": subject}), encoding="utf-8")
    (d / "questions_final.json").write_text(
        json.dumps(questions, ensure_ascii=False), encoding="utf-8")


def test_list_project_sources_only_lists_those_with_artifacts(client, projects_root):
    _seed_project(projects_root, "p1", "内科学",
                  [{"id": "q1", "question": "题一", "options": ["甲"], "answer": "A"}])
    (projects_root / "p2").mkdir()
    (projects_root / "p2" / "meta.json").write_text("{}", encoding="utf-8")   # 无产物 → 不列出
    r = client.get("/api/bank/projects").json()["projects"]
    assert [p["pid"] for p in r] == ["p1"]
    assert r[0]["subject"] == "内科学" and r[0]["count"] == 1


def test_import_from_project_keeps_media_and_group_fields(client, projects_root):
    """导入必须**搬运**案例/选项组/图/表字段——不搬运等于导入即降级。"""
    _seed_project(projects_root, "p1", "内科学", [
        {"id": "q1", "type": "A1", "question": "题一", "options": ["甲", "乙"], "answer": "A",
         "analysis": "解一", "subtopic": "考点一", "image_ref": "fig_1", "data_table": "|a|b|"},
        {"id": "q2", "type": "B1", "question": "共用选项题", "options": [], "answer": "B",
         "group_kind": "option_group", "group": {"options": ["甲", "乙"]}},
    ])
    r = client.post("/api/bank/import/project", json={"pid": "p1"}).json()
    assert r["added"] == 2 and r["skipped"] == 0
    items = client.get(f"/api/bank/banks/{r['bank_id']}").json()["items"]
    by_q = {i["question"]: i for i in items}
    assert by_q["题一"]["image_ref"] == "fig_1"
    assert by_q["题一"]["data_table"] == "|a|b|"
    assert by_q["题一"]["subtopic"] == "考点一"
    assert by_q["共用选项题"]["group_kind"] == "option_group"
    assert by_q["共用选项题"]["group"]["options"] == ["甲", "乙"]
    assert all(i["origin"] == "project" for i in items)
    b = client.get(f"/api/bank/banks/{r['bank_id']}").json()["bank"]
    assert b["source"] == "project" and b["name"] == "内科学", b


def test_import_from_project_is_copy_not_reference(client, projects_root):
    """**复制而非引用**：删掉项目后题库仍在（题库不该跟着项目消失）。"""
    _seed_project(projects_root, "p1", "内科学",
                  [{"id": "q1", "question": "题一", "options": ["甲"], "answer": "A"}])
    bid = client.post("/api/bank/import/project", json={"pid": "p1"}).json()["bank_id"]
    shutil.rmtree(projects_root / "p1")
    assert len(client.get(f"/api/bank/banks/{bid}").json()["items"]) == 1


def test_import_from_project_rejects_bad_input(client, projects_root):
    assert client.post("/api/bank/import/project", json={"pid": ""}).status_code == 400
    assert client.post("/api/bank/import/project", json={"pid": "nope"}).status_code == 404
    # 路径穿越：pid 走 _safe_pid 消毒（不得读到项目根之外）
    assert client.post("/api/bank/import/project", json={"pid": "../x"}).status_code == 400
    _seed_project(projects_root, "p3", "空项目", [])
    assert client.post("/api/bank/import/project", json={"pid": "p3"}).status_code == 400


def test_import_from_project_into_existing_bank(client, projects_root):
    bid = _make_bank(client)
    _seed_project(projects_root, "p1", "内科学",
                  [{"id": "q1", "question": "慢阻肺最主要的病因是", "options": ["吸烟", "感染"],
                    "answer": "A"}])
    r = client.post("/api/bank/import/project",
                    json={"pid": "p1", "bank_id": bid, "dedupe": "update"}).json()
    assert r["bank_id"] == bid and r["updated"] == 1, r   # 同题干 → 按 update 归并
    assert len(client.get(f"/api/bank/banks/{bid}").json()["items"]) == 2


# ---------------------------------------------------------------- 导出回流（F10）
def test_export_paper_html_is_inline_and_contains_questions(client):
    bid = _make_bank(client)
    r = client.get(f"/api/bank/banks/{bid}/export/paper")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "inline" in r.headers["content-disposition"], "缺省必须 inline（同源渲染）"
    assert "慢阻肺最主要的病因是" in r.text and "吸烟" in r.text
    assert client.get("/api/bank/banks/nope/export/paper").status_code == 404


# ---------------------------------------------------------------- 原文保存与重新解析（v10）
#
# 动机（2026-10-13 用户实测）：解析器修好第三方 md 写法后，**已入库的数据不会自动变干净**——
# 库里那份 231 题（11 个章节空壳 + 题干混进题号标签/选项/答案）只能删库重导，而原文早已不在。
# v10 起导入时保存原文，解析器再改进即可**一键重解析**已有题库。
_THIRD_PARTY_MD = """# 急诊与灾难医学 batch035 题库

## M1（8题）

### 1. [A1型题｜理解] batch035-M1-A1-001

> 模块：M1｜页码：教材P1

**急诊医学的核心内涵是**

- A. 院前急救、院内急诊和危重症监护一体化模式
- **B. 早期判断,迅速救治,保护脏器功能生命安全** ✅
- C. 急救人员、车辆、通信的调动准备和现场急救
- D. 对灾难的预见、预防和准备及灾后卫生防疫
- E. 对慢性疾病进行长期随访和康复治疗

**答案：B**
"""


def test_import_commit_saves_source_for_reparse(client):
    """入库时提交 raw ⇒ 保存原文（`has_source`），供解析器升级后重解析。"""
    r = client.post("/api/bank/import/commit", json={
        "name": "第三方库", "subject": "", "items": _parsed(client), "raw": _MD, "fmt": "md"})
    bid = r.json()["bank_id"]
    b = next(x for x in client.get("/api/bank/banks").json()["banks"] if x["id"] == bid)
    assert b["has_source"] is True and b["parser_stale"] is False, b
    src = bank.get_source(bid)
    assert src and src["raw_text"] == _MD and src["fmt"] == "md"


def test_import_without_raw_has_no_source(client):
    """不传 raw（旧客户端 / 手工建库）⇒ 没有原文——只是少了重解析能力，不影响导入。"""
    bid = _make_bank(client, name="无原文")
    b = next(x for x in client.get("/api/bank/banks").json()["banks"] if x["id"] == bid)
    assert b["has_source"] is False and b["parser_stale"] is False, b
    assert bank.get_source(bid) is None


def test_reparse_fixes_stale_parsed_items(client):
    """**核心价值**：解析器改进后，已入库的旧解析产物可一键重解析修好。

    场景复刻 2026-10-13 用户实测：旧解析器把 `## M1（8题）` 章节标题当成一道题（空壳）、
    题干混进题号标签与溯源行、选项只抓到未加粗的行。这里手工灌入等价脏数据并保存原文，
    再 reparse ⇒ 题干干净、选项完整、空壳消失。
    """
    bid = client.post("/api/bank/banks", json={
        "name": "旧产物库", "subject": "", "source": "md_import"}).json()["bank"]["id"]
    bank.add_items(bid, [
        {"question": "M1（8题）", "options": [], "answer": ""},                      # 章节标题成题（空壳）
        {"question": "1. [A1型题｜理解] batch035-M1-A1-001\n> 模块：M1｜页码：教材P1\n\n**急诊医学的核心内涵是**",
         "options": ["院前急救、院内急诊和危重症监护一体化模式"], "answer": "B"},   # 题干混脚手架 + 选项错位
    ], origin="md_import", dedupe="new")
    bank.save_source(bid, _THIRD_PARTY_MD, fmt="md")
    # 前提守卫：脏数据确实是脏的（否则本用例测不到目标缺陷）
    dirty = bank.list_items(bid)
    assert len(dirty) == 2 and any(it["question"] == "M1（8题）" for it in dirty), "前提不成立：空壳没造出来"
    assert any("型题" in it["question"] for it in dirty), "前提不成立：题干没混进标签"

    res = bank.reparse_bank(bid)
    assert res["items"] == 1, res                    # 章节标题不再成题
    it = bank.list_items(bid)[0]
    assert it["question"] == "急诊医学的核心内涵是", it["question"]
    assert it["options"] == ["院前急救、院内急诊和危重症监护一体化模式",
                             "早期判断,迅速救治,保护脏器功能生命安全",
                             "急救人员、车辆、通信的调动准备和现场急救",
                             "对灾难的预见、预防和准备及灾后卫生防疫",
                             "对慢性疾病进行长期随访和康复治疗"], it["options"]
    assert it["answer"] == "B"


def test_reparse_clears_attempts_and_reports(client):
    """重解析重发题目 id ⇒ 清空该题库作答流水，并**如实回报**清了几条。"""
    bid = _make_bank(client, name="有作答")
    items = client.get(f"/api/bank/banks/{bid}/drill").json()["items"]
    client.post("/api/bank/grade", json={"bank_id": bid, "item_id": items[0]["id"], "user_answer": "A"})
    assert len(bank.list_attempts(bank_id=bid)) == 1
    client.post("/api/bank/import/commit",
                json={"bank_id": bid, "items": _parsed(client), "raw": _MD, "fmt": "md"})
    res = bank.reparse_bank(bid)
    assert res["attempts_cleared"] == 1, res
    assert bank.list_attempts(bank_id=bid) == []


def test_reparse_without_source_is_404(client):
    """没有原文 ⇒ 404（不静默返回空）。"""
    bid = _make_bank(client, name="无原文")
    r = client.post(f"/api/bank/banks/{bid}/reparse")
    assert r.status_code == 404
    assert "原文" in r.json()["detail"]


def test_delete_bank_also_removes_source(client):
    """删题库连带删原文（不留孤儿行）。"""
    bid = _make_bank(client, name="带原文")
    client.post("/api/bank/import/commit",
                json={"bank_id": bid, "items": _parsed(client), "raw": _MD, "fmt": "md"})
    assert bank.get_source(bid) is not None
    client.delete(f"/api/bank/banks/{bid}")
    assert bank.get_source(bid) is None


# ---------------------------------------------------------------- 跨题库搜题（Ctrl+K 第二段）
def test_search_finds_question_and_reports_hit_field(client):
    """搜题干里的词必须命中，并回报**命中字段**（前端徽标「题干 / 选项 / 考点…」的取值）。"""
    _make_bank(client)
    r = client.get("/api/bank/search", params={"q": "慢阻肺"}).json()
    assert r["ok"] is True and r["total"] == 1
    hit = r["items"][0]
    assert "慢阻肺" in hit["question"]
    assert hit["hit"] == "题干"
    assert hit["bank_subject"] == "内科学"
    assert hit["answer"] == "A"


def test_search_multi_token_requires_every_token(client):
    """多词 = AND。只命中一部分的题不该混进来——否则搜两个词等于搜并集，结果没有价值。"""
    _make_bank(client)
    assert client.get("/api/bank/search", params={"q": "慢阻肺 吸烟"}).json()["total"] == 1
    # 「慢阻肺」在 q1、「心衰」在 q2：没有一道题同时含这两个词
    assert client.get("/api/bank/search", params={"q": "慢阻肺 心衰"}).json()["total"] == 0


def test_search_reaches_options_not_just_question(client):
    """命中不该只在题干：选项里的词也要搜得到（「利尿」只在 q2 的选项/解析里）。"""
    _make_bank(client)
    r = client.get("/api/bank/search", params={"q": "利尿"}).json()
    assert r["total"] == 1
    assert r["items"][0]["hit"] == "选项"


def test_search_orders_question_hits_first(client):
    """题干命中排在「只在选项里出现」之前——搜题时题干命中才是用户要的。"""
    _make_bank(client, items=[
        {"question": "利尿剂的不良反应", "options": ["低钾", "高钾"], "answer": "A"},
        {"question": "心衰的用药原则", "options": ["限盐", "利尿"], "answer": "AB",
         "analysis": "利尿是关键"},
    ])
    items = client.get("/api/bank/search", params={"q": "利尿"}).json()["items"]
    assert [i["hit"] for i in items] == ["题干", "选项"]


def test_search_empty_query_returns_nothing(client):
    """空/纯空白输入返回空 —— 搜题入口不该在用户还没输入时把千题撑到前端。"""
    _make_bank(client)
    for q in ("", "   "):
        r = client.get("/api/bank/search", params={"q": q}).json()
        assert r["total"] == 0 and r["items"] == []


def test_search_limit_truncates_items_but_not_total(client):
    """`limit` 只截结果，`total` 仍是命中总数（否则前端没法说「共 N 条，显示前 M 条」）。"""
    _make_bank(client)
    r = client.get("/api/bank/search", params={"q": "是", "limit": 1}).json()
    assert r["total"] == 2 and len(r["items"]) == 1


def test_search_can_be_scoped_to_one_bank(client):
    """限定单库（题库内搜索复用同一实现）。"""
    b1 = _make_bank(client, name="库一")
    _make_bank(client, name="库二")
    assert client.get("/api/bank/search", params={"q": "慢阻肺"}).json()["total"] == 2
    one = client.get("/api/bank/search", params={"q": "慢阻肺", "bank_id": b1}).json()
    assert one["total"] == 1 and one["items"][0]["bank_id"] == b1


def test_like_escape_neutralizes_wildcards():
    """LIKE 预筛的通配符必须转义。

    不转义时搜 `%` 会变成「命中全库」、搜 `_` 会变成单字符通配——在「题目里含 50%」
    的医学题库里这不是假想。预筛只是**优化**（真正的判定在 `_search_rank`），
    但预筛退化成全表扫会让每次敲键都扫一遍题库。
    """
    assert bank._like_escape("50%") == "50\\%"
    assert bank._like_escape("a_b") == "a\\_b"
    assert bank._like_escape("c\\d") == "c\\\\d"
    # 反斜杠必须先转义，否则 `\%` 会被二次解读
    assert bank._like_escape("\\%") == "\\\\\\%"


def test_qtype_query_column_is_populated(client):
    """`bank_items.qtype` 是冗余查询列，而 data 里的字段名是 `type` ⇒ 必须真的写进去。

    同族前科：`topic` 曾因字段名是 `subtopic` 而恒为 NULL（索引白建、`WHERE topic=?`
    永远查不到），当时**没有任何测试会红**。`qtype` 是同一形态——本次一并纠正并钉住。
    """
    bid = _make_bank(client)
    rows = dbs.get_conn().execute(
        "SELECT qtype FROM bank_items WHERE bank_id=?", (bid,)).fetchall()
    assert rows, "测试前提不成立：该题库没有题目"
    assert all(r[0] for r in rows), f"qtype 列恒为空：{[r[0] for r in rows]}"
    assert {r[0] for r in rows} == {it["type"] for it in bank.list_items(bid)}
