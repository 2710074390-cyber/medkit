"""lifespan 关闭路径测试（2026-09-20 审查 P3 补）：

覆盖 `main._shutdown_runtime()` 与 `db.shutdown()`：
- 在飞管线线程：置取消信号（停止烧 token）+ CANCELLING 标记 + 有限宽限 join；
- 在飞 OCR 线程：置 cancel Event + join；从磁盘恢复、无 `_thread` 的历史任务不导致关闭异常；
- 空状态/重复调用幂等；
- SQLite 主线程连接做被动检查点后关闭，关闭后可重新打开；
- TestClient 走完整 lifespan 时，退出上下文确实触发上述取消。
"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

import medkit.main as m
from medkit.core import db as dbs
from medkit.state import CANCELLING, OCR_JOBS, OCR_LOCK, RUN_LOCK, RUN_THREADS, RUNNING

_PID = "__shutdown_unit_test__"
_JID = "__shutdown_ocr_test__"
_JID_OLD = "__shutdown_ocr_restored__"


def _start_waiter(event: threading.Event) -> threading.Thread:
    t = threading.Thread(target=lambda: event.wait(timeout=5),
                         name="test-shutdown-waiter")
    t.start()
    return t


def _cleanup_state() -> None:
    with RUN_LOCK:
        RUNNING.pop(_PID, None)
        RUN_THREADS.pop(_PID, None)
        CANCELLING.pop(_PID, None)
    with OCR_LOCK:
        OCR_JOBS.pop(_JID, None)
        OCR_JOBS.pop(_JID_OLD, None)


def test_shutdown_cancels_inflight_pipeline_and_joins():
    _cleanup_state()
    ev = threading.Event()
    t = _start_waiter(ev)
    try:
        with RUN_LOCK:
            RUNNING[_PID] = ev
            RUN_THREADS[_PID] = t
        assert not ev.is_set()
        m._shutdown_runtime(join_timeout=5)
        assert ev.is_set(), "关闭时应给在飞管线发取消信号"
        assert CANCELLING.get(_PID) is True
        assert not t.is_alive(), "应在宽限内 join 到已取消的管线线程"
    finally:
        _cleanup_state()


def test_shutdown_cancels_inflight_ocr_and_tolerates_restored_jobs():
    _cleanup_state()
    ev = threading.Event()
    t = _start_waiter(ev)
    try:
        with OCR_LOCK:
            OCR_JOBS[_JID] = {"id": _JID, "state": "running", "cancel": ev, "_thread": t}
            # 模拟从 jobs.json 恢复的历史任务：有 cancel Event 但无 _thread（进程内并无在飞线程）
            OCR_JOBS[_JID_OLD] = {"id": _JID_OLD, "state": "queued",
                                  "cancel": threading.Event()}
        m._shutdown_runtime(join_timeout=5)
        assert ev.is_set(), "关闭时应取消在飞 OCR"
        assert OCR_JOBS[_JID_OLD]["cancel"].is_set()
        assert not t.is_alive()
    finally:
        _cleanup_state()


def test_shutdown_is_safe_when_idle_and_idempotent():
    _cleanup_state()
    m._shutdown_runtime(join_timeout=1)
    m._shutdown_runtime(join_timeout=1)   # 重复调用不抛错


def test_db_shutdown_closes_main_thread_connection_and_is_idempotent():
    conn = dbs.get_conn()
    assert conn.execute("SELECT 1").fetchone()[0] == 1
    dbs.shutdown()
    assert getattr(dbs._local, "conn", None) is None
    dbs.shutdown()   # 幂等：无连接时直接返回
    # 关闭后再取连接应能重建（不影响后续使用）
    conn2 = dbs.get_conn()
    assert conn2.execute("SELECT 1").fetchone()[0] == 1


def test_lifespan_exit_triggers_inflight_cancellation(monkeypatch):
    """完整 lifespan 集成：TestClient 退出上下文 → 关闭路径取消在飞管线。"""
    monkeypatch.setenv("MEDKIT_NO_BROWSER", "1")
    _cleanup_state()
    ev = threading.Event()
    t = _start_waiter(ev)
    try:
        with RUN_LOCK:
            RUNNING[_PID] = ev
            RUN_THREADS[_PID] = t
        with TestClient(m.app, base_url="http://127.0.0.1"):
            assert not ev.is_set(), "启动后任务应仍在飞"
        assert ev.is_set(), "lifespan 关闭应取消在飞管线"
        assert not t.is_alive()
    finally:
        t.join(timeout=5)
        _cleanup_state()
