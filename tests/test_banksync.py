# -*- coding: utf-8 -*-
"""题库同步（`core/banksync.py` + `routers/banksync.py`）测试。

## 本文件守的是什么

同步通道的风险**不在"能不能下载"**，而在三处会静默出错的地方：

1. **适配降级**：上游两代题面结构（`question_text` / `stem`）+ 押题卷第三种写法（`qid`/`system`）。
   漏认一种 ⇒ 那一批题**整批变成空题干被跳过**，而接口仍返回 200；
2. **幂等**：重复同步不能把题库刷成两倍，也不能把用户的作答流水冲掉；
3. **局部失败**：11 个源里坏 1 个，另外 10 个必须照常入库（否则用户永远同步不完）。

网络层一律用 `_http_json` 的替身注入——**测试不碰真网**（离线 CI 也要能跑）。

隔离：`tests/conftest.py` 的 autouse fixture 已把 `cfg.CONFIG_DIR` / `dbs.DB_PATH` 重定向到
本用例 tmp_path，故状态文件与题库库都落在临时目录，不触碰真实 `~/.medkit`。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import medkit.core.bank as bank  # noqa: E402
import medkit.core.banksync as bs  # noqa: E402
import medkit.main as m  # noqa: E402

# ---------------------------------------------------------------- 上游样本（取自真实产物字段）
V2_ITEM = {
    "id": "batch040-M01-A1-001",
    "module": "M01",
    "module_name": "总论与专科检查",
    "topic": "额镜的功能",
    "type": "A1",
    "bloom": "记忆",
    "stem": "额镜在耳鼻咽喉科检查中的主要作用是",
    "options": [{"label": "A", "text": "对检查部位进行加温"},
                {"label": "B", "text": "对检查部位进行放大"},
                {"label": "C", "text": "对检查部位进行照明"}],
    "answer": "C",
    "explanation": "额镜是聚光凹面镜，核心作用是照明。",
    "source_page": "教材P11",
}

# 同批第二题（题干不同 ⇒ 不会被批内去重合并）
V2_ITEM_B = {
    **V2_ITEM,
    "id": "batch040-M01-A1-002",
    "topic": "音叉试验",
    "stem": "林纳试验气导大于骨导提示",
    "options": [{"label": "A", "text": "感音神经性聋"},
                {"label": "B", "text": "正常或传导性聋"},
                {"label": "C", "text": "无法判断"}],
    "answer": "B",
}

V1_ITEM = {
    "id": "batch032_M1_A1_001",
    "question_type": "A1",
    "module": "胸部损伤",
    "bloom_level": "记忆",
    "question_text": "关于肋骨骨折的好发部位，下列叙述正确的是",
    "options": ["A. 第1～3肋骨", "B. 第4～7肋骨", "C. 第8～10肋骨"],
    "correct_answer": "B",
    "explanation": "第4～7肋骨较长而纤薄，最易骨折。",
    "source_page": "教材P257",
    "source_anchor": "RAG/教材P257",
}

PAPER_ITEM = {
    "qid": "batch041-M01-A1-001",
    "system": "总论与专科检查",
    "bloom": "记忆",
    "source": "复习重点·一、总论与专科检查",
    "stem": "耳鼻咽喉科最基础的通用照明反光器械是",
    "options": ["额镜", "耳内镜", "频闪喉镜"],
    "answer": "A",
    "explanation": "额镜为圆形聚光凹面镜。",
    "type": "A1",
}


def _blob(obj: Any) -> dict[str, Any]:
    raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return {"content": base64.b64encode(raw).decode("ascii"), "size": len(raw),
            "encoding": "base64", "sha": "x"}


def _tree(paths: dict[str, str]) -> dict[str, Any]:
    return {"tree": [{"path": p, "type": "blob", "sha": sha, "size": 100}
                     for p, sha in paths.items()], "truncated": False}


class FakeRemote:
    """GitHub API 的替身：按 URL 里的 sha 派回预置内容，并**记录下载次数**。"""

    def __init__(self, tree_paths: dict[str, str], blobs: dict[str, Any],
                 failing: set[str] | None = None,
                 ratelimited: set[str] | None = None) -> None:
        self.tree_paths = tree_paths
        self.blobs = blobs
        self.failing = failing or set()
        self.ratelimited = ratelimited or set()
        self.blob_calls: list[str] = []

    def __call__(self, url: str, what: str) -> Any:
        if "/git/trees/" in url:
            return _tree(self.tree_paths)
        for sha, payload in self.blobs.items():
            if url.endswith("/git/blobs/" + sha):
                self.blob_calls.append(sha)
                if sha in self.ratelimited:
                    raise bs.SyncError("ratelimit", "GitHub 接口限流（匿名 60 次/小时）")
                if sha in self.failing:
                    raise bs.SyncError("network", f"{what}拉取失败：IncompleteRead")
                return _blob(payload)
        raise bs.SyncError("notfound", f"{what}不存在")


@pytest.fixture(autouse=True)
def _reset_tree_cache():
    bs._reset_cache()
    yield
    bs._reset_cache()


@pytest.fixture()
def client():
    return TestClient(m.app, base_url="http://127.0.0.1")


# ---------------------------------------------------------------- 适配器（纯函数）
def test_adapt_v2_keeps_type_bloom_topic_and_page():
    rec = bs.adapt_item(V2_ITEM, batch="batch040", subject="耳鼻咽喉头颈外科学")
    assert rec is not None
    assert rec["question"] == V2_ITEM["stem"]
    assert rec["options"] == ["对检查部位进行加温", "对检查部位进行放大", "对检查部位进行照明"]
    assert rec["answer"] == "C"
    assert rec["type"] == "A1"
    assert rec["bloom"] == "记忆"
    assert rec["subtopic"] == "额镜的功能"      # 考点（topic）优先于章节名（module_name）
    assert bs.adapt_item({**V2_ITEM, "topic": ""})["subtopic"] == "总论与专科检查"
    # 通用 JSON 导入会把这两项丢掉（这正是本适配器存在的理由）
    assert rec["origin_ref"]["page"] == "教材P11"
    assert rec["origin_ref"]["source_id"] == "batch040-M01-A1-001"


def test_adapt_v1_legacy_schema():
    """batch032 用的是另一套字段名（`question_text`/`correct_answer`），必须同样认得。"""
    rec = bs.adapt_item(V1_ITEM, batch="batch032", subject="外科")
    assert rec is not None
    assert rec["question"] == V1_ITEM["question_text"]
    assert rec["answer"] == "B"
    assert rec["type"] == "A1"
    assert rec["bloom"] == "记忆"
    # v1 选项是带字母的字符串：原样交给 bank_iq 统一剥前缀（此处不重复实现）
    assert rec["options"][0] == "A. 第1～3肋骨"


def test_adapt_paper_schema_qid_and_system():
    """押题卷那份产物用的是 `qid` / `system` / `source`（第三种写法）。"""
    rec = bs.adapt_item(PAPER_ITEM, batch="", subject="耳鼻咽喉头颈外科学")
    assert rec is not None
    assert rec["subtopic"] == "总论与专科检查"
    assert rec["origin_ref"]["source_id"] == "batch041-M01-A1-001"
    assert rec["origin_ref"]["anchor"] == "复习重点·一、总论与专科检查"


def test_adapt_english_bloom_is_translated():
    """`registry_meta.by_bloom` 实测有 219 条 `comprehension` 这类英文值，落库前要译回中文。"""
    raw = dict(V2_ITEM, bloom="comprehension")
    rec = bs.adapt_item(raw)
    assert rec is not None
    assert rec["bloom"] == "理解"


def test_adapt_kaoyan_year_and_mode_variants():
    """`mode` 两种写法（`原题` / `原题（真题原样，未改编）`）都要判成原题。"""
    for mode, expected in (("原题", "考研真题"), ("原题（真题原样，未改编）", "考研真题"),
                           ("改编", "考研真题·改编"),
                           ("改编（选项重写）", "考研真题·改编")):
        raw = dict(V2_ITEM, kaoyan_origin={"year": 2013, "mode": mode})
        rec = bs.adapt_item(raw)
        assert rec is not None
        assert rec["source_type"] == expected, mode
        assert rec["source_year"] == "2013"


def test_adapt_without_stem_is_skipped():
    assert bs.adapt_item({"options": ["甲"]}) is None
    assert bs.adapt_item({"stem": "   "}) is None
    assert bs.adapt_item("不是字典") is None


def test_irregular_option_labels_keep_their_letter():
    """字母不规整时**不能只取文本**：那会把「B」变成「A」，答案直接对不上号。"""
    raw = dict(V2_ITEM, options=[{"label": "B", "text": "甲"}, {"label": "C", "text": "乙"}],
               answer="B")
    rec = bs.adapt_item(raw)
    assert rec is not None
    assert rec["options"] == ["（B）甲", "（C）乙"]
    assert "字母不规整" in rec["_warn"]


def test_subject_inference_from_sibling_markdown():
    paths = [
        "最终产物/batch040/ALL_questions_FIXED.json",
        "最终产物/batch040/ALL_questions_FIXED.md",
        "最终产物/batch040/耳鼻咽喉头颈外科学·期末题库.md",
        "最终产物/batch040/AGENT4_修改声明.md",
        "最终产物/batch042/ALL_questions_FIXED.json",
        "最终产物/batch042/耳鼻咽喉头颈外科学·真题专项题库.md",
        "最终产物/batch042/ENT真题专项_溯源表.md",
        "最终产物/押题卷_口腔科学_100题.json",
    ]
    assert bs._subject_from_paths(paths[0], paths) == "耳鼻咽喉头颈外科学"
    assert bs._subject_from_paths(paths[4], paths) == "耳鼻咽喉头颈外科学"
    # 回归：`AGENT4_修改声明.md` / `…_溯源表.md` 曾因「取最短 md 名」被当成科目，
    # 题库名变成「AGENT4_修改声明（MedAgentWork · batch035）」
    assert bs._subject_from_paths(paths[7], paths) == "口腔科学"
    only_junk = ["最终产物/batch035/ALL_questions_FIXED.json",
                 "最终产物/batch035/ALL_questions_FIXED.md",
                 "最终产物/batch035/AGENT4_修改声明.md"]
    assert bs._subject_from_paths(only_junk[0], only_junk) == ""
    # 没有任何科目线索时**不猜**
    assert bs._clean_subject("ALL_questions_FIXED") == "ALL_questions_FIXED"
    assert bs._subject_from_paths("最终产物/batch099/ALL_questions_FIXED.json",
                                  ["最终产物/batch099/ALL_questions_FIXED.json"]) == ""


def test_non_bank_json_files_are_excluded():
    """日志与合并记录不是题目——把它们当题库会同步出一堆空批次。"""
    assert bs._is_bank_file("最终产物/batch040/ALL_questions_FIXED.json")
    assert bs._is_bank_file("最终产物/押题卷_耳鼻咽喉头颈外科学_100题.json")
    assert not bs._is_bank_file("最终产物/batch040/AGENT4_追溯日志.json")
    assert not bs._is_bank_file("最终产物/batch040/learning_value_v2_merge_log.json")
    assert not bs._is_bank_file("question_bank/registry.jsonl")


# ---------------------------------------------------------------- 清单与状态
def _remote_two_batches() -> FakeRemote:
    return FakeRemote(
        {
            "最终产物/batch040/ALL_questions_FIXED.json": "s40",
            "最终产物/batch040/耳鼻咽喉头颈外科学·期末题库.md": "m40",
            "最终产物/batch040/AGENT4_追溯日志.json": "log40",
            "最终产物/押题卷_口腔科学_100题.json": "p1",
            "question_bank/registry_meta.json": "meta",
        },
        {
            "s40": [V2_ITEM, V2_ITEM_B],
            "p1": {"meta": {"subject": "口腔科学"}, "questions": [PAPER_ITEM]},
            "meta": {"updated_at": "2026-10-27T00:27:02", "stats": {"total": 6490,
                                                                   "by_batch": {"batch040": 200}}},
        },
    )


def test_list_sources_reports_batch_subject_and_estimate(monkeypatch):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    info = bs.list_sources()
    by_path = {s["path"]: s for s in info["sources"]}
    assert set(by_path) == {"最终产物/batch040/ALL_questions_FIXED.json",
                            "最终产物/押题卷_口腔科学_100题.json"}
    src = by_path["最终产物/batch040/ALL_questions_FIXED.json"]
    assert src["batch"] == "batch040"
    assert src["subject"] == "耳鼻咽喉头颈外科学"
    assert src["est_count"] == 200          # 来自 registry_meta.by_batch
    assert info["remote_total"] == 6490
    assert info["remote_updated_at"].startswith("2026-10-27")
    # 根目录押题卷没有批次目录
    assert by_path["最终产物/押题卷_口腔科学_100题.json"]["batch"] == ""


def test_status_verdicts_new_changed_synced(monkeypatch):
    remote = _remote_two_batches()
    monkeypatch.setattr(bs, "_http_json", remote)
    st = bs.status()
    assert st["ok"] is True
    assert {s["state"] for s in st["sources"]} == {"new"}

    res = bs.sync(force=False)
    assert res["failed"] == 0
    assert res["added"] == 3

    bs._reset_cache()
    st2 = bs.status()
    assert {s["state"] for s in st2["sources"]} == {"synced"}

    # 远端 sha 变了 ⇒ 同一份文件判为「有更新」
    remote.tree_paths["最终产物/batch040/ALL_questions_FIXED.json"] = "s41"
    remote.blobs["s41"] = [V2_ITEM]
    bs._reset_cache()
    st3 = bs.status()
    verdict = {s["path"]: s["state"] for s in st3["sources"]}
    assert verdict["最终产物/batch040/ALL_questions_FIXED.json"] == "changed"


def test_status_degrades_to_local_state_on_network_error(monkeypatch):
    """远端不可达时不能报错——用户要先看到「上次同步于…」，而不是一个红叉。"""
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    bs.sync(force=False)
    bs._reset_cache()

    def boom(url: str, what: str) -> Any:
        raise bs.SyncError("network", "连不上 GitHub")

    monkeypatch.setattr(bs, "_http_json", boom)
    st = bs.status()
    assert st["ok"] is False
    assert st["local_only"] is True
    assert "连不上" in st["error"]
    assert {s["state"] for s in st["sources"]} == {"offline"}
    assert st["sources"][0]["bank_id"]


def test_state_file_survives_corruption(monkeypatch):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    bs.sync(force=False)
    path = bs._state_file()
    path.write_text("{ 坏掉的 json", encoding="utf-8")
    state = bs.load_state()
    assert state["sources"] == {}         # 不抛错，按首次同步处理


# ---------------------------------------------------------------- 执行同步
def test_sync_creates_bank_with_fidelity(monkeypatch):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    res = bs.sync(force=False)
    assert res["ok"] is True
    assert res["added"] == 3 and res["failed"] == 0

    banks = {b["name"]: b for b in res["banks"]}
    assert "耳鼻咽喉头颈外科学（MedAgentWork · batch040）" in banks
    # 同名科目 + 不同批次必须区分得开（否则题库列表里四项同名）
    assert "口腔科学（MedAgentWork）" in banks

    bid = banks["耳鼻咽喉头颈外科学（MedAgentWork · batch040）"]["bank_id"]
    items = bank.list_items(bid)
    assert len(items) == 2
    it = items[0]
    assert it["type"] == "A1"
    assert it["bloom"] == "记忆"
    assert it["subtopic"] == "额镜的功能"
    assert it["origin"] == "medagentwork"
    assert it["origin_ref"]["page"] == "教材P11"
    # 选项入库时已剥字母前缀（渲染层统一补），否则会渲染成「A. A. 加温」
    assert all(not o.startswith("A.") for o in it["options"])


def test_sync_skips_download_when_sha_unchanged(monkeypatch):
    remote = _remote_two_batches()
    monkeypatch.setattr(bs, "_http_json", remote)
    bs.sync(force=False)
    remote.blob_calls.clear()
    bs._reset_cache()
    res = bs.sync(force=False)
    assert res["unchanged"] == 2
    # 只允许取 1 次索引元数据（2KB，用于报「远端共 N 题」），题库正文一份都不重下
    assert [c for c in remote.blob_calls if c != "meta"] == []


def test_resync_updates_in_place_and_keeps_attempts(monkeypatch):
    """远端修了一道题的解析 → 同步后**同一道题**（id 不变），作答流水不能丢。"""
    remote = _remote_two_batches()
    monkeypatch.setattr(bs, "_http_json", remote)
    first = bs.sync(force=False)
    bid = next(b["bank_id"] for b in first["banks"] if b["subject"] == "耳鼻咽喉头颈外科学")
    items = bank.list_items(bid)
    iid = items[0]["id"]
    bank.record_attempt(bid, iid, user_answer="C", correct=True)

    fixed = dict(V2_ITEM, explanation="额镜的核心作用是照明（已修订）。")
    remote.tree_paths["最终产物/batch040/ALL_questions_FIXED.json"] = "s42"
    remote.blobs["s42"] = [fixed, V2_ITEM_B]
    bs._reset_cache()
    res = bs.sync(force=False)
    assert res["failed"] == 0
    after = bank.list_items(bid)
    same = [x for x in after if x["id"] == iid]
    assert same, "同题应就地更新，id 不该变"
    assert "已修订" in same[0]["analysis"]
    assert len(bank.list_attempts(bank_id=bid, item_id=iid)) == 1


def test_ratelimit_stops_further_downloads(monkeypatch):
    """限流后**不再逐份重试**：额度不会在循环里恢复，多打 8 次只是让用户多等 8 秒。"""
    remote = FakeRemote(
        {"最终产物/batch040/ALL_questions_FIXED.json": "r40",
         "最终产物/batch041/ALL_questions_FIXED.json": "r41",
         "最终产物/batch042/ALL_questions_FIXED.json": "r42"},
        {"r40": [V2_ITEM], "r41": [V2_ITEM], "r42": [V2_ITEM]},
        ratelimited={"r40", "r41", "r42"},
    )
    monkeypatch.setattr(bs, "_http_json", remote)
    res = bs.sync(force=False)
    assert res["failed"] == 3
    assert remote.blob_calls == ["r40"], "限流后仍继续请求了后续文件"
    assert all("限流" in (r.get("error") or "") for r in res["results"])


def test_truncated_blob_is_detected(monkeypatch):
    """响应 200 但 base64 短一截：必须按 `size` 判出来，不能当成「远端 JSON 坏了」。"""
    monkeypatch.setattr(bs, "RETRIES", 1)
    full = json.dumps([V2_ITEM]).encode("utf-8")

    def truncated(url: str, what: str) -> dict[str, Any]:
        half = full[: len(full) // 2]
        return {"content": base64.b64encode(half).decode("ascii"),
                "size": len(full), "encoding": "base64"}

    monkeypatch.setattr(bs, "_http_json", truncated)
    with pytest.raises(bs.SyncError) as ei:
        bs._blob_json("sha1", "题库")
    assert ei.value.kind == "network"
    assert "不完整" in ei.value.message


def test_meta_is_cached_between_status_calls(monkeypatch):
    """反复开面板不该反复打接口——匿名额度只有 60 次/小时。"""
    remote = _remote_two_batches()
    monkeypatch.setattr(bs, "_http_json", remote)
    bs.status()
    bs.status()
    assert remote.blob_calls.count("meta") == 1


def test_one_failed_source_does_not_block_others(monkeypatch):
    remote = _remote_two_batches()
    remote.failing = {"p1"}
    monkeypatch.setattr(bs, "_http_json", remote)
    res = bs.sync(force=False)
    assert res["failed"] == 1
    assert res["added"] == 2               # batch040 照常入库
    statuses = {r["path"]: r["status"] for r in res["results"]}
    assert statuses["最终产物/押题卷_口腔科学_100题.json"] == "failed"
    assert statuses["最终产物/batch040/ALL_questions_FIXED.json"] == "synced"
    assert res["ok"] is False


def test_sync_rejects_concurrent_run(monkeypatch):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    assert bs._SYNC_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(bs.SyncError) as ei:
            bs.sync()
        assert ei.value.kind == "busy"
    finally:
        bs._SYNC_LOCK.release()


def test_sync_on_empty_remote_raises(monkeypatch):
    monkeypatch.setattr(bs, "_http_json", FakeRemote({}, {}))
    with pytest.raises(bs.SyncError) as ei:
        bs.sync()
    assert ei.value.kind == "empty"


# ---------------------------------------------------------------- 端点
def test_router_sources_and_run(monkeypatch, client):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    r = client.get("/api/bank/sync/sources")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert len(data["sources"]) == 2

    r2 = client.post("/api/bank/sync/run", json={"paths": [], "dedupe": "update"})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert body["added"] == 3

    r3 = client.get("/api/bank/sync/state")
    assert r3.status_code == 200
    assert len(r3.json()["sources"]) == 2


def test_router_busy_returns_409(monkeypatch, client):
    monkeypatch.setattr(bs, "_http_json", _remote_two_batches())
    assert bs._SYNC_LOCK.acquire(blocking=False)
    try:
        r = client.post("/api/bank/sync/run", json={"paths": []})
        assert r.status_code == 409
        assert "同步任务" in r.json()["detail"]
    finally:
        bs._SYNC_LOCK.release()
