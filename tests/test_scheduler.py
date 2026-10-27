"""复习调度器守卫（v0.22.0 · ADR-004 重新接线）。

## 为什么有这一组

2026-10-27 审计发现：ADR-004 早就决策了「FSRS 默认 / SM-2 legacy 可切」，
但实现模块随 v0.15 精简一起被删，**FSRS 从未接到线上路径**；
`core/bank.py` 至今把排期委托给已不存在的 `review` / `cards` 域（悬空引用）。

接线之后，「排期到底怎么算的」就成了**用户可见行为**（复习节奏变了、due 变了）。
本组用例把三件事钉死：

1. **SM-2 legacy 路径逐值不变** —— 切回 legacy 必须和 2026-10-27 之前一模一样；
2. **FSRS 路径确定** —— 同一输入必须同一输出（否则守卫钉不住，行为不可证伪）；
3. **vault 侧语义跨算法一致** —— `reps` / `lapses` / `state` / `status` 的含义
   不因换算法而变（「连续记住两次即已掌握」这条闭环对用户是承诺）。

## 每条用例的判据来源

- SM-2 黄金值：直接搬 `core/scheduler.py` 里的常量与公式（与 2026-10-27 之前的
  `core/vault_index.py` **逐字一致**），不是重新推导的。
- FSRS 阶梯：2026-10-27 **实测标定**（学习步长 10 分钟/1 天、retention 0.9、
  上限 180 天、关 fuzz），对照 SM-2 的 1→6→15→38→95→238 天。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from medkit import main as m  # noqa: E402
from medkit.core import scheduler as sched  # noqa: E402
from medkit.core import vault, vault_index  # noqa: E402

NOW = datetime(2026, 10, 27, 10, 0, 0)


@pytest.fixture()
def vroot(tmp_path, monkeypatch):
    """隔离 vault：指向 tmp 下的目录（与 tests/test_vault.py 的同名 fixture 同构）。

    `vroot` 原本只定义在 test_vault.py 里（不在 conftest），本文件用不到它的
    其它能力，故就地复刻一份，避免为了一次隔离去动全局 conftest。
    """
    root = tmp_path / "vaultroot"
    vault.init_vault(str(root))
    monkeypatch.setattr(vault, "CONFIG_CACHE", None, raising=False)
    vault.set_vault_dir(str(root))
    yield root
    vault.set_vault_dir("")


def _legacy_sm2_card(st: dict, quality: int, now: datetime) -> dict:
    """2026-10-27 之前的 SM-2 实现（**逐字复制**，不调用新代码）。

    刻意不引用 `core/scheduler.py` 的常量与函数——否则两边一起错，守卫就恒绿了。
    字面量即当时的真值：ease 初始 2.5、下限 1.3、quality≥3 算记得、
    第一次 1 天、第二次 6 天、之后 interval×ease。
    """
    q = max(0, min(int(quality), 5))
    passed = q >= 3
    ease = float(st.get("ease") or 2.5)
    if passed:
        ease = ease + (0.1 - (5 - max(0, min(q, 5))) * (0.08 + (5 - q) * 0.02))
    else:
        ease = ease - 0.2
    ease = max(1.3, round(ease, 2))
    reps = int(st.get("reps") or 0) + 1
    interval = int(st.get("interval") or 0)
    if reps == 1:
        interval = 1
    elif reps == 2:
        interval = 6
    else:
        interval = max(int(round(int(st.get("interval") or 0) * float(st.get("ease") or 2.5))),
                       int(st.get("interval") or 0) + 1)
    lapses = int(st.get("lapses") or 0)
    if passed:
        state = "review" if reps >= 2 else "learning"
    else:
        interval, reps = 0, 0
        state = "relearning"
        lapses += 1
    due = (now.date() + timedelta(days=interval)).isoformat()
    return {"ease": ease, "interval": interval, "reps": reps,
            "lapses": lapses, "state": state, "due": due}


# ================================================================ SM-2 legacy：逐值一致
@pytest.mark.parametrize("ease,reps,interval", [
    (2.5, 0, 0), (2.5, 1, 1), (2.5, 2, 6), (1.3, 0, 0), (2.8, 5, 30),
])
def test_sm2_next_intervals_golden(ease, reps, interval):
    """SM-2 间隔推进的黄金值：**搬过来的常量**，不是重新推导的。"""
    assert sched.sm2_next_intervals(ease, reps, interval) == (
        sched.INTERVAL_FIRST if reps + 1 == 1 else
        sched.INTERVAL_SECOND if reps + 1 == 2 else
        max(int(round(interval * ease)), interval + 1),
        reps + 1,
    )


@pytest.mark.parametrize("ease,quality,passed,expected", [
    (2.5, 5, True, 2.6), (2.5, 3, True, 2.36), (2.5, 1, False, 2.3),
    (1.3, 0, False, 1.3),
])
def test_sm2_update_ease_golden(ease, quality, passed, expected):
    """SM-2 难度更新的黄金值。"""
    assert sched.sm2_update_ease(ease, quality, passed) == expected


def test_sm2_scheduler_matches_legacy_golden():
    """**SM-2 路径逐值不变**——切回 legacy 必须和 2026-10-27 之前一模一样。

    判据：同一串 quality，新旧两套实现在 (due, ease, interval, reps, lapses, state)
    上逐项相等。旧实现是本文件里**手写的** `_legacy_sm2_card`（不引用新代码）。
    """
    s = sched.SM2Scheduler()
    st: dict = {}
    for q in (4, 4, 4, 1, 4, 0, 5):
        old = _legacy_sm2_card(st, q, NOW)
        got = s.schedule(st, q, now=NOW)
        assert got["due"] == old["due"], f"due 不一致（q={q}）：旧 {old['due']} vs 新 {got['due']}"
        assert got["state"] == old["state"], f"state 不一致（q={q}）"
        assert got["reps"] == old["reps"], f"reps 不一致（q={q}）"
        assert got["interval"] == old["interval"], f"interval 不一致（q={q}）"
        assert got["lapses"] == old["lapses"], f"lapses 不一致（q={q}）"
        assert got["ease"] == old["ease"], f"ease 不一致（q={q}）"
        st = {"ease": old["ease"], "reps": old["reps"], "interval": old["interval"],
              "lapses": old["lapses"]}


# ================================================================ FSRS
def test_fsrs_scheduler_is_deterministic():
    """**同输入必须同输出**——fsrs 默认开 fuzzing 会给 due 加随机偏移，
    那样就钉不住行为。本模块显式关掉 fuzzing ⇒ 两次调用完全一致。"""
    s = sched.FSRSScheduler()
    st = {"id": "m1", "reps": 0, "lapses": 0, "last_review": NOW.isoformat()}
    a = s.schedule(dict(st), 4, now=NOW)
    b = s.schedule(dict(st), 4, now=NOW)
    assert a["due"] == b["due"] and a["stability"] == b["stability"]
    assert a["difficulty"] == b["difficulty"] and a["fsrs_step"] == b["fsrs_step"]


def test_fsrs_first_review_is_one_day():
    """新卡「记住」→ 明天到期（对齐 SM-2 的第一次 interval=1）。

    这条是**体感契约**：如果第一次「记住」就跳到 6 天，用户会觉得「白复习了」。
    """
    s = sched.FSRSScheduler()
    out = s.schedule({"id": "m1"}, 4, now=NOW)
    assert out["due"] == (NOW.date() + timedelta(days=1)).isoformat()
    assert out["reps"] == 1


def test_fsrs_failure_comes_back_same_day():
    """「忘了」→ **当天**重来（对齐 SM-2 的 relearning→今天）。"""
    s = sched.FSRSScheduler()
    out = s.schedule({"id": "m1"}, 0, now=NOW)
    assert out["due"] == NOW.date().isoformat()
    assert out["state"] == "relearning"
    assert out["reps"] == 0
    assert out["lapses"] == 1


def test_fsrs_ladder_matches_sm2_shape():
    """连续「记住」的间隔阶梯应与 SM-2 **同形**（1 → 7 → 32 → …，封顶 180）。

    判据不是逐值相等（FSRS 与 SM-2 是两套数学），而是：
    ① 第一步都是 1 天；② 之后单调递增；③ 都不超过上限。
    """
    s = sched.FSRSScheduler()
    st: dict = {"id": "m2"}
    gaps: list[int] = []
    when = NOW
    for _ in range(6):
        out = s.schedule(st, 4, now=when)
        d = datetime.fromisoformat(out["due"]).date()
        gaps.append((d - when.date()).days)
        st = dict(out)
        st["last_review"] = when.isoformat()
        when = datetime.combine(d, datetime.min.time())
    assert gaps[0] == 1, f"第一步应 1 天，实得 {gaps}"
    assert gaps == sorted(gaps), f"间隔应单调不减，实得 {gaps}"
    assert all(g <= 180 for g in gaps), f"不得超上限 180 天，实得 {gaps}"


def test_fsrs_max_interval_is_respected():
    """上限必须真的生效（否则会排到几年后——考完试都轮不到）。"""
    s = sched.FSRSScheduler(max_interval=30)
    st: dict = {"id": "m3"}
    when = NOW
    for _ in range(10):
        out = s.schedule(st, 4, now=when)
        d = datetime.fromisoformat(out["due"]).date()
        assert (d - when.date()).days <= 30
        st = dict(out)
        st["last_review"] = when.isoformat()
        when = datetime.combine(d, datetime.min.time())


def test_fsrs_seeds_from_legacy_sm2_history():
    """历史 SM-2 卡切到 FSRS 时**要播种**，不能当全新卡。

    播种依据：`reps`/`lapses` > 0 ⇒ 这张卡复习过 ⇒ `interval` 近似 stability、
    `ease` 近似 difficulty。ease 越低 ⇒ 难度越高（单调）。
    """
    s = sched.FSRSScheduler()
    # 播种本身（纯函数，可直接断言）
    seed = sched.FSRSScheduler._seed_from_legacy(
        {"ease": 1.3, "interval": 9, "reps": 3, "lapses": 1})
    assert seed["stability"] == pytest.approx(9.0)
    assert seed["difficulty"] > 5.0, "ease 1.3（最难）⇒ difficulty 应偏高"
    assert seed["fsrs_state"] == "review"
    # 没复习过的卡不播种（当全新卡）
    assert sched.FSRSScheduler._seed_from_legacy({"ease": 2.5}) == {}
    # 端到端：播种过的卡与全新卡的稳定性演化不同（说明播种真的生效了）
    seeded = s.schedule({"id": "m4", "ease": 1.3, "interval": 9, "reps": 3,
                         "lapses": 1, "last_tried": NOW.isoformat()}, 4, now=NOW)
    fresh = s.schedule({"id": "m5"}, 4, now=NOW)
    assert seeded["stability"] != fresh["stability"]
    assert seeded["difficulty"] > fresh["difficulty"]


def test_fsrs_difficulty_is_monotonic_in_ease():
    """ease 越低 ⇒ FSRS difficulty 越高（播种映射必须单调）。"""
    s = sched.FSRSScheduler()
    prev = None
    for ease in (1.3, 1.8, 2.2, 2.5, 2.8):
        out = s.schedule({"id": "mx", "ease": ease, "interval": 5, "reps": 2}, 4, now=NOW)
        d = out["difficulty"]
        if prev is not None:
            assert d <= prev, f"ease 变大时 difficulty 不应变大：{ease} → {d} vs {prev}"
        prev = d


def test_fsrs_preserves_sm2_fields_for_rollback():
    """切到 FSRS 后，`ease` / `interval` 必须**原样保留**——切回 SM-2 才不会丢历史。"""
    s = sched.FSRSScheduler()
    st = {"id": "m6", "ease": 2.4, "interval": 12, "reps": 3, "lapses": 0}
    out = s.schedule(st, 4, now=NOW)
    assert out["ease"] == 2.4
    assert out["interval"] == 12


def test_fsrs_preserves_fsrs_fields_when_sm2_used():
    """反向：切回 SM-2 时，FSRS 字段也必须原样保留。"""
    s = sched.SM2Scheduler()
    st = {"id": "m7", "stability": 3.2, "difficulty": 4.5, "fsrs_state": "review",
          "fsrs_step": 0, "last_review": NOW.isoformat(), "ease": 2.5}
    out = s.schedule(st, 4, now=NOW)
    assert out["stability"] == 3.2 and out["difficulty"] == 4.5
    assert out["fsrs_state"] == "review" and out["last_review"] == NOW.isoformat()


# ================================================================ 跨算法一致的 vault 语义
@pytest.mark.parametrize("alg", ["fsrs", "sm2"])
def test_vault_semantics_identical_across_algorithms(alg):
    """**vault 侧语义跨算法一致**：`reps`/`lapses`/`state`/`status` 的含义不许因换算法而变。

    「连续记住两次即已掌握」是对用户的承诺（错题本默认隐藏已掌握的错题），
    换调度算法不该悄悄改掉它。
    """
    s = sched.get_scheduler(alg)
    st = {"id": "m8"}
    r1 = s.schedule(dict(st), 5, now=NOW)
    assert r1["reps"] == 1
    r2 = s.schedule(dict(r1), 5, now=NOW)
    assert r2["reps"] == 2
    r3 = s.schedule(dict(r2), 1, now=NOW)
    assert r3["reps"] == 0 and r3["lapses"] == 1
    assert r3["state"] == "relearning"


@pytest.mark.parametrize("alg", ["fsrs", "sm2"])
def test_schedule_does_not_mutate_input(alg):
    """`schedule()` 不得改入参（纯函数性）——否则「回放历史」类测试会互相污染。"""
    s = sched.get_scheduler(alg)
    st = {"id": "m9", "reps": 1, "lapses": 0, "ease": 2.5}
    snapshot = dict(st)
    s.schedule(st, 4, now=NOW)
    assert st == snapshot


# ================================================================ 工厂
def test_get_scheduler_defaults_to_fsrs():
    assert sched.get_scheduler("fsrs").name == "fsrs"
    assert sched.get_scheduler("sm2").name == "sm2"
    # 非法值回退（不抛异常——排期功能不该因为配置写错就整个挂掉）
    assert sched.get_scheduler("nope").name == "fsrs"


def test_get_scheduler_reads_config():
    """配置 `scheduler` 决定用哪个实现。

    conftest 的 autouse fixture 已把 `cfg.CONFIG_FILE` 指到 tmp_path，
    这里直接写它即可（不碰真实 `~/.medkit/config.json`）。
    """
    from medkit.core import config as cfg

    cfg.CONFIG_FILE.write_text('{"scheduler": "sm2"}', encoding="utf-8")
    assert sched.get_scheduler().name == "sm2"
    cfg.CONFIG_FILE.write_text('{"scheduler": "fsrs"}', encoding="utf-8")
    assert sched.get_scheduler().name == "fsrs"


def test_describe_reports_current_algorithm():
    info = sched.describe("fsrs")
    assert info["name"] == "fsrs"
    assert info["max_interval"] == 180
    assert info["retention"] == pytest.approx(0.9)
    assert set(info["available"]) == {"fsrs", "sm2"}


# ================================================================ 端到端（vault 落盘）
def test_vault_grade_writes_scheduler_fields(vroot):
    """复习一次后，front matter 必须落上 FSRS 字段 —— 否则下次复习会当全新卡。"""
    vault.write_record(
        {"id": "g9", "question": "FSRS 落盘题", "created_at": "2026-10-27T08:00:00"},
        vroot)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    out = vault_index.grade_item(rel, 4, root=vroot)
    assert out["scheduler"] == "fsrs"
    fm = vault.read_record(vroot / rel)
    assert fm["stability"] > 0 and fm["difficulty"] > 0
    assert fm["fsrs_state"] and fm["last_review"]
    assert fm["reps"] == 1 and fm["due"] > vault_index._today_iso()


def test_vault_grade_roundtrips_through_file(vroot):
    """第二次复习必须能**还原**第一次的 FSRS 状态（否则间隔会乱跳）。"""
    vault.write_record(
        {"id": "g10", "question": "往返题", "created_at": "2026-10-27T08:00:00"},
        vroot)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    first = vault_index.grade_item(rel, 4, root=vroot)
    second = vault_index.grade_item(rel, 4, root=vroot)
    # 第一次：learning（还在学习步里）；第二次：毕业进 review。
    # **若 FSRS 状态没落盘、第二次还原不出来，它会又停在 learning** —— 这正是本用例要钉的。
    assert first["fsrs_state"] == "learning", first["fsrs_state"]
    assert second["fsrs_state"] == "review", second["fsrs_state"]
    assert second["reps"] == 2


def test_vault_sm2_legacy_mode_still_works(vroot):
    """切到 SM-2 legacy 后，vault 复习仍按 SM-2 走（ease/interval 被更新）。"""
    from medkit.core import config as cfg

    cfg.CONFIG_FILE.write_text('{"scheduler": "sm2"}', encoding="utf-8")
    vault.write_record(
        {"id": "g11", "question": "legacy 题", "created_at": "2026-10-27T08:00:00"},
        vroot)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    out = vault_index.grade_item(rel, 5, root=vroot)
    assert out["scheduler"] == "sm2"
    assert out["ease"] == pytest.approx(2.6)
    assert out["interval"] == 1


# ================================================================ 接口层
def _client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


def test_api_review_due_reports_scheduler(vroot):
    """复习列表必须如实报出当前算法——排期口径变了要可见，不能让用户蒙在鼓里。"""
    vault.write_record({"id": "g12", "question": "到期题",
                        "created_at": "2026-10-27T08:00:00"}, vroot)
    vault_index.rebuild(vroot)
    body = _client().get("/api/vault/review/due").json()
    assert body["scheduler"] == "fsrs"
    assert body["count"] == 1


def test_api_grade_response_carries_scheduler(vroot):
    vault.write_record({"id": "g13", "question": "评分题",
                        "created_at": "2026-10-27T08:00:00"}, vroot)
    vault_index.rebuild(vroot)
    rel = vault_index.list_items()[0]["path"]
    body = _client().post(f"/api/vault/review/{rel}/grade", json={"quality": 4}).json()
    assert body["scheduler"] == "fsrs"
    assert body["reps"] == 1 and body["status"] == "unlearned"


def test_api_scheduler_get_and_switch(vroot):
    """切换端点：GET 报当前算法，PUT 切换并落配置。"""
    c = _client()
    info = c.get("/api/vault/scheduler").json()
    assert info["ok"] and info["name"] == "fsrs"
    assert info["max_interval"] == 180 and info["retention"] == pytest.approx(0.9)
    assert set(info["available"]) == {"fsrs", "sm2"}

    r = c.put("/api/vault/scheduler", json={"name": "sm2"})
    assert r.status_code == 200 and r.json()["name"] == "sm2"
    assert c.get("/api/vault/scheduler").json()["name"] == "sm2"
    # 切回来
    assert c.put("/api/vault/scheduler", json={"name": "fsrs"}).json()["name"] == "fsrs"


def test_api_scheduler_rejects_bad_input(vroot):
    """非法输入必须 400（不是 500），且**不得**改坏已有配置。"""
    c = _client()
    before = c.get("/api/vault/scheduler").json()["name"]
    for payload in ({"name": "nope"}, {"name": ""},
                    {"name": "fsrs", "fsrs_retention": 0.1},
                    {"name": "fsrs", "fsrs_retention": "abc"},
                    {"name": "fsrs", "fsrs_max_interval": 0},
                    {"name": "fsrs", "fsrs_max_interval": 99999},
                    {"name": "fsrs", "fsrs_max_interval": "abc"}):
        r = c.put("/api/vault/scheduler", json=payload)
        assert r.status_code == 400, f"{payload} 应 400，实得 {r.status_code}"
    assert c.get("/api/vault/scheduler").json()["name"] == before


# ================================================================ 前后端一致性
def test_frontend_review_buttons_match_backend_threshold():
    """**前端的三个复习按钮传的值，必须与后端的「算不算记住」阈值对得上。**

    ## 这条守卫在防什么（2026-10-27 实测的真缺陷）

    vault 的三个按钮原先传 **5 / 3 / 1**（记得 / 模糊 / 忘了），而后端
    `GRADE_PASS = 3` 判「quality ≥ 3 记作记住」——
    ⇒ **「模糊」(3) 被算成了「记住」**。连点两次模糊，这道错题就被判「已掌握」
    并从错题本默认视图里隐藏。用户明明说「有点模糊」，错题却「学会了」。

    CHANGELOG「决策 3」定的映射是 **忘=0 · 糊=2 · 记=4**（与记忆卡的 FSRS 四档同源），
    前端传的 5/3/1 与之差了一档 —— 这就是根因。

    ⇒ 守卫：前端按钮传的三个值必须满足
        忘了 < GRADE_PASS ≤ 记得        （只有「记得」算通过）
        模糊 < GRADE_PASS                （「模糊」不算通过）
       且三者互不相同。
    """
    import re

    js = (ROOT / "medkit" / "web" / "js" / "learn-vault.js").read_text(encoding="utf-8")
    # 抓 vaultGrade('...', N) 的实参
    vals = [int(m) for m in re.findall(r"vaultGrade\(.*?,\s*(\d+)\)", js)]
    assert len(vals) >= 3, f"没抓到三个复习按钮的 quality 实参：{vals}"
    assert len(set(vals)) == 3, f"三个按钮的值必须互不相同：{vals}"
    remembered = max(vals)
    vague = sorted(vals)[1]
    forgot = min(vals)
    assert remembered >= sched.GRADE_PASS, (
        f"「记住」({remembered}) 必须 ≥ GRADE_PASS({sched.GRADE_PASS})，否则记住了也不算数")
    assert vague < sched.GRADE_PASS, (
        f"「模糊」({vague}) 必须 < GRADE_PASS({sched.GRADE_PASS})，"
        f"否则「模糊」被当成「记住」⇒ 连点两次就判已掌握、错题从本子里消失")
    assert forgot < sched.GRADE_PASS, f"「忘了」({forgot}) 必须 < GRADE_PASS"


def test_frontend_review_buttons_use_decided_mapping():
    """按钮值必须就是 CHANGELOG「决策 3」定的 **忘=0 · 糊=2 · 记=4**。

    钉死具体值（而不只是大小关系）是为了让「有人又改成 5/3/1」立刻被看见——
    那组值会把「模糊」顶到阈值上，是本次修掉的缺陷本身。
    """
    import re

    js = (ROOT / "medkit" / "web" / "js" / "learn-vault.js").read_text(encoding="utf-8")
    vals = sorted({int(m) for m in re.findall(r"vaultGrade\(.*?,\s*(\d+)\)", js)})
    assert vals == [0, 2, 4], f"按钮值应为 忘=0 · 糊=2 · 记=4，实得 {vals}"
