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

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_MAX_RECENT = 50
_MAX_MSG = 300
_MAX_DISK_LINES = 500   # W7：磁盘错误清单保留上限（超出自动裁剪，防无限增长）
_DISK_LOCK = threading.Lock()

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
_AUTH_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key)\b\s*[:=]\s*(?:Bearer\s+)?\S+")

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
    """脱敏 + 截断：掩码已登记密钥、`sk-***`、`mr-***`/JWT，以及 `Authorization/api_key: ***`。

    `limit <= 0` ⇒ **不截断**（返回完整脱敏文本）。日志链路（`logging_setup._scrub`）
    依赖此语义取得完整上下文；若按 `t[:0]` 处理会把整条日志清空（2026-10-02 实测踩坑）。
    """
    t = str(text)
    for s in _SECRETS:
        if s in t:
            t = t.replace(s, "***")
    t = _KEY_RE.sub("sk-***", t)
    t = _EXTRA_KEY_RE.sub("***", t)
    t = _AUTH_RE.sub(r"\1: ***", t)
    return t if limit <= 0 else t[:limit]


def record(code: str, msg: str, **ctx: Any) -> None:
    """记录一次错误（留痕 + 计数 + **落盘**）。`code` 为稳定标识（如 `LLM_ERROR` / `GATE1`）。"""
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
    _append_disk(entry)   # W7：跨重启可回查（内存 RECENT 重启即清空）
    _logger.warning("[%s] %s %s", code, entry["msg"], entry.get("ctx", ""))


# ---------------------------------------------------------------- W7：持久错误清单
# 背景（2026-10-02 二轮审计）：RECENT 是进程内缓冲，重启即清空——用户遇到「出问题→重启」
# 后无法回查「上次为何失败」（除非手工翻项目目录）。此处追加 JSONL 落盘，snapshot 合并读取。
def _errors_log_path() -> Path | None:
    """错误清单落盘路径（默认 `~/.medkit/logs/errors.jsonl`）。可用 env 覆盖（测试隔离）。

    优先读 `MEDKIT_LOG_DIR`（与 logging_setup 同口径，测试用同一 env 隔离），
    回落 `cfg.CONFIG_DIR/logs`。

    **返回 `None` = 本次不落盘**（改为只留内存清单）。两种情况：
      - `MEDKIT_ERRORS_LOG=""` / `MEDKIT_LOG_DIR=""`（显式空串 ⇒ 调用方明确要求关闭落盘）；
      - `MEDKIT_NO_DISK=1`（整仓级开关，测试用）。
    留 None 分支的原因（2026-10-02 实测，R5-01 哨兵第三次触发）：
    `main._open` 是**守护线程**，它可能在 conftest 的 `monkeypatch.setenv`
    已被 function-teardown 撤销**之后**才跑到 `errs.record`。此时 env 已空 ⇒
    回落真实 `~/.medkit/logs/` ⇒ 哨兵报「测试套件触碰了真实家目录」。
    更本质地说：**一次探活超时不该替用户在家目录里凭空建 `logs/errors.jsonl`**
    ——这只是个瞬时探测失败，用户重启后回查它也没有意义（真正的失败会有别的留痕途径）。
    """
    override = os.environ.get("MEDKIT_ERRORS_LOG")
    if override is not None:            # 显式设置（含空串 ⇒ 关闭）
        return Path(override) if override else None
    if os.environ.get("MEDKIT_NO_DISK") == "1":
        return None
    log_dir = os.environ.get("MEDKIT_LOG_DIR")
    if log_dir is not None:             # 显式设置（含空串 ⇒ 关闭）
        return (Path(log_dir) / "errors.jsonl") if log_dir else None
    try:
        from .config import CONFIG_DIR  # 单一来源（~/.medkit）
        return Path(CONFIG_DIR) / "logs" / "errors.jsonl"
    except Exception:  # noqa: BLE001  配置不可用时不阻塞记录
        return None


def _append_disk(entry: dict[str, Any]) -> None:
    """追加一条到磁盘 JSONL（失败**留痕**但不抛——留痕功能自身不应拖垮调用方）。"""
    path = _errors_log_path()
    if path is None:                    # 落盘被显式关闭（见 `_errors_log_path` 说明）
        return
    try:
        with _DISK_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim_disk(path)
    except Exception as e:  # noqa: BLE001  落盘失败不阻断（内存清单仍可用），但必须留痕
        # 注意：此处**不能**递归调 `record()`（会再触发落盘 → 栈溢出）。
        # 只写 logger（不落盘），保证「静默吞掉」被审计用例识别为「已留痕」。
        _logger.warning("errors._append_disk 落盘失败（内存清单仍可用）：%s", e)


def _trim_disk(path: Path) -> None:
    """超出上限时保留最后 _MAX_DISK_LINES 行（在锁内调用；裁剪失败留痕不抛）。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > _MAX_DISK_LINES:
            path.write_text("\n".join(lines[-_MAX_DISK_LINES:]) + "\n", encoding="utf-8")
    except Exception as e:  # noqa: BLE001  裁剪失败不阻断写入，但必须留痕
        _logger.warning("errors._trim_disk 裁剪失败：%s", e)


def read_disk(limit: int = 50) -> list[dict[str, Any]]:
    """读磁盘错误清单（最后 N 条，按时间正序）。文件缺失/损坏/落盘关闭 → 空列表。"""
    path = _errors_log_path()
    if path is None:                    # 落盘被显式关闭 ⇒ 无磁盘清单可读
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for ln in lines[-max(1, int(limit or 50)):]:
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001  单行损坏跳过
            continue
    return out


def snapshot(limit: int = 50, *, include_disk: bool = True) -> dict[str, Any]:
    """诊断快照：计数 + 最近 N 条（供 `/api/diagnostics/errors`）。

    `include_disk=True`（默认）时合并**磁盘历史**——重启后仍能回查上次的错误。
    合并判据：内存清单已有则不重复（按 `t`+`code`+`msg` 去重），磁盘独有项排在前。
    """
    n = max(1, min(int(limit or 50), _MAX_RECENT))
    with _LOCK:
        mem = list(RECENT[-n:])
        counts = dict(COUNTS)
        total = int(sum(COUNTS.values()))
    if not include_disk:
        return {"counts": counts, "total": total, "recent": mem, "disk_only": []}

    seen = {(e.get("t"), e.get("code"), e.get("msg")) for e in mem}
    disk = read_disk(limit=n)
    disk_only = [e for e in disk if (e.get("t"), e.get("code"), e.get("msg")) not in seen]
    # 历史在前，内存（本次会话）在后——用户重启后先看到的是上次的失败
    merged = (disk_only + mem)[-n:]
    return {"counts": counts, "total": total, "recent": merged, "disk_only": disk_only[-n:]}


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
