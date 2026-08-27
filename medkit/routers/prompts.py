"""routers：提示词（只读查看 / 影子副本编辑 / 漂移检测 / 恢复默认）。"""

import hashlib
import json
import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents import PROMPTS_DIR
from ..core import config as cfg

router = APIRouter()

PROMPT_ROLES = {"medgen.md": "MedGen · 出题", "medqc.md": "MedQC · 质检",
                "medfix.md": "MedFix · 修复", "medreview.md": "MedReview · 复习手册",
                "medtutor.md": "MedTutor · 苏格拉底提问", "medexplain.md": "MedExplain · 教材讲解"}


def _prompt_meta() -> dict[str, Any]:
    p = cfg.PROMPTS_DIR_USER / ".meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_prompt_meta(meta: dict[str, Any]) -> None:
    cfg.PROMPTS_DIR_USER.mkdir(parents=True, exist_ok=True)
    (cfg.PROMPTS_DIR_USER / ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _builtin_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{[a-z_]+\}", text)))


@router.get("/api/prompts")
def prompts() -> dict[str, Any]:
    from ..agents import load_prompt
    meta = _prompt_meta()
    out = []
    for name, role in PROMPT_ROLES.items():
        builtin = _builtin_prompt(name)
        user_path = cfg.PROMPTS_DIR_USER / name
        custom = user_path.read_text(encoding="utf-8") if user_path.exists() else None
        base_hash = hashlib.sha256(builtin.encode("utf-8")).hexdigest()[:16]
        drifted = bool(custom and meta.get(name, {}).get("base_hash", "") != base_hash)
        out.append({
            "name": name, "role": role,
            "builtin": builtin, "custom": custom,
            "using": "custom" if custom else "builtin",
            "drifted": drifted,
            "placeholders": _placeholders(builtin),
            "content": load_prompt(name),  # 实际生效版（影子优先）
        })
    return {"prompts": out}


class PromptBody(BaseModel):
    content: str


@router.put("/api/prompts/{name}")
def put_prompt(name: str, body: PromptBody) -> dict[str, Any]:
    if name not in PROMPT_ROLES:
        raise HTTPException(404, "未知提示词")
    builtin = _builtin_prompt(name)
    required = _placeholders(builtin)
    missing = [p for p in required if p not in body.content]
    if missing:
        raise HTTPException(400, "缺少必需占位符：" + "、".join(missing)
                            + "（占位符会被运行时替换，删除将导致管线失败）")
    cfg.PROMPTS_DIR_USER.mkdir(parents=True, exist_ok=True)
    (cfg.PROMPTS_DIR_USER / name).write_text(body.content, encoding="utf-8")
    meta = _prompt_meta()
    meta[name] = {"base_hash": hashlib.sha256(builtin.encode("utf-8")).hexdigest()[:16]}
    _save_prompt_meta(meta)
    return {"ok": True, "using": "custom"}


@router.delete("/api/prompts/{name}")
def delete_prompt(name: str) -> dict[str, Any]:
    if name not in PROMPT_ROLES:
        raise HTTPException(404, "未知提示词")
    (cfg.PROMPTS_DIR_USER / name).unlink(missing_ok=True)
    return {"ok": True, "using": "builtin"}
