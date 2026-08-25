"""应用日志：~/.medkit/logs/medkit.log（RotatingFileHandler）+ 控制台。

S2（2026-08 审计补充）：此前零 logging（只有 run.log 的项目级日志）；
本模块只负责配置根 logger，管线内已埋的 logger.warning 等自动生效。
UI 实时日志（run.log 回调通道）不动。
"""

import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

from .core import config as cfg

_MAX_BYTES = 1_000_000       # 1 MB × 3 个备份
_BACKUP_COUNT = 3


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
    fh._medkit = True  # type: ignore[attr-defined]  幂等标记
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    sh._medkit = True  # type: ignore[attr-defined]

    root.addHandler(fh)
    root.addHandler(sh)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.info("MedKit 日志已初始化：%s/medkit.log", log_dir)
    return log_dir
