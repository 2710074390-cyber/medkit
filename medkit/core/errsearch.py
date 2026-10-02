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

⇒ **缩写/别名召回不在模糊匹配的能力范围内**，只能靠**别名表**。
2026-10-02 已落地 **D4 别名表**（`core/kp_aliases.py`）——见下节。

## D4 别名表已落地（2026-10-02，本节取代旧的「已知边界」表述）

上面的结论「缩写只能靠别名表」现在**已实现**：`core/kp_aliases.py` 提供等价组，
`search()` 经 `kp_aliases.expand()` 把 token 转成**组**再计分。

⚠️ **重要勘误（本条推翻了我此前的记述）**：我曾写「`心衰` 是 `心力衰竭` 的子串，
所以本模块能命中、不需要别名表」——**错了**。实测：

```
>>> "心衰" in "心力衰竭"
False          # ← 前两字是「心力」，不是「心衰」
>>> "心衰" in "心力衰竭失代偿期的处理"
False
```

⇒ `心衰` **不是** `心力衰竭` 的子串，**查「心衰」命中不了只写「心力衰竭」的记录**
（`errsearch.search` 实测 count=0）。它偶尔「看起来能中」是因为文本里
**恰好也写了「心衰」**（两写法同现），不是子串匹配的功劳。

| 查询 | 文本 | 子串命中？ | 需要别名表？ |
|---|---|---|---|
| `心衰` | `心力衰竭` | ❌ **否** | **是**（典型场景） |
| `湿啰音` | `中细湿啰音` | ✅ 是 | 否 |
| `心肌梗死` | `急性心肌梗死` | ✅ 是（单向） | 否（但同组增反向召回） |
| `COPD` | `慢性阻塞性肺疾病` | ❌ 否 | **是** |

（别再把「心衰」当"子串能覆盖"的例子——真相相反。）

### 真实 FTS5 那边为什么是 0 命中（与上表不矛盾）

`db.fts_tokens` docstring 记的 `"心衰"*` → 0 命中是**另一条机制**：
FTS5 **只有 token 匹配（精确/前缀）、没有子串匹配** ⇒ 缩写天然查不到。
本模块（子串匹配）与它**不是同一条路径**，但结论对 `心衰` 恰好一致（都命中不了）。

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

from . import kp_aliases
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
    """查询串 → 去重后的 token（≥2 字，≤`MAX_TOKENS` 项）。

    D4：先用**归一后的整串**查别名表（`kp_aliases.match_query`）——
    jieba 会把中文简称切碎（「慢阻肺」→ `['慢阻','阻肺']`，逐 token 查表全落空），
    整串匹配才能兜住。命中时把**整组打头放**，既保证召回，
    也让「慢阻肺」这类词进得了 token 列表（否则会被二元组打散、覆盖率被稀释）。
    """
    out: list[str] = []
    hit = kp_aliases.match_query(query)
    if hit:
        # 放**用户实际输入的那个词**（整串原形），不是组的首成员——
        # 后者可能是 ASCII 缩写（如 `心衰` 组的首成员是 `chf`），
        # 塞进 token 只会增加噪音、还可能误导片段取材。
        out.append(query.strip())
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


def _score_card(card: dict[str, Any], groups: list[list[str]]) -> tuple[float, set[str], set[int]]:
    """返回 `(分数, 命中字段集合, 命中**组下标**集合)`。

    分数 = Σ(字段权重 × 该字段命中的 **distinct 组** 数)。同一组在多个字段命中会累加
    ——这是想要的：题干与想法都提到「湿啰音」比只在解析里出现一次更相关。

    ## 组（`groups`）而非裸 token —— D4 别名表的接口契约

    `groups` 由 `kp_aliases.expand()` 产出：每个元素是一组**等价词**
    （如 `["copd", "慢阻肺", "慢性阻塞性肺疾病", "慢性阻塞性肺病"]`），
    **任一成员命中即算该组命中**。

    这样做的原因（改动前务必理解，否则会把排序改坏）：
    `search` 的排序首键是 **覆盖率 = 命中组数 / 总组数**。若把别名直接拍平成裸 token 追加，
    「慢性阻塞性肺疾病」会从 1 个 token 变成 4 个 ⇒ 总 token 数膨胀 ⇒ 同一张卡片
    只因主词命中的覆盖率被**稀释**，排序反而变差。按**组**计则总组数不变。
    """
    score = 0.0
    hit_fields: set[str] = set()
    hit_groups: set[int] = set()
    for field, weight in FIELD_WEIGHTS:
        text = _field_text(card, field)
        if not text:
            continue
        matched = 0
        for gi, members in enumerate(groups):
            if any(m.lower() in text for m in members):
                matched += 1
                hit_groups.add(gi)
        if matched:
            hit_fields.add(field)
            score += weight * matched
    return score, hit_fields, hit_groups


def _pick_snippet(card: dict[str, Any], hit_fields: set[str], groups: list[list[str]]) -> str:
    """片段取材：按 `FIELD_WEIGHTS` 顺序找**第一个命中且可读**的字段。

    `options` 是列表、`subject`/`chapter` 太短、`answer` 一填就容易变成"答案速查"，
    都不适合做片段——跳过它们取下一个。

    `groups` 为等价组（D4）：**先按组内成员的书写顺序找命中的那个成员**，
    用**实际命中的成员**去定位片段（拿别名去原文里找位置会 `find` 失败、
    退化成"片段不含命中点"——那正是 `_snippet` 第一版就修掉的缺陷）。
    """
    skip = {"options", "subject", "chapter", "answer", "user_answer"}
    for field, _w in FIELD_WEIGHTS:
        if field not in hit_fields or field in skip:
            continue
        raw = card.get(field)
        text = " ".join(str(x) for x in raw) if isinstance(raw, (list, tuple)) else str(raw or "")
        norm_text = _norm(text)
        for members in groups:
            for m in members:
                if m.lower() in norm_text:
                    return _snippet(text, m)
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

    # D4：token → 等价组（别名表）。无别名时是单元素组 ⇒ 行为与改动前**逐字相同**。
    groups = kp_aliases.expand(toks)

    scored: list[tuple[float, float, dict[str, Any], set[str]]] = []
    for c in cards:
        score, fields, hit = _score_card(c, groups)
        if not fields:
            continue
        scored.append((len(hit) / len(groups), score, c, fields))
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
            "snippet": _pick_snippet(c, fields, groups),
        })
    return {"query": query or "", "tokens": toks, "count": len(items), "items": items,
            "scope": "错题与笔记（不含教材正文）"}


__all__ = ["DEFAULT_LIMIT", "FIELD_WEIGHTS", "MAX_LIMIT", "query_tokens", "search"]
