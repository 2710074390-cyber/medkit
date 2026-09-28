"""素材会话存储（S3）：解析结果可保存为「素材会话」，跨项目复用 / 多教材合并出题。

存储：~/.medkit/sessions/{id}.json —— {id, name, role, source_name, chars, slice_count,
created, slices: [{sid, title, text}]}。文件名即会话 id（uuid），防路径穿越。
"""

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from . import config as cfg


def _dir() -> Path:
    d = Path(cfg.CONFIG_DIR) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


# R7（2026-09-27）：与 routers/_common.py::_safe_pid 同规则的全字符白名单。
# core 不得 import routers（分层单向：routers → core → db/llm），故此处有意重复；
# 两份由 tests/test_traversal_defense.py 同时锁住一致性。
_SAFE_SID_RE = re.compile(r"[\w\u4e00-\u9fff-]+")


def _safe_sid(sid: str) -> str:
    """会话 id = uuid hex（见 save_session）。白名单放宽到同 _safe_pid 的规则，
    容忍测试/历史数据里的 `_`、`-`、中文，同时**保证语料里不会出现路径分隔符**。

    R7 加固：旧实现只做「. / .. / / / \\」黑名单，**NUL 字节可穿过**（`"a\\x00b"` 通过校验）。
    取证结论：NUL 在 Windows 上 100% 触发 `ValueError: lstat: embedded null character in path`，
    **删不掉任何文件**（实测 keepme.json 在攻击前后都存在），所以这是纯理论破口、
    不是可被利用的漏洞。补上是因为它是**对客户端输入的校验缺项**，
    且与 `_safe_pid` 的严格度不一致；白名单方案顺带消掉了整类隐患。
    """
    if sid in {"", ".", ".."} or "/" in sid or "\\" in sid:
        raise ValueError("非法会话 ID")
    if not _SAFE_SID_RE.fullmatch(sid):
        raise ValueError("非法会话 ID")
    return sid


def save_session(name: str, role: str, slices: list[dict[str, Any]],
                 source_name: str = "") -> dict[str, Any]:
    """保存一次解析结果 → 会话。slices 需含 sid/title/text。"""
    clean = [{"sid": str(s.get("sid") or f"S{i + 1:03d}"),
              "title": str(s.get("title") or ""),
              "text": str(s.get("text") or "")}
             for i, s in enumerate(slices)]
    if not clean:
        raise ValueError("会话切片为空")
    sid = uuid.uuid4().hex[:12]
    data = {"id": sid, "name": (name or "未命名素材").strip()[:60],
            "role": role or "textbook", "source_name": str(source_name or "")[:120],
            "chars": sum(len(s["text"]) for s in clean),
            "slice_count": len(clean),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "slices": clean}
    # S3-19（R8+W）：原为裸 write_text——崩溃/断电会留下半截 JSON，下次读即损坏。
    # 统一走 fsutil.write_json_atomic（唯一临时名 + 同卷 rename）。
    from .fsutil import write_json_atomic
    write_json_atomic(_dir() / f"{sid}.json", data)
    return {k: v for k, v in data.items() if k != "slices"}


def list_sessions() -> list[dict[str, Any]]:
    out = []
    # B30：按 mtime 倒序（最近优先），不再按 uuid 文件名随机排序
    for p in sorted(_dir().glob("*.json"),
                    key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"id": d.get("id", p.stem), "name": d.get("name", ""),
                        "role": d.get("role", ""), "chars": d.get("chars", 0),
                        "slice_count": d.get("slice_count", 0),
                        "created": d.get("created", ""),
                        "source_name": d.get("source_name", "")})
        except Exception:  # noqa: BLE001
            continue
    return out


def get_session(sid: str) -> dict[str, Any]:
    sid = _safe_sid(sid)
    p = _dir() / f"{sid}.json"
    if not p.exists():
        raise FileNotFoundError("素材会话不存在")
    return json.loads(p.read_text(encoding="utf-8"))


def delete_session(sid: str) -> bool:
    sid = _safe_sid(sid)
    p = _dir() / f"{sid}.json"
    if p.exists():
        p.unlink()
        return True
    return False
