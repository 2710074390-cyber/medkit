"""文件系统工具：原子写 JSON（唯一临时名 + Windows 共享冲突重试）。

v0.5（2026-08 审计）：原 main.py 固定 tmp 名（meta.json.tmp）无重试，并发写会互相覆盖；
统一收敛为 orchestrator 的「唯一 tmp 名 + 重试」实现。
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, data: Any) -> None:
    """原子写 JSON：唯一临时名 + Windows 共享冲突重试（并发写进度文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for attempt in range(6):
            try:
                tmp.replace(path)
                return
            except OSError:
                if attempt >= 5:
                    raise
                time.sleep(0.05 + attempt * 0.03)
    finally:
        tmp.unlink(missing_ok=True)
