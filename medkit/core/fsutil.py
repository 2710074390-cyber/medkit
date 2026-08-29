"""文件系统工具：原子写 JSON（唯一临时名 + Windows 共享冲突重试）。

v0.5（2026-08 审计）：原 main.py 固定 tmp 名（meta.json.tmp）无重试，并发写会互相覆盖；
统一收敛为 orchestrator 的「唯一 tmp 名 + 重试」实现。
"""

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

# C-12：Windows/各平台不可用于文件名的字符 → 统一替换为下划线
_UNSAFE_FS_CHARS = re.compile("[\\\\/:*?\"'<>|\\x00-\\x1f\\x7f]")


def safe_filename(s: Any) -> str:
    """把任意字符串转成安全文件名（C-12，subject 拼文件名统一入口）。

    替换 反斜杠/斜杠/冒号/星号/问号/引号/尖括号/控制字符 为下划线；
    去首尾空格与点；空结果返回「未命名」。
    """
    name = _UNSAFE_FS_CHARS.sub("_", str(s or ""))
    name = name.strip(" .")
    return name or "未命名"


def read_json_list(path: Path) -> list[Any]:
    """读 JSON 数组；缺失/损坏/非列表 → 回退空列表（统一容错，供各存储模块复用）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001  文件缺失/损坏 → 空，不阻塞记录流程
        return []


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
