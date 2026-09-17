"""core：异常留痕与可观测面（U-15）。

**背景**：`medkit/` 内 `except Exception` 121 处，其中 31 处紧跟 `pass`（静默吞掉）——
用户表现为「按钮点了没反应」；全局兜底只写日志，**无错误计数、无用户可查清单**
（日志在 `~/.medkit/logs/medkit.log`，普通用户不会去看）。

**本模块**提供进程内「记录 + 计数 + 最近清单」的统一入口，并配只读诊断端点
`GET /api/diagnostics/errors`（见 `routers/diagnostics.py`）。

设计约束：
- 纯 stdlib，不导入 routers（维持分层单向）；
- `redact()` 对外回显与写盘前统一脱敏（掩码 API Key / Authorization，限制长度）；
- 线程安全（单进程多线程：uvicorn worker + 线程池）。
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from typing import Any, Iterator

_MAX_RECENT = 50
_MAX_MSG = 300

_LOCK = threading.Lock()
COUNTS: Counter[str] = Counter()
RECENT: list[dict[str, Any]] = []
_logger = logging.getLogger("medkit.errors")

# R6-20：日志/回显前统一脱敏（Key 一旦进入异常串即被写盘/回显，此处为主动防线）
_KEY_RE = re.compile(r"sk-[A-Za-z0-9_\-]{6,}")
# SEC-REDACT ③（R8+W）：原正则只遮 `sk-` 前缀——MinerU 的 `mr-xxx`、JWT `eyJ….….…`
# 这类非 sk- 形态的裸值会原样落进 run.log 与 /api/diagnostics/errors。
_EXTRA_KEY_RE = re.compile(
    r"\bmr-[A-Za-z0-9_\-]{6,}"
    r"|\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}")
_AUTH_RE = re.compile(r"(?i)\b(authorization|api[_-]?key)\b\s*[:=]\s*\S+")

# 已登记的「本机实际密钥明文」（由 config.resolve_key 登记）。
# 正则只能覆盖已知前缀形态；智谱 `xxx.yyyy`、自建网关自定义 token 等抓不住，
# 登记实际值才能精确掩码（SEC-REDACT ③ 的「动态掩码」部分）。
_SECRETS: set[str] = set()


def register_secret(value: Any) -> None:
    """登记一个实际密钥明文，供 `redact` 一并掩码（长度 <8 的短串忽略，避免误伤正常文本）。"""
    v = str(value or "").strip()
    if len(v) >= 8:
        _SECRETS.add(v)


def redact(text: Any, limit: int = _MAX_MSG) -> str:
    """脱敏 + 截断：掩码已登记密钥、`sk-***`、`mr-***`/JWT，以及 `Authorization/api_key: ***`。"""
    t = str(text)
    for s in _SECRETS:
        if s in t:
            t = t.replace(s, "***")
    t = _KEY_RE.sub("sk-***", t)
    t = _EXTRA_KEY_RE.sub("***", t)
    t = _AUTH_RE.sub(r"\1: ***", t)
    return t[:limit]


def record(code: str, msg: str, **ctx: Any) -> None:
    """记录一次错误（留痕 + 计数）。`code` 为稳定标识（如 `LLM_ERROR` / `GATE1`）。"""
    code = str(code or "UNKNOWN")
    entry: dict[str, Any] = {
        "t": time.strftime("%Y-%m-%d %H:%M:%S"),
        "code": code,
        "msg": redact(msg),
    }
    if ctx:
        entry["ctx"] = {k: redact(v, 120) for k, v in ctx.items()}
    with _LOCK:
        COUNTS[code] += 1
        RECENT.append(entry)
        if len(RECENT) > _MAX_RECENT:
            del RECENT[:len(RECENT) - _MAX_RECENT]
    _logger.warning("[%s] %s %s", code, entry["msg"], entry.get("ctx", ""))


def snapshot(limit: int = 50) -> dict[str, Any]:
    """诊断快照：计数 + 最近 N 条（只读，供 `/api/diagnostics/errors`）。"""
    n = max(1, min(int(limit or 50), _MAX_RECENT))
    with _LOCK:
        return {"counts": dict(COUNTS), "total": int(sum(COUNTS.values())),
                "recent": list(RECENT[-n:])}


def reset() -> None:
    """清空计数与清单（测试用）。"""
    with _LOCK:
        COUNTS.clear()
        RECENT.clear()


@contextmanager
def swallow(code: str, msg: str, **ctx: Any) -> Iterator[None]:
    """有意容错但必须留痕（替代裸 `except Exception: pass`）。

    用法：`with errors.swallow("LOG_PROJECT", "写 run.log 失败"): ...`
    """
    try:
        yield
    except Exception as e:  # noqa: BLE001  有意容错（本地工具不因单点失败中断），但必须留痕
        record(code, f"{msg}：{e}", **ctx)
