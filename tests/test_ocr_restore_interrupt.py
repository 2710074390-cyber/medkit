"""W5 守卫：OCR 重启后**非终态归一为 interrupted**。

背景（2026-10-02 二轮审计 W5）：`jobs.json` 中的 `queued`/`running` 属于**上一进程**，
重启后后台线程已死。若原样恢复：
- 前端轮询 `while (… !terminal.includes(state))` **永真** ⇒ 永久转圈、无错误提示；
- `_cleanup_orphan_tmp` 以 `known`（含僵尸记录）判孤儿 ⇒ 对应上传 tmp 永不被清。

本文件三道防线：
1. 结构守卫：`_restore_jobs_from_disk` 归一逻辑存在（AST 判 `interrupted` 字面量赋值）；
2. 行为守卫：驱动**真身** `_restore_jobs_from_disk`，断言 `running`/`queued` → `interrupted`，
   且终态（done/failed/cancelled）**不被改写**；
3. 前端守卫：`interrupted` 必须进终态集合（否则 UI 仍会卡住）。
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from medkit.routers import ocr as ocr_mod

_OCR_SRC = Path(__file__).resolve().parents[1] / "medkit" / "routers" / "ocr.py"
_JS_SRC = Path(__file__).resolve().parents[1] / "medkit" / "web" / "js" / "review-desk-materials.js"


@pytest.fixture()
def job_dir(tmp_path, monkeypatch):
    """隔离 OCR_JOB_DIR 到 tmp（不污染 ~/.medkit）。"""
    d = tmp_path / "ocr_jobs"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(ocr_mod, "OCR_JOB_DIR", d)
    # OCR_JOBS 是全局 dict，用例前后清空避免串扰
    with ocr_mod.OCR_LOCK:
        ocr_mod.OCR_JOBS.clear()
    yield d
    with ocr_mod.OCR_LOCK:
        ocr_mod.OCR_JOBS.clear()


def _write_jobs(d: Path, data: dict) -> None:
    (d / "jobs.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _job(state: str, **extra) -> dict:
    base = {"id": "x", "name": "a.pdf", "role": "ocr", "state": state,
            "msg": "…", "result": None, "created": 1.0}
    base.update(extra)
    return base


# ------------------------------------------------------------------ 结构守卫
def test_restore_normalizes_non_terminal_ast() -> None:
    """结构：`_restore_jobs_from_disk` 内出现 `interrupted` 字面量赋值。"""
    tree = ast.parse(_OCR_SRC.read_text(encoding="utf-8"))
    fn = next(
        (n for n in ast.walk(tree)
         if isinstance(n, ast.FunctionDef) and n.name == "_restore_jobs_from_disk"),
        None,
    )
    assert fn is not None
    has = any(
        isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and node.value.value == "interrupted"
        for node in ast.walk(fn)
    )
    assert has, "未找到非终态归一为 interrupted 的赋值"


def test_terminal_states_constant_exists() -> None:
    """结构：终态集合是模块级常量（承接新状态时只改一处）。"""
    assert isinstance(ocr_mod._TERMINAL_STATES, frozenset)
    assert "done" in ocr_mod._TERMINAL_STATES
    assert "running" not in ocr_mod._TERMINAL_STATES


# ------------------------------------------------------------------ 行为守卫（驱动真身）
def test_running_becomes_interrupted(job_dir) -> None:
    _write_jobs(job_dir, {"j1": _job("running")})
    ocr_mod._restore_jobs_from_disk()
    j = ocr_mod.OCR_JOBS["j1"]
    assert j["state"] == "interrupted"
    assert "中断" in j["msg"]


def test_queued_becomes_interrupted(job_dir) -> None:
    _write_jobs(job_dir, {"j2": _job("queued")})
    ocr_mod._restore_jobs_from_disk()
    assert ocr_mod.OCR_JOBS["j2"]["state"] == "interrupted"


@pytest.mark.parametrize("st", ["done", "failed", "cancelled"])
def test_terminal_states_preserved(job_dir, st: str) -> None:
    """终态不得被改写（归一只能作用在非终态）。"""
    _write_jobs(job_dir, {"j3": _job(st, msg="原样")})
    ocr_mod._restore_jobs_from_disk()
    j = ocr_mod.OCR_JOBS["j3"]
    assert j["state"] == st
    assert j["msg"] == "原样"


def test_restore_rebuilds_cancel_event(job_dir) -> None:
    """恢复后 cancel 必须是可用的 Event（原落盘剔除了它）。"""
    _write_jobs(job_dir, {"j4": _job("running")})
    ocr_mod._restore_jobs_from_disk()
    assert isinstance(ocr_mod.OCR_JOBS["j4"]["cancel"], threading.Event)


def test_restore_persists_normalized_state(job_dir) -> None:
    """归一结果即刻落盘（下次启动不再重复判为运行中）。"""
    _write_jobs(job_dir, {"j5": _job("running")})
    ocr_mod._restore_jobs_from_disk()
    on_disk = json.loads((job_dir / "jobs.json").read_text(encoding="utf-8"))
    assert on_disk["j5"]["state"] == "interrupted"


def test_corrupt_jobs_file_does_not_raise(job_dir) -> None:
    """损坏的 jobs.json 不得阻断启动。"""
    (job_dir / "jobs.json").write_text("{ not json", encoding="utf-8")
    ocr_mod._restore_jobs_from_disk()   # 不抛
    assert ocr_mod.OCR_JOBS == {}


# ------------------------------------------------------------------ 前端守卫
def test_frontend_interrupted_is_terminal() -> None:
    """前端终态集合必须含 interrupted（否则轮询永真、UI 卡住）。"""
    src = _JS_SRC.read_text(encoding="utf-8")
    assert "interrupted" in src
    # 终态集合单源常量存在，且包含 interrupted
    assert "OCR_TERMINAL" in src
    line = next((ln for ln in src.splitlines() if "const OCR_TERMINAL" in ln), "")
    for st in ("done", "failed", "cancelled", "interrupted"):
        assert st in line, f"OCR_TERMINAL 缺 {st}：{line}"
    # 三处判断都必须走常量，不得再有硬编码数组
    assert src.count('["done", "failed", "cancelled"]') == 0, "仍有硬编码终态数组（应走 OCR_TERMINAL）"


def test_frontend_has_interrupted_chip() -> None:
    """前端需为 interrupted 提供芯片文案（否则落回 queued 显示「排队中」）。"""
    src = _JS_SRC.read_text(encoding="utf-8")
    line = next((ln for ln in src.splitlines() if "interrupted:" in ln), "")
    assert "已中断" in line, f"interrupted 芯片文案缺失：{line}"
