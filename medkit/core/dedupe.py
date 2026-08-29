"""R3-21：同步 LLM 接口的进程内「在飞去重」+ 每科目并发上限。

连点/双标签重复生成会重复调用 LLM 并双倍扣费——前端按钮禁用挡不住并发端口；
这里给出进程内（单实例）防线：
- begin(key)/end(key)：同 key 请求在飞期间第二次 begin 返回 True（调用方回 409）；
  请求结束（成功或失败）即 end，之后的新请求不受影响（比时间窗更不易误伤）；
- try_acquire(subject, n)：per-subject 信号量（trial 等重接口限并发，满则回 429）。
"""

import threading

_GUARD = threading.Lock()
_ACTIVE: set[str] = set()
_SEMS: dict[str, threading.BoundedSemaphore] = {}


def begin(key: str) -> bool:
    """登记在飞请求；同 key 已有在飞 → 返回 True（重复提交）。"""
    with _GUARD:
        if key in _ACTIVE:
            return True
        _ACTIVE.add(key)
        return False


def end(key: str) -> None:
    with _GUARD:
        _ACTIVE.discard(key)


def try_acquire(subject: str, n: int = 2) -> bool:
    """per-subject 并发上限（非阻塞；调用方负责在 finally 中 release）。"""
    with _GUARD:
        sem = _SEMS.get(subject)
        if sem is None:
            sem = _SEMS[subject] = threading.BoundedSemaphore(max(1, n))
        return sem.acquire(blocking=False)


def release(subject: str) -> None:
    with _GUARD:
        sem = _SEMS.get(subject)
    if sem is not None:
        sem.release()