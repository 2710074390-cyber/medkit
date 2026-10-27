"""R4 批次 3：打磨项后端侧（R4-09/13/14/15/16/17/18/24）。

覆盖（与 docs/reviews/ux-audit-r4-2026-08-31.md §6 批次 3 对应）。
2026-10-15（奥卡姆精简 T2）：R4-16/17/24 三条随复习卡·记忆卡·讲解三域删除而移除。
- R4-09  取消出口给在飞子步骤补 cancelled 终态（`_cancel_out` 路径）
- R4-13  import-image 超限读后即判 400（不落盘/不进 OCR）
- R4-14  meta.json 内容非 dict（[]/字符串）→ 422 而非 500
- R4-15  llm_models 异常归一化（不回显原始异常串）
- R4-18  短 Key 掩码只露前 2 后 2
"""

from __future__ import annotations

import json

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
    """R4-15 判据不变（**异常串必须归一化、不回显**），但口径要跟上 2026-10 的改动：

    拉取失败后**不再直接返回空列表**——内置模型目录仍会兜底
    （`source_ok=False` + `ok=True`），所以「失败」这件事现在由
    `source_ok` 表达，而不是 `ok`。**归一化本身照旧必须做到**。

    ⚠️ 注意这里打的是 `list_models_detailed`：路由已改为调它
    （`list_models` 现在只是它的薄封装）。打在旧名字上会**静默失效**——
    失败注入根本没进路由，`source_ok` 会是 True，用例变成测空气。
    """
    from medkit.core.llm import LLMClient, LLMError

    def _fake_detailed(self, raise_on_error=True):
        raise LLMError("获取模型列表失败：API key 401 authentication failed for xyz.test/v1")

    monkeypatch.setattr(LLMClient, "list_models_detailed", _fake_detailed)
    # 用**无内置目录**的服务商，才能走到「真没东西可展示」的分支
    resp = cfg_router.llm_models(
        cfg_router.ModelsBody(base_url="https://x.abc", api_key="k", provider="custom"))
    assert resp["source_ok"] is False
    assert resp["ok"] is False
    assert resp["models"] == []
    assert "API Key 无效或未授权" in resp["msg"]
    # 不回显原始异常串（含 base_url/响应片段）
    assert "x.abc" not in resp["msg"]
    assert "authentication" not in resp["msg"]


def test_llm_models_falls_back_to_catalog(monkeypatch):
    """拉取失败但也**不许空白**：预置服务商有内置目录 ⇒ 仍能选模型。

    与上一条互为一对：同一注入、不同 provider，`ok`/`source_ok` 取值相反。
    这正是「`ok` 表达『有东西可展示』、`source_ok` 表达『拉取成功』」两字段分开的意义。
    """
    from medkit.core.llm import LLMClient, LLMError

    def _fake_detailed(self, raise_on_error=True):
        raise LLMError("获取模型列表失败：timeout")

    monkeypatch.setattr(LLMClient, "list_models_detailed", _fake_detailed)
    resp = cfg_router.llm_models(
        cfg_router.ModelsBody(base_url="https://x.abc", api_key="k", provider="deepseek"))
    assert resp["source_ok"] is False      # 没拉到
    assert resp["ok"] is True              # 但目录兜底，用户仍可选
    assert resp["models"], "内置目录必须提供兜底条目"
    assert all(it["source"] == "catalog" for it in resp["items"])
    assert resp["msg"], "失败原因必须如实给出（不许静默）"


# ---------------------------------------------------------------- R4-13 import-image 上限
class _FakeUpload:
    """UploadFile 替身。

    2026-10-22：`read()` 补上 `size` 形参 —— 与 `starlette.datastructures.UploadFile.read(size)`
    同签名。生产侧已改「分块读 + 超限即止」（`_common.read_upload_capped`），
    替身若仍只接受零参，会把真实的调用形态判成 TypeError（替身与真实接口不一致）。
    """

    def __init__(self, data: bytes, filename: str = "x.png"):
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if self._pos >= len(self._data):
            return b""
        end = len(self._data) if size is None or size < 0 else self._pos + size
        chunk = self._data[self._pos:end]
        self._pos += len(chunk)
        return chunk


# U-21：协程驱动统一到 `tests/conftest.py` 的 `run_coro` fixture（此前本文件另有一份
# `_run_async`（线程池实现）——同一问题两种写法，正是审查批评的「口径不一致」，已删除）。

def test_import_image_size_limit(monkeypatch, tmp_path, run_coro):
    import medkit.core.config as cfg

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"mineru": {"api_key": "k"}}, ensure_ascii=False),
                        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
    # R4-13/B-02：读后即判——图片走独立上限 MAX_IMAGE_BYTES（20MB）；此处压到 8 字节模拟超限
    monkeypatch.setattr(_common, "MAX_IMAGE_BYTES", 8)
    with pytest.raises(HTTPException) as ei:
        run_coro(lib_router.import_image(_FakeUpload(b"x" * 100)))
    assert ei.value.status_code == 400
    assert "20 MB" in str(ei.value.detail)


def test_import_image_rejects_bad_suffix(monkeypatch, tmp_path, run_coro):
    import medkit.core.config as cfg

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"mineru": {"api_key": "k"}}, ensure_ascii=False),
                        encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_FILE", cfg_file)
    with pytest.raises(HTTPException) as ei:
        run_coro(lib_router.import_image(_FakeUpload(b"x" * 10, filename="a.txt")))
    assert ei.value.status_code == 400


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
    def activate(self, limit_tokens: int = 0):   # W10：真身签名带 limit_tokens
        return object()

    def deactivate(self, token):
        return None

    def snapshot(self):
        return {"prompt_tokens": 0, "completion_tokens": 0}
