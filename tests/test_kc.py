"""知识点图谱与学习者模型守卫（v0.22 阶段 2）。

## 这一组在守什么

阶段 2 补的是两处结构性断点：

- **题目没有稳定知识点身份** → `item_kc` 用 `syllabus_items.id`（稳定哈希）当主键；
- **全量作答没进学习者模型** → `kc_state` 承接全部作答（对与错）。

判据全部来自**可观测行为**，不是「源码里有没有调某个函数」：

1. BKT 的方向（答对升、答错降）与黄金值；
2. 收缩的冷启动与收敛（先验必须能自动失效）；
3. 规则匹配的「精确 / 模糊 / 不匹配」三态，且**语义近似的考点不得互相当候选**；
4. 回填的幂等与「不覆盖人工确认过的映射」；
5. `kc_state` 的冗余列与 `data` JSON 逐值一致（双写不变量）；
6. 接口契约（状态码 / 错误体 / 字段名）。

## 为什么「语义近似不得互相当候选」是一条硬判据

「慢阻肺的诊断」与「慢阻肺的治疗」字符上很像，但**是两个考点**。
挂错了 ⇒ 掌握度归到错的知识点 ⇒ 薄弱项表给出错误的建议 ⇒ 比不挂更糟。
所以模糊匹配只给候选、必须人工确认；只有归一后完全相等才自动确认。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit import main as m  # noqa: E402
from medkit.core import kc as kclib  # noqa: E402

POOL = [
    {"kc_id": "kc_a", "item": "慢性阻塞性肺疾病", "chapter": "呼吸系统", "subject": "内科学"},
    {"kc_id": "kc_b", "item": "慢性阻塞性肺疾病的诊断", "chapter": "呼吸系统", "subject": "内科学"},
    {"kc_id": "kc_c", "item": "急性心肌梗死", "chapter": "循环系统", "subject": "内科学"},
]


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


# ================================================================ 归一与匹配
def test_norm_text_collapses_formatting_variants():
    """**排版差异**必须归一：「慢阻肺 的诊断」==「慢阻肺（诊断）」。

    但「的」是**语义成分不是排版** ⇒「慢阻肺诊断」与「慢阻肺的诊断」**不该**被归一成同一个
    （少了它可能指完全不同的考点）。这条把「归一」的边界钉死：只动格式，不动语义。
    """
    a = kclib.norm_text("慢阻肺 的诊断")        # 去空白 → 保留「的」
    b = kclib.norm_text("慢阻肺（诊断）")        # 去括号 → 「的」不会凭空长出来
    assert a == "慢阻肺的诊断", a
    assert b == "慢阻肺诊断", b
    assert a != b, "「的」是语义成分，去括号不等于去「的」"


def test_norm_text_handles_fullwidth_and_case():
    assert kclib.norm_text("ＡＢＣ") == "abc"


def test_rule_candidates_exact_fuzzy_and_none():
    """三态：完全相等 ⇒ exact；相似 ⇒ fuzzy；不像 ⇒ 不给候选。"""
    exact = kclib.rule_candidates("慢性阻塞性肺疾病", POOL)
    assert exact and exact[0]["kc_id"] == "kc_a"
    assert exact[0]["match_type"] == "exact" and exact[0]["score"] == 1.0

    fuzzy = kclib.rule_candidates("慢性阻塞性肺疾病诊断标准", POOL)
    assert any(c["match_type"] == "fuzzy" for c in fuzzy)

    none = kclib.rule_candidates("肠系膜上动脉综合征", POOL)
    assert none == [], f"不相干的题不该有候选：{none}"


def test_rule_candidates_never_treats_near_synonyms_as_exact():
    """**语义近似不得被当成精确匹配**——「诊断」与「治疗」是两个考点。

    「慢性阻塞性肺疾病的治疗」与「慢性阻塞性肺疾病的诊断」字符上极像，
    规则匹配**可以**把它列为 fuzzy 候选（那正是候选存在的意义），
    但绝不能判为 exact —— exact 会被回填**自动确认**，挂错就等于把掌握度归到错的考点。
    """
    cands = kclib.rule_candidates("慢性阻塞性肺疾病的治疗", POOL)
    for c in cands:
        assert c["match_type"] != "exact", c
    assert not any(c["kc_id"] == "kc_b" and c["match_type"] == "exact" for c in cands)


def test_backfill_auto_confirms_exact_but_not_fuzzy():
    """**exact ⇒ 自动确认；fuzzy ⇒ 必须人工确认**（实施方案 §0 D4）。

    这是整个映射机制的安全阀：自动确认意味着「用户没看过、系统就认定挂对了」，
    所以它只允许给**归一后完全相等**的那一种；相似的一律留给人点。
    """
    from medkit.core import bank as banklib
    from medkit.core import syllabus as syl

    kclib.ensure_ready()
    syl.ensure_seed(force=True)
    pool = kclib.kc_pool()
    assert pool, "知识点池为空，测不了"

    # 造两道题：一题 topic 与某考点**完全相等**，一题只**相似**
    exact_item = str(pool[0]["item"])
    fuzzy_item = str(pool[0]["item"]) + "的鉴别要点"
    banklib.create_bank("映射测试", "内科学")
    banks = banklib.list_banks()
    bid = str(banks[0]["id"])
    # 题记录里的考点字段名是 `subtopic`（`topic` 是冗余查询列）
    banklib.add_items(bid, [
        {"question": "完全匹配题", "subtopic": exact_item,
         "options": ["A. 甲", "B. 乙"], "answer": "A"},
        {"question": "相似匹配题", "subtopic": fuzzy_item,
         "options": ["A. 甲", "B. 乙"], "answer": "A"},
    ])
    items = banklib.list_items(bid)
    exact_id = items[0]["id"]
    fuzzy_id = items[1]["id"]

    kclib.backfill(bank_id=bid, method="rule", min_score=0.45, dry_run=False)

    exact_maps = kclib.mappings_for_item(exact_id, only_confirmed=True)
    assert exact_maps, "完全相等的应当被自动确认"
    assert exact_maps[0]["confidence"] == 1.0

    fuzzy_maps = kclib.mappings_for_item(fuzzy_id)
    fuzzy_confirmed = [m for m in fuzzy_maps if m["confirmed"]]
    assert fuzzy_confirmed == [], f"模糊匹配不得自动确认：{fuzzy_confirmed}"
    if fuzzy_maps:
        assert all(m["source"] == "rule" for m in fuzzy_maps)


def test_rule_candidates_sorted_by_score():
    cands = kclib.rule_candidates("急性心肌梗死", POOL)
    scores = [c["score"] for c in cands]
    assert scores == sorted(scores, reverse=True)


def test_rule_candidates_empty_topic_returns_empty():
    assert kclib.rule_candidates("", POOL) == []
    assert kclib.rule_candidates("   ", POOL) == []


# ================================================================ BKT
def test_bkt_direction_correct_raises_mastery():
    """答对 ⇒ 掌握概率上升；答错 ⇒ 下降（**方向判据**）。"""
    p = kclib.PRIOR_P_MASTERY
    up = kclib.bkt_update(p, correct=True)
    down = kclib.bkt_update(p, correct=False)
    assert up > p, f"答对应上升：{p} → {up}"
    assert down < p, f"答错应下降：{p} → {down}"


def test_bkt_golden_values():
    """黄金值（p=0.30, transit=.25, slip=.10, guess=.20）。"""
    assert kclib.bkt_update(0.30, True) == pytest.approx(0.7834, abs=1e-3)
    assert kclib.bkt_update(0.30, False) == pytest.approx(0.0914, abs=1e-3)


def test_bkt_is_bounded():
    for p in (0.0, 0.5, 1.0):
        for ok in (True, False):
            v = kclib.bkt_update(p, ok)
            assert 0.0 <= v <= 1.0


def test_shrink_cold_start_returns_prior():
    """n=0 ⇒ 返回先验（冷启动不返回 0，也不返回 1）。"""
    assert kclib.shrink(0.9, 0, kclib.PRIOR_P_MASTERY) == pytest.approx(
        kclib.PRIOR_P_MASTERY)


def test_shrink_converges_to_posterior_as_n_grows():
    """先验必须**自动失效**：n 越大，越靠近观测后验。"""
    prior = kclib.PRIOR_P_MASTERY
    a = kclib.shrink(0.9, 1, prior)
    b = kclib.shrink(0.9, 50, prior)
    assert abs(b - 0.9) < abs(a - 0.9), (a, b)


def test_shrink_is_sensitive_to_prior():
    """先验确实是有效参数（不是写死的常数）。"""
    assert kclib.shrink(0.5, 1, 0.1) < kclib.shrink(0.5, 1, 0.9)


def test_apply_attempt_is_pure():
    """纯函数：不得改入参（否则「回放历史」类测试会互相污染）。"""
    st = {"kc_id": "kc_a", "p_mastery": 0.3, "attempts": 2, "correct": 1}
    snapshot = dict(st)
    kclib.apply_attempt(st, correct=True)
    assert st == snapshot


def test_apply_attempt_monotonic_up_and_down():
    """连答对 ⇒ 单调不降；连答错 ⇒ 单调不升。"""
    st: dict = {}
    ups = []
    for _ in range(5):
        st = kclib.apply_attempt(st, correct=True)
        ups.append(st["p_mastery"])
    assert ups == sorted(ups), ups

    st2: dict = {}
    downs = []
    for _ in range(5):
        st2 = kclib.apply_attempt(st2, correct=False)
        downs.append(st2["p_mastery"])
    assert downs == sorted(downs, reverse=True), downs


def test_apply_attempt_counts_and_confidence():
    st = kclib.apply_attempt(None, correct=True)
    assert st["attempts"] == 1 and st["correct"] == 1
    assert st["p_mastery"] > kclib.PRIOR_P_MASTERY
    st2 = kclib.apply_attempt(st, correct=False)
    assert st2["attempts"] == 2 and st2["correct"] == 1


# ================================================================ 校准与题目标定
def test_calibration_curve_brier_and_buckets():
    pairs = [(1, False)] * 4 + [(5, True)] * 4 + [(3, True)] * 2
    res = kclib.calibration_curve(pairs)
    assert res["n"] == 10
    assert res["insufficient"] is False
    assert len(res["buckets"]) == 5
    b1 = [b for b in res["buckets"] if b["confidence"] == 1][0]
    b5 = [b for b in res["buckets"] if b["confidence"] == 5][0]
    assert b1["accuracy"] == 0.0 and b5["accuracy"] == 1.0
    assert 0.0 <= res["brier"] <= 1.0


def test_calibration_marks_insufficient_samples():
    """样本不足 ⇒ insufficient=True（前端据此显示「样本不足」而不是数字）。"""
    res = kclib.calibration_curve([(3, True)] * 3)
    assert res["insufficient"] is True
    assert res["n"] == 3


def test_item_stats_marks_bad_items():
    """**坏题识别**：p < 0.2（几乎没人对）或区分度为负 ⇒ 标记 bad。"""
    attempts = [{"item_id": "i1", "correct": False}] * 8 + \
               [{"item_id": "i2", "correct": True}] * 8
    items = [{"id": "i1", "topic": "难题"}, {"id": "i2", "topic": "易题"}]
    stats = kclib.item_stats(attempts, items)
    assert stats["i1"]["p"] == 0.0 and stats["i1"]["bad"] is True
    assert stats["i2"]["p"] == 1.0 and stats["i2"]["bad"] is False
    assert stats["i1"]["p_shrunk"] > 0.0, "小样本收缩后不该还是 0"


# ================================================================ 存储（SQLite）
def test_kc_state_columns_match_data():
    """**双写不变量**：冗余列的值必须与 `data` JSON 逐值一致。

    `kc_state` 走仓库既有的「data JSON 为权威 + 冗余查询列」模型，
    两边不一致 ⇒ 查询结果与真身对不上，且**不会报错**——只能靠守卫钉住。
    """
    from medkit.core import db as dbs

    kclib.ensure_ready()
    kclib.upsert_state({
        "kc_id": "kc_x", "subject": "内科学", "chapter": "呼吸系统",
        "item": "慢性阻塞性肺疾病", "p_mastery": 0.42,
        "scaffold_level": 1, "attempts": 3, "correct": 1,
    })
    with dbs.tx(write=False) as cur:
        row = cur.execute(
            "SELECT data, subject, chapter, item, p_mastery, scaffold_level,"
            " attempts, correct FROM kc_state WHERE id = ?", ("kc_x",)).fetchone()
    assert row is not None
    import json
    data = json.loads(row[0])
    assert row[1] == data["subject"]
    assert row[2] == data["chapter"]
    assert row[3] == data["item"]
    assert row[4] == data["p_mastery"]
    assert row[5] == data["scaffold_level"]
    assert row[6] == data["attempts"]
    assert row[7] == data["correct"]


def test_set_mappings_is_overwrite_semantics():
    """覆盖语义：本次列表即最终确认；空数组 = 取消全部。"""
    kclib.set_mappings("i_a", ["kc_a", "kc_b"], source="human")
    assert {x["kc_id"] for x in kclib.mappings_for_item("i_a", only_confirmed=True)} == \
        {"kc_a", "kc_b"}
    kclib.set_mappings("i_a", ["kc_c"], source="human")
    assert {x["kc_id"] for x in kclib.mappings_for_item("i_a", only_confirmed=True)} == \
        {"kc_c"}
    kclib.set_mappings("i_a", [], source="human")
    assert kclib.mappings_for_item("i_a", only_confirmed=True) == []


def test_set_mappings_requires_item_id():
    with pytest.raises(ValueError):
        kclib.set_mappings("", ["kc_a"])


# ================================================================ 接口层
def test_api_kc_items_validation():
    """非法参数必须 400（不是 500）。"""
    c = _client()
    assert c.get("/api/kc/items?limit=0").status_code == 400
    assert c.get("/api/kc/items?limit=99999").status_code == 400
    assert c.get("/api/kc/items?offset=-1").status_code == 400
    assert c.get("/api/kc/items?status=bogus").status_code == 400


def test_api_kc_map_requires_existing_item():
    c = _client()
    r = c.post("/api/kc/items/nonexistent-item/map", json={"kc_ids": ["kc_a"]})
    assert r.status_code == 404
    r2 = c.post("/api/kc/items/whatever/map", json={"kc_ids": "not-a-list"})
    assert r2.status_code in (400, 404)


def test_api_kc_map_rejects_bad_kc_ids():
    c = _client()
    r = c.post("/api/kc/items/x/map", json={"kc_ids": [1, 2]})
    assert r.status_code in (400, 404)


def test_api_kc_state_and_overview_shape():
    """响应字段名必须与前端约定一致（snake_case，零重命名）。"""
    c = _client()
    body = c.get("/api/kc/state").json()
    assert body["ok"] is True
    assert "items" in body and "total" in body
    ov = c.get("/api/kc/overview").json()
    assert ov["ok"] is True
    for k in ("total_kcs", "mapped_kcs", "by_state", "due_today", "coverage_ratio"):
        assert k in ov, k


def test_api_kc_calibration_shape():
    c = _client()
    body = c.get("/api/kc/calibration").json()
    assert body["ok"] is True
    for k in ("n", "brier", "buckets", "curve", "insufficient"):
        assert k in body, k


def test_api_kc_backfill_validation_and_dry_run():
    """method 非法 ⇒ 400；dry_run 默认真 ⇒ 不落库。"""
    c = _client()
    assert c.post("/api/kc/backfill", json={"method": "bogus"}).status_code == 400
    assert c.post("/api/kc/backfill", json={"min_score": 5}).status_code == 400
    body = c.post("/api/kc/backfill", json={"dry_run": True}).json()
    assert body["ok"] is True and body["dry_run"] is True
    assert "scanned" in body and "matched" in body


def test_api_kc_backfill_llm_degrades_to_rule():
    """LLM 通道未实现 ⇒ **降级为 rule 且如实说明**，不报错（实施方案 §0 D6）。"""
    c = _client()
    body = c.post("/api/kc/backfill", json={"method": "llm", "dry_run": True}).json()
    assert body["ok"] is True
    assert body["method"] == "rule"
    assert any("降级" in n for n in body["notes"]), body["notes"]


def test_api_kc_edges_shape():
    c = _client()
    body = c.get("/api/kc/edges").json()
    assert body["ok"] is True and isinstance(body["items"], list)
    assert c.get("/api/kc/edges?kind=bogus").status_code == 400


def test_api_kc_disabled_by_flag():
    """`features.kc = false` ⇒ 整组 404（服务端下线，前端入口同批隐藏）。"""
    from medkit.core import config as cfg

    saved = cfg.DEFAULTS.get("features")
    try:
        cfg.DEFAULTS["features"] = {"kc": False}
        c = _client()
        assert c.get("/api/kc/state").status_code == 404
        assert c.get("/api/kc/overview").status_code == 404
    finally:
        cfg.DEFAULTS["features"] = saved or {}


def test_bank_item_topic_query_column_is_alive():
    """**`bank_items.topic` 冗余查询列必须有值**（否则按考点的索引白建）。

    题记录里的字段名是 `subtopic`，查询列却叫 `topic`——不补齐的话该列恒为 NULL，
    `idx_bi_topic` 索引不到任何东西，任何 `WHERE topic = ?` 都查不到。
    这条守卫放在本文件是因为**映射路径依赖它**（`core/kc.py` 读 `topic or subtopic`）。
    """
    from medkit.core import bank as banklib
    from medkit.core import db as dbs

    kclib.ensure_ready()
    banklib.create_bank("查询列测试", "内科学")
    bid = str(banklib.list_banks()[0]["id"])
    banklib.add_items(bid, [{"question": "考点列测试题", "subtopic": "慢阻肺的诊断",
                             "options": ["A. 甲"], "answer": "A"}])
    item = banklib.list_items(bid)[0]
    assert item["topic"] == "慢阻肺的诊断", item.get("topic")
    assert item["subtopic"] == "慢阻肺的诊断"
    with dbs.tx(write=False) as cur:
        row = cur.execute("SELECT topic FROM bank_items WHERE id = ?",
                          (item["id"],)).fetchone()
    assert row is not None and row[0] == "慢阻肺的诊断", "查询列必须是可查的"
