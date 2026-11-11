# -*- coding: utf-8 -*-
"""按题库清除做题记录（`core/bank.reset_progress` + `/api/bank/banks/{bid}/reset`）。

## 这一组在守什么

用户原话：「做题数据只能靠删除全部用户数据来消除」。新能力一旦写歪，后果不是报错，
而是**悄悄多删**（连别的题库、手写笔记一起没）或**悄悄少删**（记录还在、掌握度还是旧的）。
两类都不会抛异常，只能靠范围断言抓。

判据都是**可观测的数**，不是「源码里有没有调某个函数」：

1. 预览的数字 == 实际删除的数字（确认框里的每个数字都得是真的）；
2. 只清目标题库：别的题库的流水、错题、掌握度一个不少；
3. 内容资产不动：题目、收藏 ★、笔记、`item_kc` 映射、导入原文（可重解析）；
4. **共享知识点按剩余流水重算**：两个题库练同一个考点，清掉一个后掌握度必须等于
   「只按另一个题库的流水重放」——这条是全文件最容易写错的地方（图省事就会整表重建，
   把别的题库的进度一起推平）；
5. 错题重练的流水（`mode=retry`，记的是错题 id）也属于该题库，清得掉；
6. 手动录入的错题（没有 `source_ref.bank_id`）不属于任何题库，不能被误伤；
7. 删除前有备份、删除后如实回执（`after` 重新数过）；不确认就 400。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit import main as m  # noqa: E402
from medkit.core import bank as banklib  # noqa: E402
from medkit.core import db as dbs  # noqa: E402
from medkit.core import kc as kclib  # noqa: E402
from medkit.core import library as lib  # noqa: E402
from medkit.core import syllabus as syl  # noqa: E402
from medkit.core import vault_index as vi  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    return TestClient(m.app, base_url="http://127.0.0.1")


@pytest.fixture()
def targets() -> list[dict]:
    """两个**不同**考点（放在同一科目下），供「共享知识点」与「互不干扰」两类用例。"""
    kclib.ensure_ready()
    syl.ensure_seed(force=True)
    pool = kclib.kc_pool()
    assert len(pool) >= 2, "知识点池不足两个，测不了跨库共享"
    return pool[:2]


def _bank(name: str, subtopic: str, *, questions: tuple[str, ...] = ("甲题",)) -> str:
    # 用 `create_bank` 的返回值：`list_banks()` 按 updated_at（秒级）倒序，
    # 同秒建两个库时顺序不稳定——拿列表首项会拿到上一个库，用例就测了错的对象。
    bank = banklib.create_bank(name, "内科学")
    bid = str(bank["id"])
    banklib.add_items(bid, [
        {"question": q, "subtopic": subtopic, "options": ["A. 甲", "B. 乙"], "answer": "A"}
        for q in questions])
    kclib.backfill(bank_id=bid, method="rule", dry_run=False)
    return bid


def _items(bid: str) -> list[dict]:
    return banklib.list_items(bid)


def _mistakes_of(bid: str) -> list[dict]:
    return lib.list_mistakes_of_bank(bid)


def _p(kc_id: str) -> float:
    row = kclib.get_state(kc_id) or {}
    if not row:
        return float(kclib.PRIOR_P_MASTERY)
    return float(row.get("p_mastery") or 0.0)


# ---------------------------------------------------------------- 范围：预览 == 实际
def test_preview_matches_actual_clear(client, targets):
    """确认框里的数字必须是真数字：预览 3 条作答 ⇒ 实际就删 3 条。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题", "乙题"))
    items = _items(bid)
    banklib.grade(bid, items[0]["id"], "A")
    banklib.grade(bid, items[1]["id"], "A")
    banklib.grade(bid, items[0]["id"], "B")      # 判错 → 回流一条错题

    p = client.get(f"/api/bank/banks/{bid}/reset-preview").json()
    assert (p["attempts"], p["correct"], p["wrong"]) == (3, 2, 1), p
    assert p["mistakes"] == 1 and p["mistakes_learned"] == 0, p

    r = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True, body
    assert body["cleared"]["attempts"] == p["attempts"], "清除数与预览不一致"
    assert body["cleared"]["mistakes"] == p["mistakes"], "错题清除数与预览不一致"
    assert body["after"] == {"attempts": 0, "mistakes": 0}, body["after"]


# ---------------------------------------------------------------- 边界：别的题库不动
def test_other_bank_untouched(client, targets):
    """清 A 之后，B 的流水、错题、统计一个不少。"""
    a = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    b = _bank("心血管", str(targets[1]["item"]), questions=("乙题",))
    banklib.grade(a, _items(a)[0]["id"], "B")
    banklib.grade(b, _items(b)[0]["id"], "A")
    banklib.grade(b, _items(b)[0]["id"], "B")

    assert client.post(f"/api/bank/banks/{a}/reset", json={"confirm": True}).status_code == 200

    assert banklib.list_attempts(bank_id=a) == []
    assert len(banklib.list_attempts(bank_id=b)) == 2, "别的题库的流水被一起删了"
    assert _mistakes_of(a) == []
    assert len(_mistakes_of(b)) == 1, "别的题库的错题被一起删了"
    assert banklib.bank_stats(b)["attempts"] == 2


# ---------------------------------------------------------------- 内容资产保留
def test_content_mappings_and_source_kept(client, targets):
    """题目 / 收藏 / 笔记 / 知识点映射 / 导入原文都不属于「做题痕迹」，必须留下。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题", "乙题"))
    items = _items(bid)
    banklib.set_starred(items[0]["id"], True)
    banklib.update_item(items[0]["id"], {"note": "自己写的一句话"})
    banklib.save_source(bid, "原文占位", fmt="md")
    assert kclib.mappings_for_item(items[0]["id"], only_confirmed=True), "前置：映射没建起来"
    banklib.grade(bid, items[0]["id"], "B")

    body = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True}).json()
    assert body["ok"] is True, body

    after = {i["id"]: i for i in _items(bid)}
    assert len(after) == 2, "题目被删了"
    assert after[items[0]["id"]]["starred"] is True, "收藏被删了"
    assert after[items[0]["id"]]["note"] == "自己写的一句话", "笔记被删了"
    assert kclib.mappings_for_item(items[0]["id"], only_confirmed=True), "知识点映射被删了"
    assert banklib.get_source(bid) is not None, "导入原文被删了（之后没法重解析）"
    assert body["kept"]["items"] == 2 and body["kept"]["mappings"] >= 1, body["kept"]


# ---------------------------------------------------------------- 共享知识点：按剩余重算
def test_shared_kc_is_recomputed_from_remaining_attempts(client, targets):
    """两个题库练同一个考点，清掉一个后掌握度 == 只按另一个题库的流水重放。

    这条是本文件的核心：整表 `rebuild_state()` 也能「看起来对」，但它会把
    所有知识点的 `last_seen` 推到当下、并清掉未映射行——那是顺手改别人的数据。
    """
    kc = str(targets[0]["kc_id"])
    a = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    b = _bank("呼吸系统二刷", str(targets[0]["item"]), questions=("乙题",))
    for _ in range(2):
        banklib.grade(a, _items(a)[0]["id"], "A")     # A：两次答对
    banklib.grade(b, _items(b)[0]["id"], "B")          # B：一次答错
    banklib.grade(b, _items(b)[0]["id"], "A")          # B：再一次答对

    row = kclib.get_state(kc) or {}
    assert int(row.get("attempts")) == 4, f"前置：共享知识点没累计 4 次，{row}"

    body = client.post(f"/api/bank/banks/{a}/reset", json={"confirm": True}).json()
    assert body["ok"] is True, body

    # 期望值：只重放 B 的两条流水（错 → 对）
    expect = kclib.apply_attempt(None, False)
    expect = kclib.apply_attempt(expect, True)
    row = kclib.get_state(kc) or {}
    assert int(row.get("attempts")) == 2, f"清 A 后应只剩 B 的 2 次，实际 {row}"
    assert int(row.get("correct")) == 1, row
    assert float(row.get("p_mastery")) == pytest.approx(float(expect["p_mastery"]), abs=1e-6), \
        f"掌握度不是按剩余流水重算的：{row} vs {expect}"


def test_non_shared_kc_is_zeroed(client, targets):
    """只被 A 练过的考点：清掉 A 后状态行消失（回到冷启动先验，而不是留下 0.78 的假进度）。"""
    kc = str(targets[0]["kc_id"])
    a = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(a, _items(a)[0]["id"], "A")
    assert kclib.get_state(kc) is not None

    client.post(f"/api/bank/banks/{a}/reset", json={"confirm": True})

    assert kclib.get_state(kc) is None, "只属于该题库的考点状态行应被回收"
    assert _p(kc) == pytest.approx(float(kclib.PRIOR_P_MASTERY)), "应回到冷启动先验"


def test_unrelated_kc_state_row_is_not_rewritten(client, targets):
    """**没被这次清除影响的知识点，状态行一个字节都不该变。**

    这条专门拦「顺手整表重建」：`kc.rebuild_state()` 也能算出正确的数，但它会把
    每一行的 `last_seen` / `updated_at` 推到当下——用户没动过的考点会突然显示「刚学过」。
    用一个过去的时间戳做哨兵，任何整表重建都会把它擦掉。
    """
    a = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    c = _bank("外科", str(targets[1]["item"]), questions=("丙题",))
    banklib.grade(a, _items(a)[0]["id"], "A")
    banklib.grade(c, _items(c)[0]["id"], "A")
    kc2 = str(targets[1]["kc_id"])
    row = kclib.get_state(kc2) or {}
    row.update({"last_seen": "2020-01-01T00:00:00", "updated_at": "2020-01-01T00:00:00"})
    kclib.upsert_state(row)

    r = client.post(f"/api/bank/banks/{a}/reset", json={"confirm": True})
    assert r.status_code == 200, r.text

    after = kclib.get_state(kc2) or {}
    assert after.get("last_seen") == "2020-01-01T00:00:00", f"无关知识点被改写了：{after}"
    assert after.get("updated_at") == "2020-01-01T00:00:00", after
    assert int(after.get("attempts")) == 1, "无关知识点的计数被改了"


# ---------------------------------------------------------------- 错题重练也属于该题库
def test_retry_attempts_are_counted_and_cleared(client, targets):
    """错题重练记的 `item_id` 是错题 id，但 `bank_id` 是本题库 ⇒ 预览与清除都要算上。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(bid, _items(bid)[0]["id"], "B")
    mid = _mistakes_of(bid)[0]["id"]
    r = client.post(f"/api/library/mistakes/{mid}/retry", json={"user_answer": "A"})
    assert r.status_code == 200, r.text

    assert len(banklib.list_attempts(bank_id=bid)) == 2, "重练没记到本题库流水里"
    p = client.get(f"/api/bank/banks/{bid}/reset-preview").json()
    assert p["attempts"] == 2, p

    body = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True}).json()
    assert body["cleared"]["attempts"] == 2, body
    assert banklib.list_attempts(bank_id=bid) == []
    assert lib.get_mistake(mid) is None, "重练过的错题还在"


def test_foreign_retry_still_counts_after_reset(client, targets):
    """**别少算**：手动录入的错题若 `source_ref.item_id` 指向本题库的题，它的重练流水
    `bank_id` 为空、`item_id` 是错题 id——清题库时既不该删这条流水，重算时也不能把它算丢。

    这条盯的是重算的**解析规则**（重练流水要经 `source_ref` 回指原题）。去掉这条规则，
    这个考点的掌握度会被重算成「从没练过」——用户看到进度倒退，却找不到原因。
    """
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    item_id = _items(bid)[0]["id"]
    manual = lib.add_mistake({"question": "手写录入但指向同一道题", "subject": "内科学",
                              "answer": "A", "options": ["甲", "乙"],
                              "source_ref": {"bank_id": "", "item_id": item_id}})
    r = client.post(f"/api/library/mistakes/{manual['id']}/retry", json={"user_answer": "A"})
    assert r.status_code == 200, r.text
    kc = str(targets[0]["kc_id"])
    assert int((kclib.get_state(kc) or {}).get("attempts") or 0) == 1, "前置：重练没进模型"

    client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})

    assert lib.get_mistake(manual["id"]) is not None, "不属于该题库的错题被删了"
    row = kclib.get_state(kc) or {}
    assert int(row.get("attempts") or 0) == 1, f"重练带来的掌握度被算丢了：{row}"


def test_manual_mistake_is_not_touched(client, targets):
    """手动录入的错题没有 `source_ref.bank_id`，不属于任何题库。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    manual = lib.add_mistake({"question": "手写录入的一道题", "subject": "内科学",
                              "answer": "A", "options": ["甲", "乙"]})
    banklib.grade(bid, _items(bid)[0]["id"], "B")
    assert len(_mistakes_of(bid)) == 1

    body = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True}).json()
    assert body["cleared"]["mistakes"] == 1, body
    assert lib.get_mistake(manual["id"]) is not None, "手动录入的错题被连带删了"


# ---------------------------------------------------------------- 备份与回执
def test_backup_contains_what_was_deleted(client, targets):
    """删除前必须留下可追溯的导出（含被删的作答与错题原文）。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(bid, _items(bid)[0]["id"], "B")

    body = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True}).json()
    path = Path(body["backup"])
    assert path.is_file(), f"备份不存在：{path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["kind"] == "bank_progress_reset"
    assert data["bank"]["id"] == bid
    assert len(data["attempts"]) == 1 and len(data["mistakes"]) == 1, data.keys()
    assert data["mistakes"][0]["question"], "备份里没有错题原文"


def test_reset_is_idempotent(client, targets):
    """再点一次不会报错、也不会删掉别的东西。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(bid, _items(bid)[0]["id"], "A")
    client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})

    second = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["ok"] is True and body["cleared"]["attempts"] == 0, body
    assert len(_items(bid)) == 1


# ---------------------------------------------------------------- 确认与错误码
def test_requires_explicit_confirm(client, targets):
    """没有 `confirm=true` ⇒ 400 且**一条都不删**（确认不是前端装饰）。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(bid, _items(bid)[0]["id"], "A")

    r = client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": False})
    assert r.status_code == 400, r.text
    r2 = client.post(f"/api/bank/banks/{bid}/reset", json={})
    assert r2.status_code == 400, r2.text
    assert len(banklib.list_attempts(bank_id=bid)) == 1, "未确认却删了流水"
    assert len(_items(bid)) == 1


def test_unknown_bank_is_404(client):
    assert client.get("/api/bank/banks/no-such-bank/reset-preview").status_code == 404
    r = client.post("/api/bank/banks/no-such-bank/reset", json={"confirm": True})
    assert r.status_code == 404, r.text


# ---------------------------------------------------------------- vault 联动
def test_vault_mirror_is_archived_not_left_behind(client, targets):
    """错题文件是真身：删记录后索引里不能再有它（否则错题本/助手还会读到已删的题）。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题",))
    banklib.grade(bid, _items(bid)[0]["id"], "B")
    mid = _mistakes_of(bid)[0]["id"]
    assert vi.paths_of(mid), "前置：判错没有补写进 vault"

    client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})

    assert vi.paths_of(mid) == [], "vault 索引里还留着已删除的错题"


def test_reset_does_not_touch_unrelated_tables(client, targets):
    """别的域的表不能被牵连：`banks` / `bank_items` / `bank_sources` 行数不变。"""
    bid = _bank("呼吸系统", str(targets[0]["item"]), questions=("甲题", "乙题"))
    banklib.save_source(bid, "原文", fmt="md")
    banklib.grade(bid, _items(bid)[0]["id"], "B")

    def counts() -> tuple[int, int, int]:
        with dbs.tx(write=False) as cur:
            return tuple(int(cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                         for t in ("banks", "bank_items", "bank_sources"))  # type: ignore[return-value]

    before = counts()
    client.post(f"/api/bank/banks/{bid}/reset", json={"confirm": True})
    assert counts() == before, "清记录动了题目/题库/原文表"


# ---------------------------------------------------------------- 入口不能「藏起来」
def test_entry_is_wired_in_the_bank_list():
    """功能存在但**没有入口**等于不存在。钉住三处接线：行菜单、全局导出、两个端点路径。"""
    js = (ROOT / "medkit" / "web" / "js" / "bank.js").read_text(encoding="utf-8")
    assert 'onclick="bankReset(this)"' in js, "题库行菜单里没有「清除做题记录」"
    assert "window.bankReset = bankReset;" in js, "内联 onclick 需要全局导出"
    assert "/reset-preview" in js and "/reset`" in js, "前端没调清除相关端点"
    py = (ROOT / "medkit" / "routers" / "bank.py").read_text(encoding="utf-8")
    assert '/api/bank/banks/{bid}/reset-preview' in py
    assert '/api/bank/banks/{bid}/reset"' in py
