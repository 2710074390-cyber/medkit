"""WP-05/NX-04：医学记忆卡工厂 + 调度（FSRS 默认 / SM-2 可切）。

覆盖：CardDraft 契约 → create_from_drafts 幂等入库（JSON 两态 + SQL 表）、
FSRS 排程（freezegun 冻结时钟）、SM-2 legacy 等价性、算法切换只影响新卡、
路由（flag 门禁 + 生成/列表/自评/删除）、memory apkg 导出。全部零 LLM（mock client）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.main as m  # noqa: E402
from medkit import state  # noqa: E402
from medkit.core import cards as cardlib  # noqa: E402
from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import db as dbs  # noqa: E402
from medkit.core import explain as expl  # noqa: E402
from medkit.core.scheduler import make_scheduler  # noqa: E402


@pytest.fixture()
def iso(tmp_path, monkeypatch):
    """JSON 态隔离：cards 存储与 config 全部指向临时目录。"""
    libd = tmp_path / "library"
    libd.mkdir()
    monkeypatch.setattr(cardlib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(cardlib, "CARDS_FILE", libd / "memory_cards.json")
    monkeypatch.setattr(cardlib, "DB_FILE", libd / "medkit.db")
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    state.FLAGS["cards"] = True
    yield tmp_path
    state.FLAGS.pop("cards", None)


def _drafts(n: int = 4) -> list[dict]:
    return [{"kind": "value", "front": f"数值卡 {i}", "back": f"3.25kg/{i}", "extra": "忽略"}
            for i in range(n)]


# ---------------------------------------------------------------- 入库与幂等
def test_create_from_drafts_idempotent(iso):
    added = cardlib.create_from_drafts(_drafts(4), "儿科学", "生长发育", "exp_1")
    assert len(added) == 4
    for c in added:
        assert c["kind"] == "value" and c["sched"] == "fsrs"
        assert c["state"] == "new" and c["review_log"] == []
    # 幂等：同 source+kp+front 不再新增
    assert cardlib.create_from_drafts(_drafts(4), "儿科学", "生长发育", "exp_1") == []
    # 不同 explain（source 不同）→ 新卡
    assert len(cardlib.create_from_drafts(_drafts(4), "儿科学", "生长发育", "exp_2")) == 4
    assert len(cardlib.list_cards()) == 8
    assert len(cardlib.list_cards(due_only=True)) == 8       # new 卡 today due
    assert len(cardlib.list_cards(subject="内科学")) == 0


def test_create_from_drafts_sm2_binding(iso):
    added = cardlib.create_from_drafts(_drafts(2), "生理学", "体液", "exp_3", sched="sm2")
    assert all(c["sched"] == "sm2" for c in added)


# ---------------------------------------------------------------- FSRS 排程（冻结时钟）
def test_fsrs_grade_advances_due(iso, monkeypatch):
    from freezegun import freeze_time

    cards = cardlib.create_from_drafts(_drafts(1), "内科学", "心衰", "exp_4")
    cid = cards[0]["id"]
    with freeze_time("2026-08-27 09:00:00"):
        c1 = cardlib.grade_card(cid, 3)      # Good
        assert c1["state"] in ("learning", "review")
        assert c1["due"] > "2026-08-27T09:00:00"
        assert c1["reps"] == 1 and c1["lapses"] == 0
        assert (c1["sched_data"] or {}).get("state") == "Learning"
        # 第二次 Good → 排程继续推进
        c2 = cardlib.grade_card(cid, 5)      # Easy
        assert c2["due"] > c1["due"]
        assert c2["reps"] == 2
        # 遗忘：Again → lapses +1 且排程重置（relearning 状态）
        c3 = cardlib.grade_card(cid, 0)
        assert c3["lapses"] == 1
        assert c3["state"] == "relearning"


def test_fsrs_scheduler_deterministic():
    # enable_fuzzing=False：同一输入两次排程结果一致（可测可解释）
    s = make_scheduler("fsrs")
    card = {"sched_data": None, "review_log": [], "lapses": 0, "state": "new"}
    a = s.grade(dict(card), 3)
    b = s.grade(dict(card), 3)
    assert a["due"] == b["due"] and a["ease"] == b["ease"]


# ---------------------------------------------------------------- SM-2 legacy 等价与可切
def test_sm2_legacy_parity(iso):
    s = make_scheduler("sm2")
    card = {"ease": 2.5, "interval": 0, "reps": 0, "state": "new",
            "lapses": 0, "review_log": [], "due": "2026-08-27"}
    out = s.grade(dict(card), 3)
    # 与既有 core/review.grade_card 同实现 → 首记 interval=1、reps=1
    assert out["reps"] == 1 and out["interval"] == 1
    assert out["state"] == "learning"
    assert out["due"] >= "2026-08-28"
    # 失败：quality<3 → 重置为重学 + lapses+1
    out2 = s.grade(dict(card), 1)
    assert out2["lapses"] == 1 and out2["interval"] == 0
    assert out2["state"] == "relearning"


def test_switch_algo_affects_new_cards_only(iso):
    c_fsrs = cardlib.create_from_drafts(_drafts(1), "内科学", "心衰", "exp_5", sched="fsrs")[0]
    c_sm2 = cardlib.create_from_drafts(_drafts(1), "内科学", "心衰", "exp_6", sched="sm2")[0]
    cardlib.grade_card(c_fsrs["id"], 3)
    cardlib.grade_card(c_sm2["id"], 3)
    a = cardlib.get_card(c_fsrs["id"])
    b = cardlib.get_card(c_sm2["id"])
    assert a["sched"] == "fsrs" and (a["sched_data"] or {}).get("state") == "Learning"
    assert b["sched"] == "sm2" and b["sched_data"] is None
    assert b["interval"] == 1          # SM-2 首记 1 天
    assert a["interval"] != 1          # FSRS 学习步（分钟级）→ 展示间隔非 1 天


# ---------------------------------------------------------------- 旧库升级路径（打包/存量用户）
def test_old_db_autoupgrade_on_first_read(monkeypatch, tmp_path):
    """NX-04：旧库（v4，无 cards 表）首次读取自动迁移（存量用户升级路径，防 500）。
    v0.8.1：迁移目标已升至最新版（含 v6 真题年份列）；升级幂等（year 列存在时跳过 ALTER）。"""
    libd = tmp_path / "library"
    monkeypatch.setattr(cardlib, "LIBRARY_DIR", libd)
    monkeypatch.setattr(cardlib, "CARDS_FILE", libd / "memory_cards.json")
    monkeypatch.setattr(cardlib, "DB_FILE", libd / "medkit.db")
    dbs.migrate()                                # 先建到最新版再手工模拟 v4 旧库
    with dbs.tx(write=True) as cur:
        cur.execute("DROP TABLE IF EXISTS cards")
        cur.execute("PRAGMA user_version = 4")
    assert dbs.user_version() == 4
    assert cardlib.list_cards() == [], "旧库首读应自动升级而非 500"
    assert dbs.user_version() == dbs.MIGRATIONS[-1], "首读后应升级到最新版"


# ---------------------------------------------------------------- 路由（flag 门禁 + 全链路）
def _fake_medcards():
    class FakeLLM:
        def chat_json(self, messages, **kwargs):
            schema = kwargs.get("schema")
            return schema.model_validate({
                "cards": [
                    {"kind": "value", "front": f"数值卡 {i}", "back": f"答案 {i}"}
                    for i in range(5)]})

    import medkit.agents.medcards as mc
    mc.make_client = lambda: FakeLLM()
    return mc


def test_router_cards_flow_gate_and_grade(iso, monkeypatch):
    _fake_medcards()
    monkeypatch.setattr(expl, "EXPLAINS_FILE", cardlib.LIBRARY_DIR / "explains.json")
    expl.save_explain({"id": "exp_9", "subject": "内科学", "kp_name": "心衰",
                       "content": "心衰讲解全文……", "created_at": "2026-08-27T10:00:00"})
    c = TestClient(m.app, base_url="http://127.0.0.1")
    # 列表
    r = c.get("/api/library/cards")
    assert r.status_code == 200 and r.json()["total"] == 0
    # 生成（flag on）
    r = c.post("/api/library/cards/generate", json={"explain_id": "exp_9"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["added"] == 5 and j["total"] == 5
    # 幂等重生成
    assert c.post("/api/library/cards/generate", json={"explain_id": "exp_9"}).json()["added"] == 0
    # 今日到期
    due = c.get("/api/library/cards?subject=内科学&due=1").json()
    assert due["total"] == 5 and due["stats"]["due"] == 5
    cid = due["cards"][0]["id"]
    # 自评
    r = c.post(f"/api/library/cards/{cid}/grade", json={"quality": 3})
    assert r.status_code == 200
    assert r.json()["card"]["reps"] == 1
    # 删除
    assert c.delete(f"/api/library/cards/{cid}").status_code == 200
    assert c.get("/api/library/cards").json()["total"] == 4
    # 不存在 → 404
    assert c.post("/api/library/cards/no-such/grade", json={"quality": 3}).status_code == 404


def test_router_cards_flag_off_404(iso):
    state.FLAGS["cards"] = False
    c = TestClient(m.app, base_url="http://127.0.0.1")
    r = c.post("/api/library/cards/generate", json={"explain_id": "x"})
    assert r.status_code == 404
    state.FLAGS["cards"] = True


# ---------------------------------------------------------------- apkg 记忆卡导出
def test_export_memory_apkg(iso, tmp_path):
    from medkit.render.apkg import export_memory_apkg

    cardlib.create_from_drafts(_drafts(3), "儿科学", "生长发育", "exp_8")
    out = tmp_path / "mem.apkg"
    export_memory_apkg(cardlib.list_cards(), "儿科学", "mem_test", out)
    data = out.read_bytes()
    assert data[:2] == b"PK" and len(data) > 5000      # zip 包（genanki）
