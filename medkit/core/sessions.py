"""素材会话存储（S3）：解析结果可保存为「素材会话」，跨项目复用 / 多教材合并出题。

存储：~/.medkit/sessions/{id}.json —— {id, name, role, source_name, chars, slice_count,
created, slices: [{sid, title, text}]}。文件名即会话 id（uuid），防路径穿越。
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any

from . import config as cfg


def _dir() -> Path:
    d = Path(cfg.CONFIG_DIR) / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_sid(sid: str) -> str:
    if sid in {"", ".", ".."} or "/" in sid or "\\" in sid:
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
    (_dir() / f"{sid}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
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
