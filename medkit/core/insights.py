"""题库身份与解析覆盖率（P0 · 2026-10-29）。

## 为什么需要它

2026-10-29 实测：本机 497 道题 `topic` 为空 **497/497**、`option_rationales` 覆盖 **0/497**。
没有知识点身份 ⇒ 掌握度、变式选点、自适应都只能回退到「科目」粒度；
没有覆盖率面板 ⇒ 用户不知道「哪些题能逐项讲、哪些只能整题讲」。

## 三条约定

1. **只补空值**：已有 `subtopic` 一律不改——用户手改过的题比推断更可信。
2. **来源必须可分辨**：每条回填带 `_topic_source`（`origin_ref.module` / `keyword` / `subject`），
   便于回溯「这个考点是从哪来的」。
3. **默认 dry-run**：正式写入必须显式 `dry_run=False`，避免一次点击改掉几百条题。
"""

from __future__ import annotations

import re
from typing import Any

from . import bank as banklib

# 关键词匹配下限（字符 bigram Jaccard）；与原 kc.rule_candidates 的 fuzzy 门槛同值
KEYWORD_MIN_SCORE = 0.45
_PUNCT = re.compile(r"[\s·、，,。．\.；;：:！!？?（）()\[\]【】「」『』<>/\\|_\-—–～~*#\"'`]+")


def norm_text(s: Any) -> str:
    return _PUNCT.sub("", str(s or "").lower())


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _jaccard(a: str, b: str) -> float:
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _origin_module(it: dict[str, Any]) -> str:
    o = it.get("origin_ref")
    if isinstance(o, dict):
        return str(o.get("module") or "").strip()
    return ""


def _candidates(items: list[dict[str, Any]]) -> list[str]:
    """可用的考点名池：题库里已存在的 subtopic + origin_ref.module 的唯一值。"""
    pool: list[str] = []
    seen: set[str] = set()
    for it in items:
        for raw in (it.get("subtopic"), _origin_module(it)):
            name = str(raw or "").strip()
            if name and name not in seen:
                seen.add(name)
                pool.append(name)
    return pool


def infer_topic(it: dict[str, Any], pool: list[str], subject: str = "") -> tuple[str, str]:
    """给一道题推断考点。返回 `(topic, source)`；推不出返回 `("", "")`。

    顺序：已有 subtopic → `origin_ref.module` → 题干与考点池的 bigram 匹配 → 空。
    刻意**不用题库科目顶替**：科目是「外科学」，不是考点；拿它当考点会让整库
    只有一个知识点，掌握度比空着更误导。科目只在调用方需要兜底时单独使用。
    """
    cur = str(it.get("subtopic") or "").strip()
    if cur:
        return cur, "existing"
    mod = _origin_module(it)
    if mod:
        return mod, "origin_ref.module"
    text = norm_text(it.get("question"))
    if text and pool:
        best, best_score = "", 0.0
        for cand in pool:
            score = _jaccard(text, norm_text(cand))
            if score > best_score:
                best, best_score = cand, score
        if best and best_score >= KEYWORD_MIN_SCORE:
            return best, "keyword"
    return "", ""


def backfill_topics(bank_id: str = "", dry_run: bool = True,
                    limit: int = 0) -> dict[str, Any]:
    """把空的 `subtopic` 补上。返回统计 + 前 10 条样本（供预演报告）。

    `limit > 0` 时只处理前 N 条（大库先试跑）；`dry_run=True` 只统计不写库。
    """
    banks = [bank_id] if bank_id else [str(b.get("id") or "") for b in banklib.list_banks()]
    scanned = filled = skipped = 0
    by_source: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    for bid in banks:
        if not bid:
            continue
        bank = banklib.get_bank(bid) or {}
        subject = str(bank.get("subject") or "")
        items = banklib.list_items(bid)
        pool = _candidates(items)
        for it in items:
            if limit and scanned >= limit:
                break
            scanned += 1
            if str(it.get("subtopic") or "").strip():
                skipped += 1
                continue
            topic, source = infer_topic(it, pool, subject)
            if not topic:
                skipped += 1
                continue
            filled += 1
            by_source[source] = by_source.get(source, 0) + 1
            if len(samples) < 10:
                samples.append({
                    "bank_id": bid,
                    "item_id": str(it.get("id") or ""),
                    "question": str(it.get("question") or "")[:60],
                    "topic": topic,
                    "source": source,
                })
            if not dry_run:
                banklib.update_item(str(it.get("id") or ""), {"subtopic": topic})
    return {"dry_run": bool(dry_run), "banks": len(banks), "scanned": scanned,
            "filled": filled, "skipped": skipped, "by_source": by_source,
            "samples": samples}


def coverage(bank_id: str = "") -> dict[str, Any]:
    """覆盖率面板：知识点身份 + 逐选项解析 + 原文引句 + 难度。

    口径逐字段独立统计——「有逐选项理由」不等于「有原文引句」，
    合成一个百分比会掩盖真实缺口。
    """
    banks = [bank_id] if bank_id else [str(b.get("id") or "") for b in banklib.list_banks()]
    fields = ("items", "topic", "rationales", "misconceptions", "source_quote", "difficulty")
    per_bank: list[dict[str, Any]] = []
    total: dict[str, int] = {k: 0 for k in fields}
    for bid in banks:
        if not bid:
            continue
        bank = banklib.get_bank(bid) or {}
        items = banklib.list_items(bid)
        row: dict[str, Any] = {
            "bank_id": bid, "name": str(bank.get("name") or ""),
            "subject": str(bank.get("subject") or ""), "items": len(items),
        }
        for k in fields[1:]:
            row[k] = 0
        for it in items:
            total["items"] += 1
            if str(it.get("subtopic") or "").strip():
                row["topic"] += 1
                total["topic"] += 1
            rats = it.get("option_rationales")
            if isinstance(rats, dict) and any(str(v).strip() for v in rats.values()):
                row["rationales"] += 1
                total["rationales"] += 1
            mics = it.get("misconceptions")
            if isinstance(mics, dict) and any(str(v).strip() for v in mics.values()):
                row["misconceptions"] += 1
                total["misconceptions"] += 1
            if str(it.get("source_quote") or "").strip():
                row["source_quote"] += 1
                total["source_quote"] += 1
            try:
                if int(it.get("difficulty") or 0) > 0:
                    row["difficulty"] += 1
                    total["difficulty"] += 1
            except (TypeError, ValueError):
                pass
        per_bank.append(row)
    return {"total": total, "banks": per_bank,
            "scope": bank_id or "all"}
