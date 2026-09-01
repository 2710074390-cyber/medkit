"""R4 批次 3：打磨项后端侧（R4-09/13/14/15/16/17/18/24）。

覆盖（与 docs/reviews/ux-audit-r4-2026-08-31.md §6 批次 3 对应）：
- R4-09  取消出口给在飞子步骤补 cancelled 终态（`_cancel_out` 路径）
- R4-13  import-image 超限读后即判 400（不落盘/不进 OCR）
- R4-14  meta.json 内容非 dict（[]/字符串）→ 422 而非 500
- R4-15  llm_models 异常归一化（不回显原始异常串）
- R4-16  未分类复习卡不再重复计入每科（分区互斥口径）
- R4-17  cards_generate 在飞去重 409（连点不再重复调用 LLM）
- R4-18  短 Key 掩码只露前 2 后 2
- R4-24  purge-same 显式清同名复习卡/记忆卡（计数正确、分区口径）
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi import HTTPException

from medkit.core import config as core_cfg
from medkit.routers import _common
from medkit.routers import config as cfg_router
from medkit.routers import library as lib_router
from medkit.routers._common import _read_meta_checked


# ---------------------------------------------------------------- R4-14 meta 非 dict
def test_read_meta_checked_non_dict_422(tmp_path):
    base = tmp_path / "p"
    base.mkdir()
    (base / "meta.json").write_text("[]", encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        _read_meta_checked(base)
    assert ei.value.status_code == 422
    (base / "meta.json").write_text('"just a string"', encoding="utf-8")
    with pytest.raises(HTTPException) as ei:
        _read_meta_checked(base)
    assert ei.value.status_code == 422
    (base / "meta.json").write_text('{"stage": "done"}', encoding="utf-8")
    assert _read_meta_checked(base)["stage"] == "done"


# ---------------------------------------------------------------- R4-18 短 Key 掩码
def test_mask_api_key_short_keys():
    assert core_cfg.mask_api_key("") == ""
    assert core_cfg.mask_api_key("sk-ab") == "sk*ab"          # 5 位：露前 2 后 2（中间 1 星）
    assert core_cfg.mask_api_key("sk-abcd") == "sk***cd"      # 7 位
    assert core_cfg.mask_api_key("sk-abcdefg") == "sk******fg"  # 10 位：旧逻辑只藏 2 位 → 现藏 6
    assert core_cfg.mask_api_key("sk-abcdefgh") == "sk*******gh"  # 11 位
    full = "sk-abcdefghijkl"                                       # 15 位（≥12：4+7+4）
    assert core_cfg.mask_api_key(full) == "sk-a" + "*" * (len(full) - 8) + "ijkl"


# ---------------------------------------------------------------- R4-15 llm_models 归一化
def test_llm_models_error_hint_normalized(monkeypatch):
    from medkit.core.llm import LLMClient, LLMError

    def _fake_list_models(self, raise_on_error=True):
        raise LLMError("获取模型列表失败：API key 401 authentication failed for xyz.test/v1")

    monkeypatch.setattr(LLMClient, "list_models", _fake_list_models)
    resp = cfg_router.llm_models(cfg_router.ModelsBody(base_url="https://x.abc", api_key="k"))
    assert resp["ok"] is False
    assert "API Key 无效或未授权" in resp["msg"]
    # 不回显原始异常串（含 base_url/响应片段）
    assert "x.abc" not in resp["msg"]
    assert "authentication" not in resp["msg"]


# ---------------------------------------------------------------- R4-13 import-image 上限
class _FakeUpload:
    def __init__(self, data: bytes, filename: str = "x.png"):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


def _run_async(coro_fn):
    """在独立线程执行协程——兼容浏览器层同进程（主线程可能已有事件循环运行，
    直接 asyncio.run 会抛「cannot be called from a running event loop」）。"""
    import concurrent.futures as cf

    def _target():
        return asyncio.run(coro_fn())

    with cf.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_target).result(timeout=120)


def test_import_image_size_limit(monkeypatch, tmp_path):
    import medkit.core.config as cfg

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"mineru": {"api_key": "k"}}, ensure_ascii=False),
                        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
    # R4-13：读后即判（上限常量在 _common 单源；此处压缩到 8 字节模拟超限）
    monkeypatch.setattr(_common, "MAX_FILE_SIZE", 8)
    with pytest.raises(HTTPException) as ei:
        _run_async(lambda: lib_router.import_image(_FakeUpload(b"x" * 100)))
    assert ei.value.status_code == 400
    assert "200 MB" in str(ei.value.detail)


def test_import_image_rejects_bad_suffix(monkeypatch, tmp_path):
    import medkit.core.config as cfg

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"mineru": {"api_key": "k"}}, ensure_ascii=False),
                        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
    with pytest.raises(HTTPException) as ei:
        _run_async(lambda: lib_router.import_image(_FakeUpload(b"x" * 10, filename="a.txt")))
    assert ei.value.status_code == 400


# ---------------------------------------------------------------- R4-16 分区互斥
def _monkeypatch_lib_files(monkeypatch, tmp_path):
    import medkit.core.cards as cardlib
    import medkit.core.explain as expl
    import medkit.core.library as lib
    import medkit.core.review as rev

    for mod, attr, fname in (
            (lib, "MISTAKES_FILE", "mistakes.json"),
            (lib, "KNOWLEDGE_FILE", "knowledge.json"),
            (rev, "REVIEW_QUEUE_FILE", "review_queue.json"),
            (expl, "EXPLAINS_FILE", "explains.json"),
            (cardlib, "CARDS_FILE", "memory_cards.json")):
        p = tmp_path / fname
        monkeypatch.setattr(mod, attr, p)
        monkeypatch.setattr(mod, "DB_FILE", tmp_path / "no.db")
    return lib, rev, cardlib


def test_subjects_unclassified_not_counted(monkeypatch, tmp_path):
    import medkit.core.library as lib

    _monkeypatch_lib_files(monkeypatch, tmp_path)
    lib.add_mistake({"question": "q1", "subject": "内科学", "know_tags": ["肺通气"]})
    lib.add_mistake({"question": "q2", "subject": "外科学", "know_tags": ["骨折"]})
    rev = lib_router.rev
    rev.enqueue("肺通气", "内科学", "kp1")
    rev.enqueue("骨折", "外科学", "kp2")
    rev.enqueue("空科目卡", "", "kp0")          # 未分类卡：不再计入任何科目

    stats = lib_router.subjects()["stats"]
    by = {s["subject"]: s for s in stats}
    assert by["内科学"]["review_total"] == 1      # 只含本科目卡
    assert by["外科学"]["review_total"] == 1
    # partition 不变量：单科总数不含未分类
    assert by["内科学"]["review_due"] <= by["内科学"]["review_total"]


# ---------------------------------------------------------------- R4-17 cards_generate 去重
def test_cards_generate_dedupe_409(monkeypatch, tmp_path):
    import medkit.state as state
    from medkit.routers.library import CardsGenerateBody

    monkeypatch.setattr(state, "FLAGS", {"cards": True})
    _monkeypatch_lib_files(monkeypatch, tmp_path)
    monkeypatch.setattr(lib_router.expl, "get_explain",
                        lambda eid: {"id": eid, "subject": "内科学", "kp_name": "肺通气"})

    def _slow_gen(client, rec):
        time.sleep(0.4)
        return []

    monkeypatch.setattr(lib_router.medcards, "generate_cards", _slow_gen)
    body = CardsGenerateBody(explain_id="e1")
    first: dict[str, int] = {}

    def _call():
        try:
            lib_router.cards_generate(body)
            first["r"] = 200
        except HTTPException as e:
            first["r"] = e.status_code

    t = threading.Thread(target=_call)
    t.start()
    time.sleep(0.15)                                # 第一单在飞中
    with pytest.raises(HTTPException) as ei:
        lib_router.cards_generate(body)             # 重复提交 → 409
    assert ei.value.status_code == 409
    t.join()
    assert first["r"] == 502                        # 首单正常走完（无 drafts → 502 契约失败）
    # 在飞结束 → 可再次提交（不永久锁死；本次同样 502）
    with pytest.raises(HTTPException) as ei:
        lib_router.cards_generate(body)
    assert ei.value.status_code == 502


# ---------------------------------------------------------------- R4-09 取消补终态
def test_cancel_out_terminates_inflight_substeps(tmp_path):
    from medkit.core import orchestrator as och

    base = tmp_path / "p"
    base.mkdir()
    meta_path = base / "meta.json"
    meta_path.write_text(json.dumps({"pid": "p", "stage": "qc"}), encoding="utf-8")
    # 模拟 QC 批次事件滞留 running（_qc_step 预写下一批 running）
    och._substep(base, "qc", "batch2", "质检批次 2/5", "running", "提交中…")
    assert och._SUBSTEP_INFLIGHT
    res = och._cancel_out(base, meta_path, set(), [])
    assert res["stage"] == "cancelled"
    lines = (base / "substeps.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["status"] == "cancelled"
    assert last["step"] == "batch2"
    assert not och._SUBSTEP_INFLIGHT, "取消后登记表应清空（不再有永久 running）"


def test_run_project_error_terminates_inflight(monkeypatch, tmp_path):
    """R4-09 异常路径：run_project 抛错时在飞子步骤补 failed 终态。"""
    import medkit.core.config as cfg
    import medkit.core.orchestrator as och

    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    (proj / "meta.json").write_text(json.dumps({"pid": "p", "stage": "gate1"}),
                                    encoding="utf-8")
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"projects_dir": str(tmp_path / "projects")}),
                        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(och, "usage", _FakeUsage())

    def _boom(pid, seed=None, overrides=None, cancel=None):
        raise RuntimeError("boom")

    monkeypatch.setattr(och, "_run_project_impl", _boom)
    # 预置一个 running 子步骤（模拟异常抛出前在飞）
    och._substep(proj, "qc", "medqc", "MedQC 质检", "running", "…")
    with pytest.raises(RuntimeError):
        och.run_project("p")
    lines = (proj / "substeps.jsonl").read_text(encoding="utf-8").splitlines()
    last = json.loads(lines[-1])
    assert last["status"] == "failed"


class _FakeUsage:
    def activate(self):
        return object()

    def deactivate(self, token):
        return None

    def snapshot(self):
        return {"prompt_tokens": 0, "completion_tokens": 0}


# ---------------------------------------------------------------- R4-24 purge-same
def test_review_purge_same(monkeypatch, tmp_path):
    import medkit.core.library as lib
    from medkit.routers.library import PurgeSameBody

    _, rev, cardlib = _monkeypatch_lib_files(monkeypatch, tmp_path)
    lib.add_mistake({"question": "q", "subject": "内科学", "know_tags": ["肺通气"]})
    rev.enqueue("肺通气", "内科学", "kp1")
    rev.enqueue("骨折", "外科学", "kp2")
    cardlib.create_from_drafts(
        [{"front": "铁缺乏+贫血第一步", "back": "查血清铁蛋白", "kind": "concept"}],
        "内科学", "肺通气", "e1")
    cardlib.create_from_drafts(
        [{"front": "骨折第一步", "back": "固定", "kind": "concept"}],
        "外科学", "骨折", "e2")

    r = lib_router.review_purge_same(PurgeSameBody(subject="内科学", kp_name="肺通气"))
    assert r == {"ok": True, "review_removed": 1, "memory_removed": 1}
    # 别的科目不受影响
    assert len(rev.list_cards("外科学")) == 1
    assert len(cardlib.list_cards("外科学")) == 1
    # 空 kp 拒绝
    with pytest.raises(HTTPException) as ei:
        lib_router.review_purge_same(PurgeSameBody(subject="内科学", kp_name=""))
    assert ei.value.status_code == 400
