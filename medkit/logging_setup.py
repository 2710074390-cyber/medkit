"""应用日志：~/.medkit/logs/medkit.log（RotatingFileHandler）+ 控制台。

S2（2026-08 审计补充）：此前零 logging（只有 run.log 的项目级日志）；
本模块只负责配置根 logger，管线内已埋的 logger.warning 等自动生效。
UI 实时日志（run.log 回调通道）不动。
"""

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Optional, cast

from .core import config as cfg

_MAX_BYTES = 1_000_000       # 1 MB × 3 个备份
_BACKUP_COUNT = 3

# U-24（R6-18）：写盘/控制台前的主动脱敏——掩码 `sk-***` 与 `Authorization/api_key: ***`
_SCRUB_PATTERNS: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"sk-[A-Za-z0-9_\-]{6,}"), "sk-***"),
    (re.compile(r"(?i)(\bapi[_-]?key\b\s*[:=]\s*)\S+"), r"\1***"),
    (re.compile(r"(?i)(\bauthorization\b\s*[:=]\s*(?:Bearer\s+)?)\S+"), r"\1***"),
)


def _scrub(text: str) -> str:
    for pat, repl in _SCRUB_PATTERNS:
        text = pat.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    """日志脱敏 Filter：挂在文件/控制台 handler 上，格式化前掩码敏感值。

    背景：Logger 一旦把含 Key 的异常串写日志，Key 即随 `~/.medkit/logs/medkit.log`
    落盘/上屏。此 Filter 在**格式化之前**改写 record，作为写盘前最后一层防线；
    与 `core.errors.redact()`（进程内回显/诊断）分工互补。
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D102
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001  格式化异常不该阻断日志链路
            return True
        if any(p.search(text) for p, _ in _SCRUB_PATTERNS):
            record.msg = _scrub(text)
            record.args = ()
        return True


def setup_logging(log_dir: Optional[Path] = None) -> Path:
    """配置根 logger（幂等：已配置则跳过）。返回实际日志目录。"""
    root = logging.getLogger()
    if any(getattr(h, "_medkit", False) for h in root.handlers):
        return log_dir or Path(cfg.CONFIG_DIR) / "logs"

    if log_dir is None:
        # MEDKIT_LOG_DIR 便于便携部署重定向；默认 ~/.medkit/logs
        log_dir = Path(os.environ.get("MEDKIT_LOG_DIR") or (cfg.CONFIG_DIR / "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    fh = RotatingFileHandler(log_dir / "medkit.log", maxBytes=_MAX_BYTES,
                             backupCount=_BACKUP_COUNT, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.addFilter(RedactingFilter())
    # 幂等标记打在 handler 上（测试与二次 setup 均按此属性识别）；标准库 Handler 无此属性，
    # 经 cast(Any) 赋值，避免 type: ignore 与 ruff B010（禁止 setattr 常量属性）。
    cast(Any, fh)._medkit = True
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh.addFilter(RedactingFilter())
    cast(Any, sh)._medkit = True

    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.info("MedKit 日志已初始化：%s/medkit.log", log_dir)
    return log_dir
