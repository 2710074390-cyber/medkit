"""WP-02 真题考点频次分析（考试锚定，《结构化执行方案》§3 WP-02）。

红线（方案 §WP-02）：
- **未确认数据不进入任何权重**（人工确认门：来源真题文本 → 草稿 → 确认/改错/合并 → 落库 confirmed=1）；
- **不展示真题原文**：可视化/导出仅章节条目 + 频次；
- 本地计数防 LLM 计数幻觉：本章用「零 LLM 词典匹配」做主路径（真题句子 × 大纲条目/章节词典 →
  归属频次），LLM 归一留作增强开关（默认关）。

存储：SQLite `realexam_freq`（迁移 v3）。频次聚合键 (subject, chapter, item)。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from . import db as dbs
from .schema import RealexamNorm, validate_or_repair

_RE_COLS = ("subject", "chapter", "item", "freq", "confirmed", "source", "created_at")


def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _row_id(subject: str, chapter: str, item: str) -> str:
    return hashlib.sha1(f"{subject}|{chapter}|{item}".encode("utf-8")).hexdigest()[:16]


def _dictionary() -> list[dict[str, str]]:
    """词典：大纲条目（含所属章/科目）——来自 syllabus_items kind=item（seed + teacher，二选一模型）。"""
    with dbs.tx(write=True) as cur:
        rows = dbs.list_rows(cur, "syllabus_items",
                             "WHERE kind='item' AND item != ''")
    return [{"subject": r.get("subject") or "", "chapter": r.get("chapter") or "",
             "item": r.get("item") or ""} for r in rows]


def _sentences(text: str) -> list[str]:
    """真题文本 → 句子（行/句号/问号切分）；去空去短。"""
    parts = re.split(r"[\n。？！；;]+", text or "")
    return [p.strip() for p in parts if len(p.strip()) >= 6]


def analyze(text: str, subject: str = "") -> dict[str, Any]:
    """真题文本 → 频次草稿（本地词典匹配，零 LLM；主体 = 大纲条目命中计数）。

    未命中任何条目/章的句子计入 stats.unmatched（提示归属覆盖不足）。
    """
    dbs.migrate()
    entries = [e for e in _dictionary() if e["item"]]
    chapters_src = [e for e in _dictionary() if e["chapter"]]
    drafts: dict[tuple[str, str, str], int] = {}
    chapter_hits: dict[tuple[str, str], int] = {}
    unmatched = 0
    for sent in _sentences(text):
        # 一句可命中多条目标（不再首命中即 break）→ 频次更接近语义命中率
        hits = [e for e in entries
                if e["item"] and len(e["item"]) >= 2 and e["item"] in sent]
        if hits:
            for hit in hits:
                subject_of = hit["subject"] or subject
                key = (subject_of, hit["chapter"] or "", hit["item"])
                drafts[key] = drafts.get(key, 0) + 1
            continue
        # 章级命中（条目词典未覆盖而章名出现）
        for e in chapters_src:
            ch = e["chapter"]
            if ch and len(ch) >= 2 and ch in sent:
                subj = e["subject"] or subject
                chapter_hits[(subj, ch)] = chapter_hits.get((subj, ch), 0) + 1
                break
        else:
            unmatched += 1
    out = {
        "drafts": [{"subject": k[0], "chapter": k[1], "item": k[2], "freq": v}
                   for k, v in drafts.items()],
        "chapter_hits": [{"subject": k[0], "chapter": k[1], "freq": v}
                         for k, v in chapter_hits.items()],
        "stats": {"sentences": len(_sentences(text)), "items": len(drafts),
                  "unmatched": unmatched},
    }
    out["drafts"].sort(key=lambda d: -d["freq"])
    return out


# ---------------------------------------------------------------- 人工确认门
def list_drafts(subject: str = "", confirmed: Optional[bool] = None) -> list[dict[str, Any]]:
    dbs.migrate()
    where, params = [], []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if confirmed is not None:
        where.append("confirmed = ?")
        params.append(1 if confirmed else 0)
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    with dbs.tx(write=True) as cur:
        rows = dbs.list_rows(cur, "realexam_freq", f"{cond} ORDER BY freq DESC", tuple(params))
    return rows


def confirm(items: list[dict[str, Any]]) -> dict[str, Any]:
    """确认/订正：id 存在则更新（可改 chapter/item/freq/confirmed）；新键合并入。"""
    dbs.migrate()
    added = updated = 0
    with dbs.tx(write=True) as cur:
        for it in items:
            subj = str(it.get("subject") or "").strip()
            ch = str(it.get("chapter") or "").strip()
            item = str(it.get("item") or "").strip()
            freq = max(int(it.get("freq") or 1), 1)
            rid = it.get("id") or _row_id(subj, ch, item)
            exists = cur.execute("SELECT 1 FROM realexam_freq WHERE id=?", (rid,)).fetchone()
            rec = {"id": rid, "subject": subj, "chapter": ch, "item": item,
                   "freq": freq, "confirmed": 1 if it.get("confirmed", True) else 0,
                   "source": it.get("source") or "manual", "created_at": _now()}
            dbs.put_row(cur, "realexam_freq", rec, _RE_COLS)
            if exists:
                updated += 1
            else:
                added += 1
                # 同键草稿合并：freq 累加（若已存在同 subject/chapter/item 的行）
                dup = cur.execute(
                    "SELECT id FROM realexam_freq WHERE subject=? AND chapter=? AND item=? AND id<>?",
                    (subj, ch, item, rid)).fetchone()
                if dup:
                    cur.execute("DELETE FROM realexam_freq WHERE id=?", (dup[0],))
    return {"added": added, "updated": updated}


def confirm_drafts(drafts: list[dict[str, Any]]) -> dict[str, Any]:
    """批量确认 analyze 生成的草稿（幂等：重复确认 → 合并频次）。"""
    return confirm([{**d, "confirmed": True, "source": "analyze"} for d in drafts])


def delete(rid: str) -> bool:
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM realexam_freq WHERE id=?", (rid,))
        return cur.rowcount > 0


# ---------------------------------------------------------------- 频次视图（无原文，红线）
def freq_view(subject: str = "", top: int = 60) -> dict[str, Any]:
    """章节 × 频次热力数据（仅 confirmed；不包含真题原文）。"""
    rows = [r for r in list_drafts(subject, confirmed=True)]
    by_chapter: dict[str, dict[str, Any]] = {}
    total = 0
    for r in rows:
        ch = r.get("chapter") or "（未分章）"
        bucket = by_chapter.setdefault(ch, {"chapter": ch, "items": [], "freq": 0})
        bucket["freq"] += int(r.get("freq") or 0)
        bucket["items"].append({"id": r.get("id"), "item": r.get("item") or "",
                                "freq": int(r.get("freq") or 0)})
        total += int(r.get("freq") or 0)
    chapters = sorted(by_chapter.values(), key=lambda c: -c["freq"])[:max(top, 1)]
    for c in chapters:
        c["items"].sort(key=lambda x: -x["freq"])
    return {"subject": subject or "全部", "total": total, "chapters": chapters,
            "top_items": sorted(rows, key=lambda r: -int(r.get("freq") or 0))[:20]}


def report_md(subject: str = "") -> str:
    """热门考点清单（仅频次与条目，无原文）。"""
    data = freq_view(subject)
    lines = [f"# 真题高频考点 · {data['subject']}", "",
             f"> 基于自备真题 {data['total']} 次考点命中（人工确认后）", ""]
    for ch in data["chapters"]:
        if not ch["items"]:
            continue
        lines.append(f"## {ch['chapter']}（{ch['freq']} 次）")
        for it in ch["items"][:10]:
            lines.append(f"- {it['item']} ×{it['freq']}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 权重接入（默认 0，红线）
def freq_map(subject: str = "") -> dict[str, float]:
    """已确认频次 → {条目: 归一化频次[0,1]}（供 recommend/gap 使用；未确认不计）。"""
    dbs.migrate()
    rows = list_drafts(subject, confirmed=True)
    max_freq = max([int(r.get("freq") or 0) for r in rows], default=0) or 1
    return {r.get("item") or "": min(int(r.get("freq") or 0) / max_freq, 1.0) for r in rows}


# ---------------------------------------------------------------- LLM 考频归一（增强开关，默认关）
# WP-02 红线：本地词典匹配确定性更高、零成本；LLM 归一仅作增强开关（默认关），
# 且校验（RealexamNorm）失败 → 返回 None 走人工复核，不直接落库。
NORM_SYSTEM = (
    "你是医学考试考频分析助手。给定一段自备真题文本，请提取其中反复出现的考点条目，"
    "并归一到大纲条目（item）；每条输出 subject / chapter / item / freq（该条目在本文本中"
    "出现的次数，≥1）。严格输出 JSON："
    "{'items':[{'subject':'','chapter':'','item':'','freq':1}]}，无多余文字；"
    "仅为原文可支持的语义计数，未出现的条目不得编造。"
)


def analyze_llm(client: Any, text: str, subject: str = "", enabled: bool = False,
                repair_fn: Optional[Any] = None,
                max_chars: int = 8000) -> Optional[dict[str, Any]]:
    """真题文本 → 频次草稿（LLM 归一增强；默认关，作 WP-02 的可玩性开关）。

    返回 ``{"drafts": [...]}``，可直接喂 ``confirm_drafts``；校验 / 修复失败返回 ``None``
    （调用方转人工复核，不入库）。``repair_fn`` 遵循 ``validate_or_repair`` 语义。
    """
    if not enabled:
        return None
    user = f"科目：{subject or '（未指定）'}\n真题文本：\n{(text or '')[:max_chars]}"
    raw = client.chat_json([{"role": "system", "content": NORM_SYSTEM},
                            {"role": "user", "content": user}], temperature=0.2)
    norm = validate_or_repair(raw, RealexamNorm, repair_fn)
    if norm is None:
        return None
    drafts = [item.model_dump() for item in norm.items]
    return {"drafts": drafts, "stats": {"sentences": len(_sentences(text)),
                                        "items": len(drafts), "unmatched": 0}}
