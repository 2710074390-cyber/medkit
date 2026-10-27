"""学习者模型接线守卫（v0.22 阶段 3）——断点 A 的根治验收。

## 这一组在守什么

审计断点 A 是：**作答流水没进学习者模型**——`bank_attempts` 是全量流水，
但掌握度只由**错题**聚合，答对 50 题掌握度纹丝不动（单向棘轮）。

阶段 3 的判据全部是**可观测行为**，不是「源码里有没有调某个函数」：

1. 答对 ⇒ `kc_state.p_mastery` **上升**；答错 ⇒ **下降**（方向断言，不能只断言公式）；
2. 「流水 + 状态」**同一事务**：状态写入失败时，流水必须**一并回滚**（不留「有流水无状态」）；
3. `kc_state` 是**读模型**：从 `bank_attempts ⋈ item_kc` 重放重建，必须与增量维护**逐值相等**；
4. 未映射的题**照样能判分**，只是 `mapped=false`（不能因为没回填就不让人做题）；
5. 置信度：写进流水；越界由接口 **400** 拒绝，核心层一律归 0（可选字段不得变成必填）；
6. 薄弱项表**优先** `kc_state`，对不上的老知识点**回退** legacy（不许凭空消失）；
7. 错题重练带 `source_ref.item_id` 时，状态归到**原题**的知识点（否则「练了不算」）。

## 为什么「原子性」要专门注入一次失败

同事务是**看不见**的性质：如果只在成功路径上断言「两者都有」，两次独立写入
也能通过。唯一能区分「同事务」与「两次写」的办法，是**让第二次写失败**，
再断言第一次也没留下——所以这里 monkeypatch `apply_attempt_in` 抛错。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit import main as m  # noqa: E402
from medkit.core import bank as banklib  # noqa: E402
from medkit.core import db as dbs  # noqa: E402
from medkit.core import kc as kclib  # noqa: E402
from medkit.core import library as lib  # noqa: E402
from medkit.core import syllabus as syl  # noqa: E402


def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


# ---------------------------------------------------------------- 夹具
@pytest.fixture()
def bank_with_kc():
    """建一个题库 + 两道题，并把第一道题**精确映射**到一个真实考点。

    返回 `(bank_id, mapped_item, unmapped_item, kc_id)`。
    映射走真实链路（`backfill` → exact ⇒ 自动确认），不手工插 `item_kc`——
    手工插入会绕开「只让 exact 自动确认」这条安全阀，测出来的东西不能代表线上。
    """
    kclib.ensure_ready()
    syl.ensure_seed(force=True)
    pool = kclib.kc_pool()
    assert pool, "知识点池为空，测不了"
    target = pool[0]

    banklib.create_bank("状态测试题库", str(target.get("subject") or "内科学"))
    bid = str(banklib.list_banks()[0]["id"])
    banklib.add_items(bid, [
        {"question": "已映射题", "subtopic": str(target["item"]),
         "options": ["A. 甲", "B. 乙"], "answer": "A"},
        {"question": "未映射题", "subtopic": "肠系膜上动脉综合征（题库里不存在的考点）",
         "options": ["A. 甲", "B. 乙"], "answer": "A"},
    ])
    items = banklib.list_items(bid)
    mapped = next(i for i in items if i["question"] == "已映射题")
    unmapped = next(i for i in items if i["question"] == "未映射题")
    kclib.backfill(bank_id=bid, method="rule", dry_run=False)
    assert kclib.mappings_for_item(mapped["id"], only_confirmed=True), \
        "前置条件不成立：精确匹配没被自动确认"
    return bid, mapped, unmapped, str(target["kc_id"])


def _p(kc_id: str) -> float:
    """当前掌握概率；**没有状态行时返回冷启动先验**（不是 0）。

    返回 0 会把「从未作答」说成「完全不会」——于是第一次答错会显得「从 0 涨到 0.23」，
    方向断言就永远测不出真回归（这是本文件的第一个坑，已踩过）。
    """
    row = kclib.get_state(kc_id) or {}
    if not row:
        return float(kclib.PRIOR_P_MASTERY)
    return float(row.get("p_mastery") or 0.0)


# ---------------------------------------------------------------- 方向：答对也要进模型
def test_grade_updates_kc_state_on_correct_answer(bank_with_kc):
    """**答对必须让掌握度上升**——这正是断点 A 的病灶（原来答对不入模）。

    这条用例在旧实现下**必红**：旧 `grade` 只调 `record_attempt`，
    `kc_state` 根本不写，`p_mastery` 恒为 0（本条会因 `before == after` 失败）。
    """
    bid, mapped, _unmapped, kc_id = bank_with_kc
    before = _p(kc_id)
    res = banklib.grade(bid, mapped["id"], "A", confidence=4)
    after = _p(kc_id)

    assert res["correct"] is True
    assert res["kc"]["mapped"] is True
    assert [k["kc_id"] for k in res["kc"]["kcs"]] == [kc_id]
    assert after > before, f"答对后掌握度没上升：{before} → {after}"
    assert res["kc"]["kcs"][0]["delta"] > 0
    row = kclib.get_state(kc_id) or {}
    assert int(row.get("attempts")) == 1
    assert int(row.get("correct")) == 1


def test_grade_updates_kc_state_on_wrong_answer(bank_with_kc):
    """答错必须让掌握度**下降**（方向断言——阶段 2 曾把 BKT 次序写反而不降反升）。"""
    bid, mapped, _unmapped, kc_id = bank_with_kc
    before = _p(kc_id)
    res = banklib.grade(bid, mapped["id"], "B")       # 正确答案是 A

    assert res["correct"] is False
    assert _p(kc_id) < before, f"答错后掌握度没下降：{before} → {_p(kc_id)}"
    row = kclib.get_state(kc_id) or {}
    assert int(row.get("attempts")) == 1
    assert int(row.get("correct")) == 0


def test_repeated_correct_answers_are_monotonic(bank_with_kc):
    """连续答对 ⇒ 掌握度**单调不减**（收缩会让它一开始涨得慢，但不能回落）。"""
    bid, mapped, _unmapped, kc_id = bank_with_kc
    seen = []
    for _ in range(5):
        banklib.grade(bid, mapped["id"], "A")
        seen.append(_p(kc_id))
    assert seen == sorted(seen), f"掌握度出现回落：{seen}"
    assert seen[-1] > seen[0], "五次全对却没有增长"


def test_unmapped_item_still_grades(bank_with_kc):
    """**未映射的题照样能判分**，只是 `mapped=false`。

    这条防的是「把可选功能做成必填」：没做过知识图谱回填的用户，
    做题不该被拦住，也不该被判成错误。
    """
    bid, _mapped, unmapped, _kc_id = bank_with_kc
    res = banklib.grade(bid, unmapped["id"], "A")
    assert res["correct"] is True
    assert res["kc"]["mapped"] is False
    assert res["kc"]["kcs"] == []
    # 流水照写（每日目标要用）
    assert any(a["item_id"] == unmapped["id"] for a in banklib.list_attempts(bank_id=bid))


# ---------------------------------------------------------------- 原子性
def test_grade_and_attempt_are_atomic(bank_with_kc, monkeypatch):
    """**注入状态写入失败 ⇒ 流水必须一并回滚**（同事务的可证伪判据）。

    只在成功路径断言「两者都有」是**测不出**同事务的：两次独立写入也能通过。
    唯一的判别方法是让第二次写失败，再看第一次有没有留下残骸。
    """
    bid, mapped, _unmapped, _kc_id = bank_with_kc
    before = len(banklib.list_attempts(bank_id=bid))

    def _boom(*_a, **_k):
        raise RuntimeError("注入：状态写入失败")

    monkeypatch.setattr(kclib, "apply_attempt_in", _boom)
    with pytest.raises(RuntimeError, match="注入"):
        banklib.grade(bid, mapped["id"], "A")

    after = len(banklib.list_attempts(bank_id=bid))
    assert after == before, f"状态失败后流水仍被写入（不是同事务）：{before} → {after}"


# ---------------------------------------------------------------- 读模型可重建
def test_kc_state_matches_replay_from_attempts(bank_with_kc):
    """`kc_state` 是**读模型**：重放 `bank_attempts ⋈ item_kc` 必须与增量维护**逐值相等**。

    这是「不建 attempt_kc 表、只保留一个真相」这条设计的可执行证明。
    若两者不等，说明有一处写入绕过了 `apply_attempt`（第二个真相出现了）。
    """
    bid, mapped, unmapped, kc_id = bank_with_kc
    for ans in ("A", "B", "A", "A", "B"):
        banklib.grade(bid, mapped["id"], ans)
    banklib.grade(bid, unmapped["id"], "A")          # 未映射，不影响任何状态

    inc = kclib.get_state(kc_id) or {}
    out = kclib.rebuild_state()
    replayed = kclib.get_state(kc_id) or {}

    assert out["applied"] == 5, f"重放条数不对：{out}"
    for field in ("p_mastery", "attempts", "correct"):
        assert float(replayed.get(field) or 0) == float(inc.get(field) or 0), \
            f"{field} 增量与重放不一致：{inc.get(field)} vs {replayed.get(field)}"


# ---------------------------------------------------------------- 置信度
def test_confidence_is_recorded_in_attempt(bank_with_kc):
    """自评置信度写进流水（校准曲线的数据源）。"""
    bid, mapped, _unmapped, _kc_id = bank_with_kc
    banklib.grade(bid, mapped["id"], "A", confidence=4)
    row = [a for a in banklib.list_attempts(bank_id=bid) if a["item_id"] == mapped["id"]][-1]
    assert int(row.get("confidence") or 0) == 4


def test_confidence_out_of_range_is_treated_as_not_collected(bank_with_kc):
    """越界/非法值在**核心层**一律归 0（可选字段不得让人做不成题）。

    接口层是**另一条**判据：显式填了非法值要 400（见下一个用例）。
    两层不同不是矛盾——核心层的调用方（脚本/内部）不该被前端校验绑架。
    """
    bid, mapped, _unmapped, _kc_id = bank_with_kc
    for bad in (9, -1, "abc", None):
        banklib.grade(bid, mapped["id"], "A", confidence=bad)   # type: ignore[arg-type]
    rows = [a for a in banklib.list_attempts(bank_id=bid) if a["item_id"] == mapped["id"]]
    assert all(int(r.get("confidence") or 0) == 0 for r in rows), \
        f"越界置信度未被归零：{[r.get('confidence') for r in rows]}"


# ---------------------------------------------------------------- 接口契约
def test_grade_api_returns_kc_and_validates_confidence(bank_with_kc):
    """`POST /api/bank/grade`：带 `kc` 摘要；`confidence` 越界 **400 且不落流水**。"""
    bid, mapped, _unmapped, kc_id = bank_with_kc
    c = _client()

    ok = c.post("/api/bank/grade", json={"bank_id": bid, "item_id": mapped["id"],
                                         "user_answer": "A", "confidence": 5})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["kc"]["mapped"] is True
    assert body["kc"]["kcs"][0]["kc_id"] == kc_id
    assert "attempt_id" in body and body["attempt_id"]

    before = len(banklib.list_attempts(bank_id=bid))
    bad = c.post("/api/bank/grade", json={"bank_id": bid, "item_id": mapped["id"],
                                          "user_answer": "A", "confidence": 9})
    assert bad.status_code == 400, bad.text
    assert len(banklib.list_attempts(bank_id=bid)) == before, "400 不该留下流水"


def test_grade_api_allows_omitting_confidence(bank_with_kc):
    """不传 `confidence`（跳过自评）必须正常判分——这是默认路径。"""
    bid, mapped, _unmapped, _kc_id = bank_with_kc
    r = _client().post("/api/bank/grade",
                       json={"bank_id": bid, "item_id": mapped["id"], "user_answer": "A"})
    assert r.status_code == 200, r.text
    assert r.json()["correct"] is True


# ---------------------------------------------------------------- 掌握度口径
def test_mastery_view_prefers_kc_then_falls_back():
    """薄弱项表：**对得上的用 kc 的 p_mastery，对不上的保留 legacy**。

    整表替换会让「没映射过的老知识点」从界面消失——那不是修好口径，是丢数据。
    """
    kclib.ensure_ready()
    syl.ensure_seed(force=True)
    pool = kclib.kc_pool()
    target = pool[0]

    # legacy 侧两条：一条与考点同名，一条是「只在错题本里存在」的孤立考点
    lib._save(lib.KNOWLEDGE_FILE, [
        {"name": str(target["item"]), "subject": str(target.get("subject") or ""),
         "score": 0.05, "miss": 3, "last_tried": "2026-10-01T00:00:00"},
        {"name": "只在错题本里的孤立考点", "subject": "内科学",
         "score": 0.30, "miss": 1, "last_tried": "2026-10-02T00:00:00"},
    ])
    # kc 侧：同一考点两次答对（走 `upsert_state` 这条真实写入口）
    for _ in range(2):
        st = kclib.apply_attempt(kclib.get_state(str(target["kc_id"])), True)
        st.update({"kc_id": str(target["kc_id"]), "item": str(target["item"]),
                   "subject": str(target.get("subject") or ""),
                   "chapter": str(target.get("chapter") or "")})
        kclib.upsert_state(st)

    view = lib.get_mastery_view()
    rows = {r["name"]: r for r in view["knowledge"]}
    hit = rows[str(target["item"])]
    assert hit["score_source"] == "kc", hit
    assert hit["score"] == pytest.approx(_p(str(target["kc_id"])), abs=1e-3)
    assert hit["score"] > 0.05, "kc 的分数没有覆盖 legacy 的 0.05"
    assert "只在错题本里的孤立考点" in rows, "legacy 独有知识点被覆盖没了"
    assert rows["只在错题本里的孤立考点"]["score_source"] == "legacy"
    assert view["stats"]["score_sources"]["kc"] == 1
    assert view["stats"]["score_sources"]["legacy"] == 1


def test_readiness_is_false_without_attempts():
    """没有作答流水时 `ready=False`——**不假装有结论**（不许显示 0% 冒充「准备度」）。"""
    from medkit.core import dashboard

    kclib.ensure_ready()
    out = dashboard.summary()
    assert out["readiness"]["ready"] is False
    assert out["readiness"]["kcs"] == 0


def test_readiness_aggregates_after_practice(bank_with_kc):
    """练过之后准备度给出可核的三件事：练过几个、平均掌握、最薄弱的几个。"""
    from medkit.core import dashboard

    bid, mapped, _unmapped, kc_id = bank_with_kc
    banklib.grade(bid, mapped["id"], "A")
    out = dashboard.summary()["readiness"]
    assert out["ready"] is True
    assert out["kcs"] == 1
    assert out["avg_mastery"] == pytest.approx(_p(kc_id), abs=1e-3)
    assert out["weakest"][0]["kc_id"] == kc_id


# ---------------------------------------------------------------- 错题重练接线
def test_retry_feeds_kc_state_via_source_ref(bank_with_kc):
    """错题重练带 `source_ref.item_id` ⇒ 状态归到**原题**的知识点。

    否则「重练」永远只改错题本的 status，掌握度不动 —— 用户练一整晚，薄弱项表不变。
    """
    bid, mapped, _unmapped, kc_id = bank_with_kc
    banklib.grade(bid, mapped["id"], "B")            # 先判错，回流成错题
    def _ref_item(x):
        ref = x.get("source_ref") or {}
        return str(ref.get("item_id") or ref.get("question_id") or "")

    mistake = next((x for x in lib.list_mistakes() if _ref_item(x) == mapped["id"]), None)
    assert mistake is not None, "判错没有回流成错题，测不了重练"
    # 两个溯源键都要在：`question_id` 是标准键，`item_id` 是题库回流补的（阶段 3 用）
    assert (mistake.get("source_ref") or {}).get("item_id") == mapped["id"], \
        f"题库的 source_ref 被覆盖了：{mistake.get('source_ref')}"
    assert (mistake.get("source_ref") or {}).get("question_id") == mapped["id"]

    before = _p(kc_id)
    r = _client().post(f"/api/library/mistakes/{mistake['id']}/retry",
                       json={"user_answer": "A", "confidence": 3})
    assert r.status_code == 200, r.text
    assert r.json()["kc"]["mapped"] is True, r.json()
    assert _p(kc_id) > before, "重练判对没有推动掌握度"


def test_retry_rejects_bad_confidence(bank_with_kc):
    """重练接口同样校验置信度（两个入口的契约必须一致，否则前端要写两套）。"""
    bid, mapped, _unmapped, _kc_id = bank_with_kc
    banklib.grade(bid, mapped["id"], "B")
    mistake = next(x for x in lib.list_mistakes()
                   if mapped["id"] in {str((x.get("source_ref") or {}).get(k) or "")
                                       for k in ("item_id", "question_id")})
    r = _client().post(f"/api/library/mistakes/{mistake['id']}/retry",
                       json={"user_answer": "A", "confidence": 7})
    assert r.status_code == 400, r.text


# ---------------------------------------------------------------- 不允许第二个真相
def test_no_attempt_kc_table_exists():
    """**不得出现 `attempt_kc` 表**（设计方案 D5：避免第二个真相）。

    `bank_attempts ⋈ item_kc` 已经能重建全部状态；再存一份「作答→知识点」的
    冗余关系，就意味着两处可能不一致，且没有任何守卫能说清哪边对。
    """
    kclib.ensure_ready()
    with dbs.tx(write=False) as cur:
        names = {r[0] for r in cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "attempt_kc" not in names, "出现了第二个真相表"
    assert {"item_kc", "kc_state", "bank_attempts"} <= names
