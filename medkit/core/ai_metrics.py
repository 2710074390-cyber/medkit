"""AI 调用可观测（P0 · 2026-10-29）。

## 为什么本地记录而不是埋点上报

本产品的承诺是「数据不出本机」。所以指标只写 `~/.medkit/metrics/ai.jsonl`，
由用户自己在界面上看；没有远程上报、没有第三方 SDK。

## 记什么（只记能回答「这次好不好」的四件事）

| 字段 | 回答的问题 |
|---|---|
| `ms` | 这次等了多久（首字到收尾） |
| `grounded` | 有没有可验证依据（false = 被拦下拒答） |
| `cites` / `dropped` | 有效引用几条、编造引用被剔几条 |
| `stopped` / `error` | 用户主动停了几次、失败几次 |

**不记问题原文、不记答复正文**——那会把用户的学习内容写进日志。
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import config as cfg

MAX_LINES = 500          # 只保留最近 500 行；超了滚动截断（不是无限增长）
KINDS = ("ask", "handbook", "variant")


def metrics_path() -> Path:
    return cfg.CONFIG_DIR / "metrics" / "ai.jsonl"


def record(kind: str, *, ms: int, grounded: bool = False, cites: int = 0,
           dropped: int = 0, stopped: bool = False, error: str = "",
           tools: int = 0) -> dict[str, Any]:
    """追加一条记录。**失败不抛**——观测不该把主流程拖垮。"""
    rec = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind if kind in KINDS else "ask",
        "ms": max(0, int(ms)),
        "grounded": bool(grounded),
        "cites": max(0, int(cites)),
        "dropped": max(0, int(dropped)),
        "stopped": bool(stopped),
        "error": str(error or "")[:200],
        "tools": max(0, int(tools)),
    }
    path = metrics_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        _trim(path)
    except OSError:
        pass
    return rec


def _trim(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    if len(lines) <= MAX_LINES:
        return
    keep = lines[-MAX_LINES:]
    try:
        path.write_text("\n".join(keep) + "\n", encoding="utf-8")
    except OSError:
        pass


def rows(days: int = 30) -> list[dict[str, Any]]:
    """读最近 N 天的记录。损坏行跳过（不因一行脏数据丢全部）。"""
    path = metrics_path()
    if not path.is_file():
        return []
    cutoff = date.today().toordinal() - max(1, int(days))
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        at = str(rec.get("at") or "")
        try:
            if date.fromisoformat(at[:10]).toordinal() < cutoff:
                continue
        except ValueError:
            continue
        out.append(rec)
    return out


def summary(days: int = 30) -> dict[str, Any]:
    """汇总：次数 / grounded 率 / 引用剔除 / 停止 / 失败 / 平均耗时。"""
    data = rows(days)
    n = len(data)
    if not n:
        return {"days": days, "calls": 0, "grounded_rate": None, "dropped": 0,
                "stopped": 0, "errors": 0, "avg_ms": None, "p95_ms": None,
                "kinds": {}, "insufficient": True}
    ms = sorted(int(r.get("ms") or 0) for r in data)
    kinds: dict[str, int] = {}
    for r in data:
        k = str(r.get("kind") or "ask")
        kinds[k] = kinds.get(k, 0) + 1
    grounded = sum(1 for r in data if r.get("grounded"))
    return {
        "days": days,
        "calls": n,
        "grounded_rate": round(grounded / n, 3),
        "dropped": sum(int(r.get("dropped") or 0) for r in data),
        "stopped": sum(1 for r in data if r.get("stopped")),
        "errors": sum(1 for r in data if str(r.get("error") or "").strip()),
        "avg_ms": int(sum(ms) / n),
        "p95_ms": ms[min(len(ms) - 1, int(len(ms) * 0.95))],
        "kinds": kinds,
        # 样本 < 10 时不给「率」下结论（与校准曲线同一口径：样本不足就说不足）
        "insufficient": n < 10,
    }


class Timer:
    """`with Timer() as t: ...` → `t.ms`。只用于本地观测。"""

    def __init__(self) -> None:
        self._t0 = 0.0
        self.ms = 0

    def __enter__(self) -> "Timer":
        self._t0 = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.ms = int((time.monotonic() - self._t0) * 1000)


def clear() -> int:
    """清空本地指标（用户主动）。返回删除的行数。"""
    path = metrics_path()
    if not path.is_file():
        return 0
    try:
        n = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        path.unlink()
        return n
    except OSError:
        return 0


__all__ = ["KINDS", "MAX_LINES", "Timer", "clear", "metrics_path", "record", "rows", "summary"]
