"""错点变式训练（P1 · 2026-10-29）。

## 它解决什么

错题助手能解释「为什么错」，但解释完就结束。本模块把「一次错题」变成
「一组同考点、不同问法的变式题」，答完立即判分，失败当天回流复习。

## 三条硬约定

1. **选点复用本地口径**：`library.compute_priority` / `vault_index.stats` 的
   `priority≥0.3`、`gap.KP_QUESTIONS_CAP=3`——不另起一套排序。
2. **答案不下发**：生成后只返回题干与选项；判分后才返回答案/解析/逐选项理由。
3. **文件是真身**：变式结果写回原错题的 front matter（`variant_pass` / `variant_fail`
   / `variant_last`），不新建错题文件、不污染用户题库。变式集本身是可弃 JSON。

## 为什么变式通过不等于「已掌握」

变式可能蒙对，也可能只是记住了这次选项。掌握仍由原题的 FSRS 复习
「连续记住两次」决定；变式只作为**迁移证据**记录。
"""

from __future__ import annotations

import json
import math
import re
import secrets
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import config as cfg
from . import fsutil, vault, vault_index
from .bank import answers_equal

VARIANTS_DIR = cfg.CONFIG_DIR / "variants"
MAX_VARIANTS = 5
DEFAULT_VARIANTS = 3
PRIORITY_MIN = 0.3          # 与 gap.plan 同口径：低于它不练
SET_ID_RE = re.compile(r"^var-\d{14}-[0-9a-f]{6}$")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _safe_id(set_id: str) -> str:
    s = str(set_id or "").strip()
    if not SET_ID_RE.match(s):
        raise ValueError("非法的变式集 id")
    return s


def _set_path(set_id: str) -> Path:
    return VARIANTS_DIR / f"{_safe_id(set_id)}.json"


# ------------------------------------------------------------------ 选点
def _row(path: str, rec: dict[str, Any], priority: float) -> dict[str, Any]:
    return {
        "path": path,
        "question": str(rec.get("question") or ""),
        "subject": str(rec.get("subject") or ""),
        "chapter": str(rec.get("chapter") or ""),
        "topic": str(rec.get("topic") or ""),
        "priority": round(float(priority), 4),
        "miss_count": int(rec.get("miss_count") or 1),
        "status": str(rec.get("status") or "unlearned"),
    }


def plan(subject: str = "", limit: int = DEFAULT_VARIANTS) -> dict[str, Any]:
    """选出最该练的错点。

    先按薄弱组（科目/章节/主题）排序；组内优先未掌握的第一条。
    没有分组或分组为空时，回退到「全部未掌握错题」。
    """
    limit = max(1, min(int(limit or DEFAULT_VARIANTS), MAX_VARIANTS))
    st = vault_index.stats(subject=subject)
    groups = [g for g in (st.get("groups") or []) if int(g.get("total") or 0) > 0]
    groups.sort(key=lambda g: (-float(g.get("priority") or 0),
                               str(g.get("subject") or ""),
                               str(g.get("chapter") or ""),
                               str(g.get("topic") or "")))
    out: list[dict[str, Any]] = []
    for g in groups:
        pri = float(g.get("priority") or 0)
        if pri < PRIORITY_MIN or int(g.get("unlearned") or 0) <= 0:
            continue
        items = vault_index.list_items(
            subject=str(g.get("subject") or ""),
            chapter=str(g.get("chapter") or ""),
            topic=str(g.get("topic") or ""),
        )
        cand = [it for it in items if str(it.get("status") or "") != "learned"]
        if not cand:
            continue
        out.append(_row(str(cand[0].get("path") or ""), cand[0], pri))
        if len(out) >= limit:
            return {"count": len(out), "items": out, "reason": ""}
    if out:
        return {"count": len(out), "items": out, "reason": ""}

    # 回退：没有分组（例如 chapter/topic 全空但同一科目）时，直接取未掌握的错题。
    items = vault_index.list_items(subject=subject)
    cand = [it for it in items if str(it.get("status") or "") != "learned"]
    for it in cand[:limit]:
        out.append(_row(str(it.get("path") or ""), it, PRIORITY_MIN))
    reason = "" if out else "没有未掌握的错题。先刷题判错，或在「新建错题」里记一条。"
    return {"count": len(out), "items": out, "reason": reason}


# ------------------------------------------------------------------ 集存取
def save_set(source_path: str, source_rec: dict[str, Any],
             items: list[dict[str, Any]]) -> dict[str, Any]:
    """把生成结果落盘为可弃 JSON；返回完整集（含答案，供判分）。"""
    set_id = f"var-{datetime.now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"
    data = {
        "id": set_id,
        "source_path": str(source_path or ""),
        "source_id": str(source_rec.get("id") or ""),
        "subject": str(source_rec.get("subject") or ""),
        "chapter": str(source_rec.get("chapter") or ""),
        "topic": str(source_rec.get("topic") or ""),
        "created_at": _now(),
        "items": list(items or []),
        "attempts": [],
    }
    fsutil.write_json_atomic(_set_path(set_id), data)
    return data


def load_set(set_id: str) -> dict[str, Any]:
    path = _set_path(set_id)
    if not path.is_file():
        raise KeyError("变式集不存在（可能已清理）")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise KeyError(f"变式集读取失败：{e}") from e
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise KeyError("变式集内容损坏")
    return data


def _public_item(item: dict[str, Any], idx: int) -> dict[str, Any]:
    """判分前下发：**不含答案/解析/理由**（防剧透，与题库练习同口径）。"""
    return {
        "index": idx,
        "question": str(item.get("question") or ""),
        "options": [str(o) for o in (item.get("options") or [])],
        "type": str(item.get("type") or "A1"),
        "difficulty": int(item.get("difficulty") or 0),
        "variant_type": str(item.get("variant_type") or "stem"),
    }


def public_set(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "set_id": str(data.get("id") or ""),
        "source_path": str(data.get("source_path") or ""),
        "subject": str(data.get("subject") or ""),
        "chapter": str(data.get("chapter") or ""),
        "topic": str(data.get("topic") or ""),
        "created_at": str(data.get("created_at") or ""),
        "items": [_public_item(it, i) for i, it in enumerate(data.get("items") or [])],
    }


# ------------------------------------------------------------------ 判分与回流
def _pass_threshold(total: int) -> int:
    """2/3 通过。3 题→2；2 题→2；1 题→1。"""
    return max(1, math.ceil(int(total) * 2 / 3)) if total else 1


def _update_source(source_path: str, passed: bool) -> dict[str, Any]:
    """写回原错题的 front matter。失败不抛硬错（判分结果仍要返回给用户）。"""
    root = vault.vault_root()
    path = root / str(source_path or "")
    if not path.is_file():
        return {"ok": False, "reason": "原错题文件不存在（可能已删）"}
    try:
        fm, sec = vault.parse(path.read_text(encoding="utf-8", errors="replace"))
        rec = vault.to_record(fm, sec)
        now = _now()
        if passed:
            rec["variant_pass"] = int(rec.get("variant_pass") or 0) + 1
        else:
            rec["variant_fail"] = int(rec.get("variant_fail") or 0) + 1
            rec["miss_count"] = int(rec.get("miss_count") or 1) + 1
            rec["status"] = "unlearned"
            rec["due"] = _today()
        rec["variant_last"] = now
        rec["last_tried"] = now
        vault.write_record_at(path, rec)
        vault_index.reindex_file(path, root)
        return {"ok": True, "passed": passed,
                "variant_pass": int(rec.get("variant_pass") or 0),
                "variant_fail": int(rec.get("variant_fail") or 0)}
    except (OSError, ValueError) as e:
        return {"ok": False, "reason": f"写回失败：{e}"}


def grade(set_id: str, answers: dict[str, Any]) -> dict[str, Any]:
    """判分：集合语义比较（与 `bank.answers_equal` 同源），写回原错题。"""
    data = load_set(set_id)
    items = data.get("items") or []
    if not items:
        raise ValueError("变式集为空")
    rows: list[dict[str, Any]] = []
    correct_n = 0
    ans_map = {str(k): v for k, v in (answers or {}).items()}
    for i, it in enumerate(items):
        user = str(ans_map.get(str(i), "") or "")
        ok = answers_equal(user, it.get("answer"))
        correct_n += 1 if ok else 0
        rows.append({
            "index": i,
            "question": str(it.get("question") or ""),
            "options": [str(o) for o in (it.get("options") or [])],
            "user_answer": user,
            "answer": str(it.get("answer") or ""),
            "correct": ok,
            "analysis": str(it.get("analysis") or ""),
            "option_rationales": dict(it.get("option_rationales") or {}),
            "misconceptions": dict(it.get("misconceptions") or {}),
            "source_quote": str(it.get("source_quote") or ""),
            "difficulty": int(it.get("difficulty") or 0),
        })
    total = len(items)
    passed = correct_n >= _pass_threshold(total)
    attempt = {"at": _now(), "answers": {str(i): str((answers or {}).get(str(i), "")) for i in range(total)},
               "correct": correct_n, "total": total, "passed": passed}
    data.setdefault("attempts", []).append(attempt)
    data["attempts"] = data["attempts"][-20:]
    fsutil.write_json_atomic(_set_path(set_id), data)
    source = _update_source(str(data.get("source_path") or ""), passed)
    return {
        "set_id": str(data.get("id") or ""),
        "correct": correct_n,
        "total": total,
        "passed": passed,
        "threshold": _pass_threshold(total),
        "items": rows,
        "source": source,
        "attempts": len(data["attempts"]),
    }
