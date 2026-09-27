"""测试全局隔离（S0）：学习库 SQL 底座默认指向临时目录，防止测试触碰真实 ~/.medkit。

背景：medkit.db 建立（SQL 模式）后，若测试只 monkeypatch 各模块的文件常量而
未隔离 `DB_FILE`，`_store_is_sql()` 会判定 SQL 模式并写真实用户库（本会话曾踩坑：
一次失败回归把真实库从 1/4/1/1 条污染到 16/11/3/6 条，已用 `.pre-db-import-*.bak` 恢复）。
本 autouse fixture 统一把 db 底座与四域模块全部重定向到本测试的 tmp_path：
DB 不存在 → 各模块自动回落 JSON 路径（既有 174+ 测试行为零变化）。

R5-01（2026-09-01）：此前本 fixture 只隔离 `DB_PATH`/`DB_FILE`，未隔离各模块的 **JSON
文件常量**（`lib.MISTAKES_FILE`/`KNOWLEDGE_FILE`、`rev.REVIEW_QUEUE_FILE`、
`expl.EXPLAINS_FILE`/`SLICE_INDEX_FILE`、`tut.TUTOR_SESSIONS_FILE`、
`cards.CARDS_FILE`）——tmp db 不存在 → 各模块回落 JSON 路径 → 直接原子写真实
`~/.medkit/library/*.json`（R5 实机两轮复现：mistakes.json 4→8 条、knowledge.json 被整体
替换）。现把全部文件常量一并重定向到同一 store_dir，并加 session 级「家目录哈希哨兵」
（`_sentinel_no_home_touch`，套件结束断言真实 ~/.medkit 未被测试触碰）。
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

import pytest

from medkit.core import cards as cards_mod
from medkit.core import config as cfg
from medkit.core import db as dbs
from medkit.core import explain as expl
from medkit.core import library as lib
from medkit.core import review as rev
from medkit.core import tutor as tut

# R5-01：模块级「文件常量 → 相对库内文件名」清单（与各 core 模块定义保持一致）。
# 只 patch 文件常量，不 patch CONFIG_DIR 本身（config 相关测试各自隔离，行为零变化）。
_FILE_CONSTANTS: list[tuple[Any, str, str]] = [
    (lib, "MISTAKES_FILE", "mistakes.json"),
    (lib, "KNOWLEDGE_FILE", "knowledge.json"),
    (rev, "REVIEW_QUEUE_FILE", "review_queue.json"),
    (expl, "EXPLAINS_FILE", "explains.json"),
    (expl, "SLICE_INDEX_FILE", "slice_index.json"),
    (tut, "TUTOR_SESSIONS_FILE", "tutor_sessions.json"),
    (cards_mod, "CARDS_FILE", "memory_cards.json"),
]


@pytest.fixture(autouse=True)
def _isolate_medkit_store(tmp_path, monkeypatch):
    store_dir = tmp_path / "library"
    db_file = store_dir / "medkit.db"
    monkeypatch.setattr(dbs, "LIBRARY_DIR", store_dir)
    monkeypatch.setattr(dbs, "DB_PATH", db_file)
    for mod in (lib, rev, expl, tut):
        monkeypatch.setattr(mod, "DB_FILE", db_file)
    # R5-01：JSON 文件常量同样重定向（此前缺失 → 测试回落 JSON 时写真实 ~/.medkit）
    for mod, attr, fname in _FILE_CONSTANTS:
        monkeypatch.setattr(mod, attr, store_dir / fname)
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path)  # 兜底：派生路径（ocr/presets）不落真实家目录
    monkeypatch.setattr(cfg, "CONFIG_FILE", tmp_path / "config.json")
    # R5-01 补（2026-09-27 定位到哨兵触发真因）：**日志**同样重定向。
    # `main._lifespan` 裸调 `setup_logging()`，该函数第一优先级读 `MEDKIT_LOG_DIR`，
    # 回落 `cfg.CONFIG_DIR/logs`。只 patch `CONFIG_DIR` 不够稳，根因有两条：
    #   ① `setup_logging()` 的幂等判据是「根 logger 上是否已有 `_medkit` handler」，
    #      这是**进程级全局**状态 —— 第一个进入 lifespan 的用例装好 handler 后，
    #      后续用例（哪怕已 patch 了 CONFIG_DIR）的日志全写进第一个路径；
    #   ② 一旦那个「第一个」是真实 CONFIG_DIR，整轮日志就落进用户真实
    #      `~/.medkit/logs/medkit.log`，套件结束哨兵按 sha1 比对必然报差异。
    # 实测（干净子进程）：`with TestClient(app)` 会打印
    #   「MedKit 日志已初始化：C:\Users\38063\.medkit\logs/medkit.log」→ 真实日志被写。
    # 修法：钉住环境变量（第一优先级）+ 用例前后摘掉已存在的 `_medkit` handler，
    # 保证每条用例的日志只写本用例的 tmp_path。
    monkeypatch.setenv("MEDKIT_LOG_DIR", str(tmp_path / "logs"))
    import logging as _logging

    _root = _logging.getLogger()
    for _h in [h for h in _root.handlers if getattr(h, "_medkit", False)]:
        _root.removeHandler(_h)
        try:
            _h.close()
        except Exception:  # noqa: BLE001  关闭失败不该影响用例
            pass
    dbs.reset_conn()
    yield store_dir
    # 收尾：摘掉本用例期间新装的 handler（含 lifespan 装的），避免泄漏到下一用例
    for _h in [h for h in _root.handlers if getattr(h, "_medkit", False)]:
        _root.removeHandler(_h)
        try:
            _h.close()
        except Exception:  # noqa: BLE001  关闭失败不该影响用例
            pass


# ---------------------------------------------------------------- R5-01 防污染哨兵
def _snapshot_home_medkit() -> dict[str, str]:
    """递归快照真实 ~/.medkit 全部文件：路径 → sha1（含缺失标记）。"""
    root = Path(os.path.expanduser("~")) / ".medkit"
    snap: dict[str, str] = {}
    if not root.exists():
        return {"<absent>": "-"}
    for p in sorted(r for r in root.rglob("*") if r.is_file()):
        try:
            digest = hashlib.sha1(p.read_bytes()).hexdigest()
        except OSError:
            digest = "<unreadable>"
        snap[str(p.relative_to(root))] = digest
    return snap


@pytest.fixture(scope="session", autouse=True)
def _sentinel_no_home_touch():
    """R5-01：套件开始/结束各快照一次真实 ~/.medkit，结束断言完全一致。

    「tests must never touch user home」——即使某测试绕过本文件顶部的常量重定向，
    哨兵也会在套件结束时失败并给出具体变更文件清单（而非静默污染用户数据）。
    注意：browser 子进程测试（server_launcher）使用隔离 home，不触碰真实 ~/.medkit。
    """
    before = _snapshot_home_medkit()
    yield
    after = _snapshot_home_medkit()
    changed = {k: (before.get(k), after.get(k)) for k in set(before) | set(after)
               if before.get(k) != after.get(k)}
    assert not changed, (
        "测试套件触碰了真实 ~/.medkit（R5-01 防污染哨兵触发）：\n"
        + "\n".join(f"  {k}: {old} → {new}" for k, (old, new) in
                    sorted(changed.items()))
    )


# ---------------------------------------------------------------- R6-01 协程驱动
def _run_coro_in_thread(coro, timeout: float = 30.0):
    """在新线程中 `asyncio.run(coro)`：返回结果，或原样抛出协程内的异常。

    为什么不能直接 `asyncio.run()`（R6-01，2026-09-15）：
    `tests/browser/conftest.py` 的 session 级 `browser` fixture 让 Playwright 同步 API
    （`sync_playwright()`）在整个会话期间保持打开——它在 greenlet 中运行自己的事件循环，
    会把当前线程的 asyncio running-loop 标记置位。此后同进程内任何 `asyncio.run()`
    都会抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`。
    实测：`pytest -q`（browser 与单测同进程收集）必现 3 例失败，而 `--ignore=tests/browser`
    时全绿。在新线程里跑即可完全免疫主线程的事件循环状态。
    """
    import asyncio
    import threading

    box: dict[str, object] = {}

    def _target() -> None:
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as exc:  # 原样回抛（含断言失败），不吞异常
            box["error"] = exc

    thread = threading.Thread(target=_target, name="run-coro")
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise TimeoutError(f"协程在 {timeout}s 内未结束")
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


@pytest.fixture()
def run_coro():
    """驱动协程的测试工具（返回 `_run_coro_in_thread`）。用法：`run_coro(drive())`。

    需要「跑一个 async 函数并断言其结果」的用例一律走本 fixture，不要直接 `asyncio.run()`。
    """
    return _run_coro_in_thread
