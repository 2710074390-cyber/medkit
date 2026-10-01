"""在自己的错题与笔记里检索（EP-01 阶段 4 · 原方案 D1）。

## 检索范围**严格限定在用户自己的数据内**

《总纲》§2.4 的硬约束 + 方案 §7 阶段 4 的修正：检索面只有**错题（含其中的想法/错因/修正）
与知识点表述**，**不含教材正文**——教材内容走生成链的 `slices_fts`，两条面不能混
（混了就等于把机构讲义灌进知识库，§2.4 明令禁止）。本模块因此**只吃 `list[dict]` 的错题**，
不 import 任何教材检索模块（有 AST 守卫钉住）。

## 为什么**不建** `mistakes_fts` 索引（有意不抽象）

- 错题量级在万级以下，实时扫一遍的代价可忽略（与 `metacog` 全纯函数同一理由：
  四个统计指标都随口径变化，落表就要处理缓存失效，而实时算成本可忽略）；
- 建索引要动 schema（`user_version` +1 ⇒ 用户真实库迁移）+ 每次增删改都要同步
  ⇒ **引入"索引与真身漂移"这一类全新的失效模式**，而收益只是"快一点"。
- **判据：这个优化会不会引入新的失败模式？** 会 ⇒ 不做（《总纲》§2.1 停手信号）。

## 分词复用 `db.fts_tokens`（单源）

jieba 词 + **CJK 二元组**。⚠️ **二元组在本模块里的作用与在 FTS5 里不同**，别混：

| 场景 | 匹配机制 | 二元组的作用 |
|---|---|---|
| **FTS5**（`slices_fts`） | 只做 **token** 匹配（精确/前缀），**没有子串匹配** | **关键**：「湿啰音」能命中「中细湿啰音」，靠的就是索引侧存了 bigram「湿啰」 |
| **本模块**（子串匹配） | `查询token in 归一文本` | **部分重叠召回**：查询「湿啰音」也能命中只含「湿啰」的卡片（连续子串已由子串匹配覆盖） |

⇒ 换言之：**「湿啰音」→「中细湿啰音」在本模块里是子串匹配命中的**（「湿啰音」本就是
「中细湿啰音」的连续子串），**不是**二元组的功劳。二元组带来的额外召回是"只沾了查询的一段"。
（这条我第一版又说反了——**同一类错误在本次会话里犯了三次**，见文末留档。）

## ⚠️ 一条被实测推翻的设计（留档，别再走回头路）

初版做了**两段式召回**：① 子串（≈0 ms）→ 零命中时跑 ② 分词**前缀**匹配（≈340 ms/千条），
设想用 ② 兜「缩写」（心衰 → 心力衰竭）。**实测两点都错**：

1. **② 严格弱于 ①**：jieba token 与二元组都是原文的**连续子串**；
   若查询词是某个 token 的**前缀**，那它本身就是原文的子串 ⇒ ① 早就命中了。
   ② 一次都救不回 ① 漏掉的东西，只白花 340 ms。
2. **缩写不是前缀**：「心力衰竭」的前两字是「心力」——`心衰` **不是**它的前缀。
   真 FTS5 实测：`"心衰"*` 对「心力衰竭的机制」命中 **0**（`"心力"*` 才命中 1）。
   （顺带发现 `db.fts_tokens` 的 docstring 曾拿这个当例子，是错的，已勘误。）

⇒ **缩写/别名召回不在模糊匹配的能力范围内**，只能靠**别名表**
（方案 §9.4 / 待办 D4，需用户提供常用表述）。这是本模块的**已知边界**，不是待修的 bug。

## 留档：我在本次会话里把「召回机制」说错了三次

1. 说「二元组兜底 ⇒『心衰』命中『心力衰竭』」——**缩写不是前缀**，真 FTS5 实测 0 命中；
2. 说「前缀扫描能兜缩写」——**前缀严格弱于子串**（jieba token 是原文的连续子串），白花 340 ms；
3. 说「二元组让『湿啰音』命中『中细湿啰音』」——那是**子串**命中的，二元组在本模块里管的是
   **部分重叠**（FTS5 里才是它管的，因为 FTS5 没有子串匹配）。

**共同教训**：**「我举的例子能跑通」不等于「是我说的那个机制让它跑通的」**。
凡要给机制举例子，先**把机制拆成可分别验证的最小对照**——
本例的对照就是「查询串本身是不是文本的连续子串」：是 ⇒ 子串匹配就能解释，与二元组无关。
"""

from __future__ import annotations

import re
from typing import Any

from .db import fts_tokens

# 检索字段与权重（越大越"这题在讲什么"）。
# ⚠️ 顺序即**片段取材优先级**：题干 > 我的想法 > 修正 > 错因 > 解析 > 答案。
FIELD_WEIGHTS: tuple[tuple[str, int], ...] = (
    ("question", 5),
    ("topic", 3),
    ("error_tag", 3),
    ("ai_error_tag", 3),
    ("my_reasoning", 3),
    ("fix", 2),
    ("chapter", 2),
    ("options", 2),
    ("analysis", 1),
    ("subject", 1),
    ("answer", 1),
    ("user_answer", 1),
)

# 查询 token 下限长度：单字召回噪声太大（「的」「是」会命中一切）
MIN_TOKEN_LEN = 2
# 查询 token 上限：与 `db.fts_match_expr` 同口径（≤40）
MAX_TOKENS = 40
# 片段上下文半径（字符）
SNIPPET_RADIUS = 26
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_WS = re.compile(r"\s+")


def _norm(v: Any) -> str:
    """归一：小写 + 去空白。中文检索里空格通常是用户随手打的，不该影响召回。"""
    return _WS.sub("", str(v or "").lower())


def query_tokens(query: str) -> list[str]:
    """查询串 → 去重后的 token（≥2 字，≤`MAX_TOKENS` 项）。"""
    out: list[str] = []
    for t in fts_tokens(query or ""):
        if len(t) >= MIN_TOKEN_LEN and t not in out:
            out.append(t)
    return out[:MAX_TOKENS]


def _field_text(card: dict[str, Any], field: str) -> str:
    """取字段的归一化文本；`options` 是列表，拼起来一起搜。"""
    v = card.get(field)
    if isinstance(v, (list, tuple)):
        return _norm(" ".join(str(x) for x in v))
    return _norm(v)


def _snippet(text: str, token: str) -> str:
    """以**第一个命中处**为中心取片段（命中点必须在片段里，否则片段没有信息量）。"""
    raw = str(text or "")
    pos = raw.lower().find(token)
    if pos < 0:
        return raw[:SNIPPET_RADIUS * 2]
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(raw), pos + len(token) + SNIPPET_RADIUS)
    return ("…" if start else "") + raw[start:end] + ("…" if end < len(raw) else "")


def _score_card(card: dict[str, Any], tokens: list[str]) -> tuple[float, set[str], set[str]]:
    """返回 `(分数, 命中字段集合, 命中 token 集合)`。

    分数 = Σ(字段权重 × 该字段命中的 **distinct** token 数)。同一 token 在多个字段命中会累加
    ——这是想要的：题干与想法都提到「湿啰音」比只在解析里出现一次更相关。
    """
    score = 0.0
    hit_fields: set[str] = set()
    hit_tokens: set[str] = set()
    for field, weight in FIELD_WEIGHTS:
        text = _field_text(card, field)
        if not text:
            continue
        matched = [t for t in tokens if t in text]
        if matched:
            hit_fields.add(field)
            hit_tokens.update(matched)
            score += weight * len(matched)
    return score, hit_fields, hit_tokens


def _pick_snippet(card: dict[str, Any], hit_fields: set[str], tokens: list[str]) -> str:
    """片段取材：按 `FIELD_WEIGHTS` 顺序找**第一个命中且可读**的字段。

    `options` 是列表、`subject`/`chapter` 太短、`answer` 一填就容易变成"答案速查"，
    都不适合做片段——跳过它们取下一个。
    """
    skip = {"options", "subject", "chapter", "answer", "user_answer"}
    for field, _w in FIELD_WEIGHTS:
        if field not in hit_fields or field in skip:
            continue
        raw = card.get(field)
        text = " ".join(str(x) for x in raw) if isinstance(raw, (list, tuple)) else str(raw or "")
        for t in tokens:
            if t in _norm(text):
                return _snippet(text, t)
    # 只命中短字段/答案 → 退化为题干开头（保证每条结果都有可读内容）
    return str(card.get("question") or "")[:SNIPPET_RADIUS * 2]


def search(cards: list[dict[str, Any]], query: str, *,
           subject: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """在自己的错题里检索。返回 `{query, tokens, count, items, scope}`。

    - `tokens` 回显实际用的 token（**空 = 查询串太短/全是单字**）——
      前端据此区分「没搜」（提示换个词）与「搜了没命中」；
    - 排序：先覆盖率（命中 token 数 / 总 token 数）、再分数（见模块 docstring）；
    - `subject` 是**作用域过滤**（与错题本"科目"下拉同口径），不是查询条件。
    """
    toks = query_tokens(query)
    lim = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    if subject:
        cards = [c for c in cards if str(c.get("subject") or "") == subject]
    if not toks:
        return {"query": query or "", "tokens": [], "count": 0, "items": [],
                "scope": "错题与笔记（不含教材正文）"}

    scored: list[tuple[float, float, dict[str, Any], set[str]]] = []
    for c in cards:
        score, fields, hit = _score_card(c, toks)
        if not fields:
            continue
        scored.append((len(hit) / len(toks), score, c, fields))
    # 稳定排序：覆盖率 → 分数 → id（同分时顺序可复现，便于测试与用户预期）
    scored.sort(key=lambda x: (-x[0], -x[1], str(x[2].get("id") or "")))

    items = []
    for coverage, score, c, fields in scored[:lim]:
        items.append({
            "id": str(c.get("id") or ""),
            "subject": c.get("subject") or "",
            "chapter": c.get("chapter") or "",
            "topic": c.get("topic") or "",
            "question": c.get("question") or "",
            "error_tag": c.get("error_tag") or c.get("ai_error_tag") or "",
            "round": c.get("round") or "",
            "score": round(score, 2),
            "coverage": round(coverage, 3),
            "matched_fields": sorted(fields),
            "snippet": _pick_snippet(c, fields, toks),
        })
    return {"query": query or "", "tokens": toks, "count": len(items), "items": items,
            "scope": "错题与笔记（不含教材正文）"}


__all__ = ["DEFAULT_LIMIT", "FIELD_WEIGHTS", "MAX_LIMIT", "query_tokens", "search"]
