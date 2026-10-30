"""routers：Ctrl/⌘+K 命令面板的**聚合检索**（题目 / 错题 / 考点）。

## 为什么在服务端聚合，而不是前端连发三个请求

1. **截断口径要在一处**：三个域各取前 N 条才有意义（"题目 6 条 + 错题 3 条 + 考点 2 条"）。
   前端各发各的，`limit` 就变成"每域 12 条"，面板会被最长的那个域撑满，用户看不出
   哪一段更相关。
2. **一段坏不拖垮其余**：每组独立 try/except，失败的域只是缺席并把原因带回去。
   这是前端「搜索失败只少一段结果、不让面板变哑」那条承诺的服务端一半——只在
   JS 里 `catch` 只能兜住网络层，兜不住"某个域的索引坏了"。

## 口径边界（写在代码里，免得被当成承诺）

- **不做同义词**：「慢阻肺」搜不到只写「慢性阻塞性肺疾病」的题。`bank_iq` 的归一只动
  格式不动语义，替代不了医学别名词典；方案见 `docs/adr/ADR-002-fts5-jieba-retrieval.md`。
- **不做模糊纠错、不做拼音**：面板是"我记得题目里有这几个字"的入口，不是开放式问答。
- 多词是 **AND**（每个词都要出现），不是并集——并集在千题库里等于没筛。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from .. import state
from ..core import bank as banklib
from ..core import syllabus as sylllib
from ..core import vault_index

router = APIRouter(tags=["palette"])

_PER_GROUP_DEFAULT = 6
_PER_GROUP_MAX = 20

# 三个域的展示顺序 = 用户最可能想要的顺序：题目 → 错题 → 考点
# （考点排在最后：它更像"顺带看看有没有这个知识点"，而不是直接命中内容）
_GROUP_LABELS = (("bank", "题目"), ("mistake", "错题"), ("kc", "考点"))


def _tokens(q: str) -> list[str]:
    """空白切词 + 小写。与 `bank.search_items` 同一口径（它内部还会再切一次）。"""
    return [t for t in str(q or "").lower().split() if t]


def _hit_all(text: str, tokens: list[str]) -> bool:
    low = str(text or "").lower()
    return bool(low) and all(t in low for t in tokens)


def _bank_group(q: str, limit: int) -> dict[str, Any]:
    res = banklib.search_items(q, limit=limit)
    items = [{
        "kind": "bank",
        "item_id": it.get("item_id", ""),
        "bank_id": it.get("bank_id", ""),
        "title": it.get("question", ""),
        "sub": " · ".join(x for x in (it.get("bank_subject") or it.get("bank_name"),
                                      it.get("bank_term"), it.get("type")) if x),
        "hit": it.get("hit", ""),
        # 预览要用的全部字段（只读，不给「跳去作答」——搜题多半是在确认这题怎么写的）
        "options": it.get("options", []),
        "answer": it.get("answer", ""),
        "analysis": it.get("analysis", ""),
        "subtopic": it.get("subtopic", ""),
    } for it in res.get("items", [])]
    return {"kind": "bank", "label": "题目", "total": int(res.get("total") or 0),
            "items": items}


def _mistake_group(q: str, limit: int) -> dict[str, Any]:
    rows = vault_index.list_items(q=q)
    items = [{
        "kind": "mistake",
        "path": r.get("path", ""),
        "title": r.get("question") or r.get("topic") or r.get("path", ""),
        "sub": " · ".join(x for x in (r.get("subject"), r.get("chapter"),
                                      r.get("topic")) if x),
        "hit": "错题",
        "options": [str(x) for x in (r.get("options") or [])] if isinstance(r.get("options"), list) else [],
        "answer": r.get("answer", ""),
        "analysis": r.get("analysis", ""),
        # 用户自己写的那两段是错题最有价值的部分——预览里给出来，别让搜索只停在题干
        "understanding": r.get("understanding", ""),
        "doubts": r.get("doubts", ""),
        "status": r.get("status", ""),
        "due": r.get("due", ""),
    } for r in rows[:limit]]
    return {"kind": "mistake", "label": "错题", "total": len(rows), "items": items}


def _kc_group(q: str, limit: int) -> dict[str, Any]:
    """考点（`syllabus_items` 的 `kind='item'`）。

    开关关闭时返回空组而不是 404：面板是"顺带搜一下"，整块入口不该因为进阶功能没开
    就报错（用户没开大纲管理，考点本来就该是空的）。
    """
    if not state.flag("syllabus"):
        return {"kind": "kc", "label": "考点", "total": 0, "items": []}
    tokens = _tokens(q)
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for row in sylllib.list_kc_items():
        item = str(row.get("item") or "")
        chapter = str(row.get("chapter") or "")
        subject = str(row.get("subject") or "")
        if not _hit_all(f"{item} {chapter} {subject}", tokens):
            continue
        # 考点名本身命中最相关；只命中章节/科目次之；同档按名字短的在前（更精确）
        key = (0 if _hit_all(item, tokens) else 1, len(item))
        scored.append((key, {
            "kind": "kc",
            "kc_id": str(row.get("id") or ""),
            "title": item,
            "sub": " · ".join(x for x in (subject, chapter) if x),
            "hit": "考点",
        }))
    scored.sort(key=lambda x: (x[0], x[1]["title"]))
    return {"kind": "kc", "label": "考点", "total": len(scored),
            "items": [rec for _, rec in scored[:limit]]}


_GROUP_FUNCS = {"bank": _bank_group, "mistake": _mistake_group, "kc": _kc_group}


@router.get("/api/palette/search")
def palette_search(q: str = "", limit: int = _PER_GROUP_DEFAULT) -> dict[str, Any]:
    """一个入口搜题目 / 错题 / 考点（Ctrl/⌘+K 面板）。

    `q` 为空 ⇒ 空结果（不是全库）：面板还没输入时该显示命令，不是千题。
    `limit` 是**每组**上限，不是总数——三段各取前 N 条，短的那段不会因为长的占位而消失。
    """
    q = (q or "").strip()
    try:
        limit = int(limit or _PER_GROUP_DEFAULT)
    except (TypeError, ValueError):
        limit = _PER_GROUP_DEFAULT
    limit = max(1, min(limit, _PER_GROUP_MAX))
    if not q:
        return {"q": "", "groups": [], "errors": []}

    groups: list[dict[str, Any]] = []
    errors: list[str] = []
    for kind, label in _GROUP_LABELS:
        try:
            g = _GROUP_FUNCS[kind](q, limit)
        except Exception as e:  # noqa: BLE001  单域失败只让这一段缺席，整块仍可用
            errors.append(f"{label}：{e.__class__.__name__}: {e}")
            g = {"kind": kind, "label": label, "total": 0, "items": []}
        if g["items"] or g["total"]:
            groups.append(g)
    return {"q": q, "limit": limit, "groups": groups, "errors": errors}
