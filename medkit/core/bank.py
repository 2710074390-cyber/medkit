"""题库域（bank domain）：用户自建 / 导入的题目作为一等数据存起来，供「练习」刷题。

## 定位（与出题工坊的关系）

| | 出题工坊（`#tab-bank`） | 题库域（本模块） |
|---|---|---|
| 定位 | 从教材**生成**新题 | **管理与练习**已有题 |
| 输入 | 教材切片 + 教师重点 + LLM | 用户自建 / md 导入 / 工坊产出 |
| 成本 | 烧 LLM token | **零 LLM** |

## 存储

**SQL-only**：三张表（`banks` / `bank_items` / `bank_attempts`）在 `db.py` 的 v9 迁移里建立。
与 `library` 的 JSON/SQL 双轨不同——题库域**没有历史 JSON 数据**，不需要双轨收敛，
也不需要回落（回落只会造出第二套真相）。调用方进门前先 `ensure_ready()`（= `dbs.migrate()`，
幂等）。

## 与既有资产的接线（不另起炉灶）

- 判错回流错题本：复用 `library.sync_from_paper()`（题面 + user_answer）。
- 「何时该复习」一律交给**知识点级调度**（`core/scheduler.py`：FSRS 默认 / SM-2 legacy，
  由 vault 调用），**题库域不实现第二套调度器**（两套调度器并存是维护灾难）。
  `bank_attempts` 只回答「这道题做过几次、对了几次」。
"""

from __future__ import annotations

import itertools
import re
import time
from datetime import datetime
from typing import Any, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import write_json_atomic

_BANK_COLS = ("name", "subject", "term", "source", "created_at", "updated_at")
_ITEM_COLS = ("bank_id", "qtype", "topic", "answer", "origin", "created_at")
_ATT_COLS = ("bank_id", "item_id", "session_id", "correct", "mode", "occurred_at",
             "confidence")
_SRC_COLS = ("bank_id", "fmt", "parser_version", "imported_at")

# 单次批量上限（对齐 routers 侧 mistakes 的 500 条口径；题库一次导入千题要分批）
MAX_BATCH = 500

_ID_SEQ = itertools.count()


def _new_id(prefix: str) -> str:
    """毫秒时间戳 + 进程内单调序号（同毫秒并发也不撞 id）。

    ⚠️ 序号**必须定宽补零**：本域的排序是 `ORDER BY <时间>, id`（见 `list_items` /
    `list_attempts`），而 `id` 是字符串比较——序号跨位数（9 → 10）时
    `"…_9" > "…_10"`，字典序与数值序不一致 ⇒ **题目顺序会翻**
    （实测：练习第 1 题变成第 2 题，且用例结果随「文件执行顺序」变化）。
    """
    return f"{prefix}_{int(time.time() * 1000)}_{next(_ID_SEQ):06d}"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_ready() -> None:
    """进题库域前先建库/迁移（幂等）。失败向上抛——调用方给可读错误，不静默回落。"""
    dbs.migrate()


# ---------------------------------------------------------------- 题库 CRUD
def _row_bank(cur: Any, bid: str) -> Optional[dict[str, Any]]:
    return dbs.find_row(cur, "banks", "id=?", (bid,))


def list_banks() -> list[dict[str, Any]]:
    """题库列表（按 updated_at 倒序），每项带 items/attempted 计数与掌握率。"""
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        banks = dbs.list_rows(cur, "banks")
        counts: dict[str, int] = {}
        for item in dbs.list_rows(cur, "bank_items"):
            key = str(item.get("bank_id") or "")
            counts[key] = counts.get(key, 0) + 1
        # 只取 bank_id / parser_version 两列：原文可能几百 KB，列表页不需要正文
        cur.execute("SELECT bank_id, parser_version FROM bank_sources")
        srcs = {str(r[0]): str(r[1] or "") for r in cur.fetchall()}
        stats = _stats_by_bank(cur)
    finally:
        cur.close()
    from . import bank_iq
    out: list[dict[str, Any]] = []
    for bank in banks:
        bid = str(bank.get("id") or "")
        s = stats.get(bid, {})
        pv = srcs.get(bid, "")
        out.append({**bank, "items": counts.get(bid, 0),
                    "attempted": s.get("attempted", 0),
                    "mastery": s.get("mastery", 0.0),
                    "accuracy": s.get("accuracy", 0.0),
                    # 重解析可用性：有原文才显示按钮；版本落后于当前解析器则提示「可修复」
                    "has_source": bid in srcs,
                    "parser_stale": bool(pv) and pv != bank_iq.PARSER_VERSION})
    out.sort(key=lambda r: str(r.get("updated_at") or r.get("created_at") or ""), reverse=True)
    return out


def get_bank(bid: str) -> Optional[dict[str, Any]]:
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        return _row_bank(cur, bid)
    finally:
        cur.close()


def create_bank(name: str, subject: str = "", source: str = "",
                items: Optional[list[dict[str, Any]]] = None,
                origin: str = "manual", term: str = "") -> dict[str, Any]:
    """建题库（可同时灌入题目）。返回落库后的题库记录（含 items 计数）。

    `term` = 学期（`大三下` / `大四上`）：同一科目跨学期常有多份题库，只凭 `subject`
    分不清「这本题库属于哪个学期」。空串表示未知（用户可后续手工补）。
    """
    ensure_ready()
    name = (name or "").strip() or "未命名题库"
    bid = _new_id("bk")
    now = _now()
    rec = {"id": bid, "name": name, "subject": (subject or "").strip(),
           "term": (term or "").strip(),
           "source": (source or "").strip(), "created_at": now, "updated_at": now}
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, "banks", rec, _BANK_COLS)
    if items:
        add_items(bid, items, origin=origin)
    return get_bank(bid) or rec


def update_bank(bid: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    ensure_ready()
    allowed = {"name", "subject", "term", "source"}
    with dbs.tx(write=True) as cur:
        rec = _row_bank(cur, bid)
        if rec is None:
            return None
        for k, v in (patch or {}).items():
            if k in allowed:
                rec[k] = str(v or "").strip()
        rec["updated_at"] = _now()
        dbs.put_row(cur, "banks", rec, _BANK_COLS)
    return rec


def delete_bank(bid: str) -> dict[str, Any]:
    """删除题库（含题目与作答流水）。删除前自动导出完整备份 JSON 到 ~/.medkit/exports/。

    返回 {"deleted": bool, "items": n, "attempts": n, "backup": <路径或空>}。
    """
    ensure_ready()
    bank = get_bank(bid)
    if bank is None:
        return {"deleted": False, "items": 0, "attempts": 0, "backup": ""}
    items = list_items(bid)
    attempts = list_attempts(bank_id=bid)
    backup = ""
    try:
        exports = cfg.CONFIG_DIR / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = exports / f"bank_{bid}_{ts}.json"
        write_json_atomic(path, {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "bank": bank, "items": items, "attempts": attempts,
        })
        backup = str(path)
    except Exception:  # noqa: BLE001  备份失败不阻断删除（但如实告知调用方 backup 为空）
        backup = ""
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM bank_items WHERE bank_id=?", (bid,))
        cur.execute("DELETE FROM bank_attempts WHERE bank_id=?", (bid,))
        cur.execute("DELETE FROM bank_sources WHERE bank_id=?", (bid,))
        cur.execute("DELETE FROM banks WHERE id=?", (bid,))
    return {"deleted": True, "items": len(items), "attempts": len(attempts), "backup": backup}


# ---------------------------------------------------------------- 原文与重解析
def save_source(bid: str, raw_text: str, fmt: str = "md", parser_version: str = "") -> None:
    """保存导入原文（供解析器改进后重解析）。同一题库再次导入 → 覆盖（以最新一次为准）。"""
    ensure_ready()
    from . import bank_iq
    if not str(raw_text or "").strip():
        return
    with dbs.tx(write=True) as cur:
        rec = {
            "id": str(bid), "bank_id": str(bid), "fmt": str(fmt or "md"),
            "parser_version": str(parser_version or bank_iq.PARSER_VERSION),
            "imported_at": _now(), "raw_text": str(raw_text),
        }
        dbs.put_row(cur, "bank_sources", rec, _SRC_COLS)


def get_source(bid: str) -> Optional[dict[str, Any]]:
    """取该题库保存的原文（无则 None）。"""
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        return dbs.find_row(cur, "bank_sources", "bank_id=?", (bid,))
    finally:
        cur.close()


def reparse_bank(bid: str) -> dict[str, Any]:
    """用保存的原文**重新解析**并替换该题库的全部题目。

    这是「解析器改进后修旧数据」的唯一正路（动机见 `db._V10_UP` 注释）：
    解析器修好了，已入库的数据不会自动变干净——没有它就只能删库重导，而原文往往已不在。

    题库不存在 → KeyError；没有原文（手工建的题库 / 旧版导入）→ KeyError。

    ⚠️ **作答流水会清空**：重解析后题目 id 全部重发，旧流水按 id 关联会全部孤立。
    如实返回 `attempts_cleared`（不静默）。
    """
    ensure_ready()
    from . import bank_iq
    if get_bank(bid) is None:
        raise KeyError("题库不存在")
    src = get_source(bid)
    if src is None:
        raise KeyError("该题库没有保存原文，无法重解析（只有导入的题库会保存原文）")
    raw = str(src.get("raw_text") or "")
    fmt = str(src.get("fmt") or "md")
    if not raw.strip():
        raise KeyError("保存的原文为空，无法重解析")
    rows = bank_iq.parse_bank_text(raw, fmt)
    n_attempts = len(list_attempts(bank_id=bid))
    # 2026-10-16 修复（原子性）：删除与重建收进**同一事务**。此前 DELETE 先单独
    # 提交、add_items 再开第二个事务——重建中途失败（磁盘满/进程中断）题库被清空
    # 且未回填；两个事务之间并发读还会看到「空库」窗口。
    stats, to_write = _prepare_items(bid, rows, "md_import", "new", existing=[])
    now = _now()
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM bank_items WHERE bank_id=?", (bid,))
        cur.execute("DELETE FROM bank_attempts WHERE bank_id=?", (bid,))
        for rec in to_write:
            dbs.put_row(cur, "bank_items", rec, _ITEM_COLS)
        bank = _row_bank(cur, bid)
        if bank is not None:
            bank["updated_at"] = now
            dbs.put_row(cur, "banks", bank, _BANK_COLS)
        # 记下本次用的解析器版本（同事务，避免库已换血但版本号滞留）
        dbs.put_row(cur, "bank_sources", {
            "id": str(bid), "bank_id": str(bid), "fmt": fmt,
            "parser_version": bank_iq.PARSER_VERSION,
            "imported_at": now, "raw_text": raw,
        }, _SRC_COLS)
    return {
        "bank_id": bid, "parsed": len(rows), "items": len(list_items(bid)),
        "added": stats["added"], "skipped": stats["skipped"],
        "errors": list(stats["errors"])[:10],
        "attempts_cleared": n_attempts,
        "parser_version": bank_iq.PARSER_VERSION,
    }


# ---------------------------------------------------------------- 题目
def list_items(bid: str) -> list[dict[str, Any]]:
    """题库内题目，**按入库原序**（`created_at` 同秒时用 `id` 定序，id 序号定宽见 `_new_id`）。"""
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        rows = dbs.list_rows(cur, "bank_items", "WHERE bank_id=? ORDER BY created_at, id", (bid,))
    finally:
        cur.close()
    return rows


def get_item(iid: str) -> Optional[dict[str, Any]]:
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        return dbs.find_row(cur, "bank_items", "id=?", (iid,))
    finally:
        cur.close()


# ---- 跨题库搜题（本地、零 LLM） ----

# 关键词上限：词越多结果越少（多词 = AND），且每个词都要在 Python 里扫一遍。
_SEARCH_MAX_TOKENS = 8
# 参与匹配的字段与命中徽标文案（顺序 = 徽标优先级：题干 > 选项 > 考点 > 解析 > 答案）
_SEARCH_FIELDS: tuple[tuple[str, str], ...] = (
    ("question", "题干"), ("options", "选项"), ("subtopic", "考点"),
    ("analysis", "解析"), ("answer", "答案"),
)
_SEARCH_LIMIT_MAX = 50
# 分析原文上限：30 条结果 × 千字解析会把响应撑到 MB 级，而搜题只需要看一眼
_SEARCH_ANALYSIS_MAX = 1200


def _like_escape(s: str) -> str:
    """转义 LIKE 通配符。

    **必须做**：不转义时搜 `%` 会命中全库、搜 `_` 会退化成单字符通配——
    在「题目里含 5% 这种数字」的医学题库里这不是假想。
    """
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_text(it: dict[str, Any]) -> dict[str, str]:
    """把一条题摊成「字段 → 小写文本」。纯函数（可单测）。

    `options` 归一后是 `list[str]`，但第三方题库与老数据里也有 `{"A": "…"}` 的写法——
    两种都收，否则「选项里明明有这词却搜不到」。
    """
    opts = it.get("options")
    if isinstance(opts, dict):
        opt = " ".join(f"{k} {v}" for k, v in opts.items())
    elif isinstance(opts, (list, tuple)):
        opt = " ".join(str(x) for x in opts if x is not None)
    else:
        opt = str(opts or "")
    return {
        "question": str(it.get("question") or "").lower(),
        "options": opt.lower(),
        "subtopic": str(it.get("subtopic") or it.get("topic") or "").lower(),
        "analysis": str(it.get("analysis") or "").lower(),
        "answer": str(it.get("answer") or "").lower(),
    }


def _search_rank(it: dict[str, Any], tokens: list[str]) -> Optional[dict[str, Any]]:
    """命中判定 + 排序键。纯函数（可单测）；返回 None 表示不命中。

    **多词 = AND**：每个词都得在某个字段里出现。只命中一部分说明不相关
    （搜「慢阻肺 诊断」时，「慢阻肺」在题干、「诊断」在别处的题不该混进来）。
    """
    text = _search_text(it)
    joined = "\n".join(text[f] for f, _ in _SEARCH_FIELDS)
    if any(t not in joined for t in tokens):
        return None
    counts = {f: sum(1 for t in tokens if t in text[f]) for f, _ in _SEARCH_FIELDS}
    counts = {f: n for f, n in counts.items() if n}
    # 同分取靠前的字段（dict 保持 _SEARCH_FIELDS 顺序 ⇒ max 返回第一个最大值）
    best = max(counts, key=lambda f: counts[f])
    all_in_q = counts.get("question") == len(tokens)
    return {
        "field": dict(_SEARCH_FIELDS)[best],
        # 题干全中 > 题干部分命中 > 只命中其它字段；
        # 同档按命中词数多的在前，再按题干短的在前（词占题干比例越高，越像「就是问这个」）
        "key": (0 if all_in_q else 1 if "question" in counts else 2,
                -sum(counts.values()), len(text["question"])),
    }


def search_items(q: str = "", limit: int = 30, bank_id: str = "") -> dict[str, Any]:
    """跨题库搜题：**本地子串匹配，不联网、不调模型**。

    口径边界（说清楚，免得被当成同义词搜索）：「慢阻肺」**搜不到**只写
    「慢性阻塞性肺疾病」的题——那需要一份医学别名词典，而 `bank_iq` 的归一只动格式
    不动语义，替代不了它。词表方案见 ADR-002（FTS5 + jieba，千题级以上再上）。
    """
    q = (q or "").strip()
    tokens = [t for t in re.split(r"\s+", q.lower()) if t][:_SEARCH_MAX_TOKENS]
    if not tokens:
        return {"q": q, "tokens": [], "total": 0, "items": []}
    try:
        limit = int(limit or 30)
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, _SEARCH_LIMIT_MAX))
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        # SQL 侧只用**最长那个词**预筛（选择性最好，也省掉拼一长串 LIKE）；
        # 其余词在 Python 里做 AND——反正都要扫，SQL 里多拼几个 LIKE 没有收益。
        where = "WHERE data LIKE ? ESCAPE '\\'"
        params: list[Any] = ["%" + _like_escape(max(tokens, key=len)) + "%"]
        if bank_id:
            where += " AND bank_id=?"
            params.append(str(bank_id))
        rows = dbs.list_rows(cur, "bank_items", where + " ORDER BY created_at, id",
                            tuple(params))
    finally:
        cur.close()
    banks = {str(b.get("id") or ""): b for b in list_banks()}
    scored: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for it in rows:
        r = _search_rank(it, tokens)
        if r is None:
            continue
        bid = str(it.get("bank_id") or "")
        b = banks.get(bid) or {}
        scored.append((r["key"] + (str(it.get("created_at") or ""), str(it.get("id") or "")), {
            "item_id": str(it.get("id") or ""),
            "bank_id": bid,
            "bank_name": str(b.get("name") or ""),
            "bank_subject": str(b.get("subject") or ""),
            "bank_term": str(b.get("term") or ""),
            "question": str(it.get("question") or ""),
            "type": str(it.get("type") or ""),
            "subtopic": str(it.get("subtopic") or ""),
            "answer": str(it.get("answer") or ""),
            "options": list(it.get("options") or []) if isinstance(it.get("options"), list) else [],
            "analysis": str(it.get("analysis") or "")[:_SEARCH_ANALYSIS_MAX],
            "starred": bool(it.get("starred")),
            "hit": r["field"],
        }))
    scored.sort(key=lambda x: x[0])
    return {"q": q, "tokens": tokens, "total": len(scored),
            "items": [rec for _, rec in scored[:limit]]}


def _prepare_items(bid: str, items: list[dict[str, Any]], origin: str,
                   dedupe: str, existing: list[dict[str, Any]]
                   ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """构建待写入记录（纯计算，无库 I/O）。返回 (stats, to_write)。

    从 `add_items` 抽出，供 `reparse_bank` 把「删除 + 重建」收进同一事务。
    """
    from . import bank_iq  # 局部导入：避免 core 内循环依赖（bank_iq 会 import library）

    stats: dict[str, Any] = {"added": 0, "updated": 0, "skipped": 0, "errors": []}
    by_strict: dict[str, dict[str, Any]] = {}
    by_loose: dict[str, dict[str, Any]] = {}
    for it in existing:
        by_strict.setdefault(bank_iq.strict_key(it), it)
        by_loose.setdefault(bank_iq.loose_key(it), it)
    now = _now()
    to_write: list[dict[str, Any]] = []
    for i, raw in enumerate(items or [], 1):
        try:
            rec = bank_iq.normalize_item(raw, bid=bid, origin=origin, now=now)
            # `bank_items.topic` 是**冗余查询列**，但题记录里的字段名是 `subtopic`
            # ⇒ 不补这一行的话该列恒为 NULL，`idx_bi_topic` 索引白建，
            # 任何 `WHERE topic = ?` 都查不到东西（2026-10-27 实施 v0.22 阶段 2 时发现）。
            if rec is not None:
                rec["topic"] = rec.get("subtopic") or ""
                # 同族的第二处：`bank_items.qtype` 也是冗余查询列，而记录里的字段名是
                # `type`（`bank_iq.normalize_item` 的契约）⇒ 不补同样恒为 NULL。
                # 目前没有查询依赖它，但「列名与 data 对不上」正是 topic 那次的失效形态，
                # 一并纠正，免得下次有人信了这列。
                rec["qtype"] = rec.get("type") or ""
            if rec is None:
                stats["skipped"] += 1
                stats["errors"].append(f"#{i}: 缺题干或答案，已跳过")
                continue
            sk, lk = bank_iq.strict_key(rec), bank_iq.loose_key(rec)
            hit = by_strict.get(sk) or by_loose.get(lk)
            if hit is not None:
                if dedupe == "skip":
                    stats["skipped"] += 1
                    continue
                if dedupe == "update":
                    rec["id"] = hit["id"]
                    rec["created_at"] = hit.get("created_at") or now
                    to_write.append(rec)
                    stats["updated"] += 1
                    by_strict[sk] = rec
                    by_loose[lk] = rec
                    continue
                # dedupe == "new"：保留旧题，新题另起一行
            to_write.append(rec)
            stats["added"] += 1
            by_strict.setdefault(sk, rec)
            by_loose.setdefault(lk, rec)
        except Exception as e:  # noqa: BLE001  单条失败不阻断整批
            stats["skipped"] += 1
            stats["errors"].append(f"#{i}: {e}")
    return stats, to_write


def add_items(bid: str, items: list[dict[str, Any]], origin: str = "md_import",
              dedupe: str = "update") -> dict[str, Any]:
    """批量入题。dedupe ∈ {update, skip, new}（对应 Anki 的重复项三选一）。

    单条失败不阻断整批（照 `library.batch_add` 的模式）。返回 {added, updated, skipped, errors}。
    """
    ensure_ready()
    stats: dict[str, Any] = {"added": 0, "updated": 0, "skipped": 0, "errors": []}
    if not items:
        return stats
    if dedupe not in ("update", "skip", "new"):
        dedupe = "update"
    stats, to_write = _prepare_items(bid, items, origin, dedupe, list_items(bid))
    now = _now()
    if to_write:
        with dbs.tx(write=True) as cur:
            for rec in to_write:
                dbs.put_row(cur, "bank_items", rec, _ITEM_COLS)
            bank = _row_bank(cur, bid)
            if bank is not None:
                bank["updated_at"] = now
                dbs.put_row(cur, "banks", bank, _BANK_COLS)
    return stats


def delete_item(iid: str) -> bool:
    ensure_ready()
    with dbs.tx(write=True) as cur:
        row = dbs.find_row(cur, "bank_items", "id=?", (iid,))
        if row is None:
            return False
        cur.execute("DELETE FROM bank_items WHERE id=?", (iid,))
        cur.execute("DELETE FROM bank_attempts WHERE item_id=?", (iid,))
    return True


# 可编辑字段白名单（**正向枚举**：未列出的键一律忽略，不静默接受任意字段）
# 含 starred / note（题库域独有的收藏与笔记，只存在 data JSON 里）。
_ITEM_EDITABLE = (
    "question", "options", "answer", "analysis", "type", "bloom", "subtopic",
    "case_id", "case_order", "case_stem", "group_kind", "group",
    "image_ref", "data_table", "source_type", "source_year",
    "starred", "note",
)


def update_item(iid: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    """改一道题（局部更新：只覆盖 patch 里出现的可编辑字段）。

    重新走 `bank_iq.normalize_item` 归一（答案转大写、选项剥字母前缀、契约告警），
    保证「手改出来的题」与「导入进来的题」形状一致——否则两种来源的题在渲染/判分上会分叉。
    题干被改空 → 抛 `ValueError`（调用方给 400）。
    """
    ensure_ready()
    from . import bank_iq

    with dbs.tx(write=True) as cur:
        row = dbs.find_row(cur, "bank_items", "id=?", (iid,))
        if row is None:
            return None
        merged = dict(row)
        for k, v in (patch or {}).items():
            if k in _ITEM_EDITABLE:
                merged[k] = v
        rec = bank_iq.normalize_item(merged, bid=str(row.get("bank_id") or ""),
                                     origin=str(row.get("origin") or "manual"), now=_now())
        if rec is None:
            raise ValueError("题干不能为空")
        rec["topic"] = rec.get("subtopic") or ""   # 冗余查询列（见 _prepare_items 的说明）
        rec["id"] = row.get("id")            # id 不可改（作答流水按它关联）
        rec["created_at"] = row.get("created_at") or rec["created_at"]
        dbs.put_row(cur, "bank_items", rec, _ITEM_COLS)
        bank = _row_bank(cur, str(row.get("bank_id") or ""))
        if bank is not None:
            bank["updated_at"] = rec["updated_at"]
            dbs.put_row(cur, "banks", bank, _BANK_COLS)
    return rec


def set_starred(iid: str, starred: bool) -> Optional[dict[str, Any]]:
    """题目级收藏（星标）开关。"""
    return update_item(iid, {"starred": bool(starred)})


# ---------------------------------------------------------------- 作答流水
def _norm_confidence(confidence: Any) -> int:
    """自评置信度归一到 **0（未采集）或 1..5**。非法值一律当未采集，不抛错。

    为什么不抛错：置信度是**可选**输入（前端允许「跳过」）。为了一个可跳过的字段
    让整次判分失败，是把自己的可选功能变成别人的必填项。
    """
    try:
        v = int(confidence)
    except (TypeError, ValueError):
        return 0
    return v if 1 <= v <= 5 else 0


def _put_attempt_in(cur: Any, bank_id: str, item_id: str, user_answer: str,
                    correct: bool, mode: str, session_id: str, secs: int,
                    confidence: int) -> dict[str, Any]:
    """在**调用方的事务里**写一条流水（不自己开事务，理由见 `kc.put_state_in`）。"""
    rec = {
        "id": _new_id("at"),
        "bank_id": str(bank_id or ""), "item_id": str(item_id or ""),
        "session_id": str(session_id or ""), "user_answer": str(user_answer or ""),
        "correct": bool(correct), "mode": str(mode or "practice"),
        "secs": int(secs or 0), "confidence": _norm_confidence(confidence),
        "occurred_at": _now(),
    }
    dbs.put_row(cur, "bank_attempts", rec, _ATT_COLS)
    return rec


def record_attempt(bank_id: str, item_id: str, user_answer: str = "",
                   correct: bool = False, mode: str = "practice",
                   session_id: str = "", secs: int = 0,
                   confidence: int = 0) -> dict[str, Any]:
    """记一条作答流水（append-only）。返回落库记录。

    只写流水、**不动学习者状态**——需要两者原子一致时用 `record_attempt_with_kc`。
    """
    ensure_ready()
    with dbs.tx(write=True) as cur:
        return _put_attempt_in(cur, bank_id, item_id, user_answer, correct,
                               mode, session_id, secs, confidence)


def record_attempt_with_kc(bank_id: str, item_id: str, user_answer: str = "",
                           correct: bool = False, mode: str = "practice",
                           session_id: str = "", secs: int = 0,
                           confidence: int = 0,
                           kc_item_id: str = "") -> dict[str, Any]:
    """**写流水 + 更新 `kc_state`，同一事务**。返回 `{"attempt", "kc"}`。

    这是 v0.22 阶段 3 的核心：审计断点 A（「答对 50 题，掌握度纹丝不动」）的根治点。

    ## 为什么必须同事务

    两次独立写入之间失败，会留下「有流水、无状态」的裂口：流水是真相、状态是读模型，
    读模型落后一步**不会自愈**（除非全量重放）。同事务后，要么两者都在，要么都不在。

    ## 为什么放在 core 而不是 routers

    本仓 U-09 守卫禁止路由层写 `dbs.tx(` / 裸 SQL——事务边界归 core。
    路由层只负责参数校验与响应拼装。

    `kc_item_id` 用于「流水 id ≠ 映射 id」的场景（错题重练记的是错题 id，
    但它可能带 `source_ref.item_id` 指向原题）。
    """
    ensure_ready()
    from . import kc as kclib
    with dbs.tx(write=True) as cur:
        rec = _put_attempt_in(cur, bank_id, item_id, user_answer, correct,
                              mode, session_id, secs, confidence)
        kc = kclib.apply_attempt_in(cur, item_id, bool(correct), kc_item_id=kc_item_id)
    return {"attempt": rec, "kc": kc}


def list_attempts(bank_id: str = "", item_id: str = "") -> list[dict[str, Any]]:
    ensure_ready()
    conn = dbs.get_conn()
    cur = conn.cursor()
    try:
        if item_id:
            rows = dbs.list_rows(cur, "bank_attempts", "WHERE item_id=? ORDER BY occurred_at, id", (item_id,))
        elif bank_id:
            rows = dbs.list_rows(cur, "bank_attempts", "WHERE bank_id=? ORDER BY occurred_at, id", (bank_id,))
        else:
            rows = dbs.list_rows(cur, "bank_attempts", "ORDER BY occurred_at, id")
    finally:
        cur.close()
    return rows


def _latest_by_item(attempts: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每道题**最后一次**作答（按 occurred_at 再按 id 排序，取末条）。"""
    latest: dict[str, dict[str, Any]] = {}
    for a in sorted(attempts, key=lambda r: (str(r.get("occurred_at") or ""), str(r.get("id") or ""))):
        latest[str(a.get("item_id") or "")] = a
    return latest


def _stats_of(items: list[dict[str, Any]], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """纯统计（可单测）：输入题目 + 流水，输出计数/正确率/掌握率/薄弱考点。"""
    total = len(items)
    n_att = len(attempts)
    n_ok = sum(1 for a in attempts if a.get("correct"))
    latest = _latest_by_item(attempts)
    attempted = len(latest)
    mastered = sum(1 for a in latest.values() if a.get("correct"))
    by_topic: dict[str, dict[str, int]] = {}
    item_topic = {str(i.get("id") or ""): str(i.get("topic") or i.get("subtopic") or "未分类")
                  for i in items}
    for a in attempts:
        t = item_topic.get(str(a.get("item_id") or ""), "未分类")
        g = by_topic.setdefault(t, {"attempts": 0, "correct": 0})
        g["attempts"] += 1
        if a.get("correct"):
            g["correct"] += 1
    weak: list[dict[str, Any]] = [
        {"topic": t, "attempts": g["attempts"], "correct": g["correct"],
         "accuracy": round(g["correct"] / g["attempts"], 4) if g["attempts"] else 0.0}
        for t, g in by_topic.items()]
    weak.sort(key=lambda r: (r["accuracy"], -r["attempts"], r["topic"]))
    return {
        "items": total, "attempts": n_att, "correct": n_ok, "attempted": attempted,
        "mastered": mastered,
        "accuracy": round(n_ok / n_att, 4) if n_att else 0.0,
        "mastery": round(mastered / total, 4) if total else 0.0,
        "weak_topics": weak,
    }


def _stats_by_bank(cur: Any) -> dict[str, dict[str, Any]]:
    items_by_bank: dict[str, list[dict[str, Any]]] = {}
    for it in dbs.list_rows(cur, "bank_items"):
        items_by_bank.setdefault(str(it.get("bank_id") or ""), []).append(it)
    att_by_bank: dict[str, list[dict[str, Any]]] = {}
    for a in dbs.list_rows(cur, "bank_attempts"):
        att_by_bank.setdefault(str(a.get("bank_id") or ""), []).append(a)
    out: dict[str, dict[str, Any]] = {}
    for bid in set(items_by_bank) | set(att_by_bank):
        out[bid] = _stats_of(items_by_bank.get(bid, []), att_by_bank.get(bid, []))
    return out


def bank_stats(bid: str) -> dict[str, Any]:
    """题库统计（掌握率/正确率/薄弱考点）。数字全部来自作答流水，纯计算。"""
    ensure_ready()
    return _stats_of(list_items(bid), list_attempts(bank_id=bid))


def today_attempts(date: str = "") -> dict[str, Any]:
    """今日作答汇总（每日目标用）。**题库练习 + 错题重练都计入**（后者由 routers 层
    以 `bank_id=""`、`mode="retry"` 记同一条流水——练习就是练习，不该只算一半）。

    `date` 为空取**本地今天**（`occurred_at` 是本地 `isoformat`，与它同口径比较）。
    **不加查询列/索引**：作答流水按题量级增长（千题×几次），全表读在毫秒量级；
    真到十万级再考虑按日期建索引（那时也该同时做归档）。
    """
    day = (date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    rows = [a for a in list_attempts() if str(a.get("occurred_at") or "").startswith(day)]
    return {
        "date": day,
        "attempts": len(rows),
        "correct": sum(1 for a in rows if a.get("correct")),
        "items": len({str(a.get("item_id") or "") for a in rows}),
    }


def drill_items(bid: str, mode: str = "all", limit: int = 0) -> list[dict[str, Any]]:
    """取一份练习题目集合。

    mode：`all` 全部 / `wrong` 只取「最后一次作答为错」的题 / `undone` 只取没做过的题 /
    `starred` 只取收藏（星标）的题。
    返回题目按原序，附 `_last_correct`（None=未做）供前端标记。
    """
    items = list_items(bid)
    latest = _latest_by_item(list_attempts(bank_id=bid))
    out: list[dict[str, Any]] = []
    for it in items:
        iid = str(it.get("id") or "")
        last = latest.get(iid)
        row = dict(it)
        row["_last_correct"] = (None if last is None else bool(last.get("correct")))
        if mode == "wrong" and (last is None or last.get("correct")):
            continue
        if mode == "undone" and last is not None:
            continue
        if mode == "starred" and not it.get("starred"):
            continue
        out.append(row)
    if limit and limit > 0:
        out = out[:limit]
    return out


# ---------------------------------------------------------------- 判分与回流
def grade(bid: str, item_id: str, user_answer: str, session_id: str = "",
          mode: str = "practice", secs: int = 0,
          sync_mistake: bool = True, confidence: int = 0) -> dict[str, Any]:
    """判一道题：比对答案 → 记流水 + 更新知识点状态 → （判错时）回流错题本。

    ## 判分口径（与押题卷同源，见 `render/qbank_html.py` 的 `answersEqual`）

    剥空白/逗号/顿号后**排序**再比较——`"B D"` 等价 `"BD"`。X 型（多选）同样适用。
    这里**刻意不引入第二种判分规则**：应用内判分与导出押题卷必须同题同判。

    ## 回流

    `sync_mistake=True` 且判错 → 复用 `library.sync_from_paper()`（题面 + `user_answer`），
    与押题卷「同步错题到错题本」走同一条通道 ⇒ 不会绕过任何既有口径。

    ## 学习者状态（v0.22 阶段 3）

    流水与 `kc_state` **同事务**写入（`record_attempt_with_kc`）——**答对也更新状态**，
    不再只在判错时回流。`confidence` 是作答前的自评 1..5（0 = 未采集，允许跳过）。
    """
    ensure_ready()
    item = get_item(item_id)
    if item is None or str(item.get("bank_id") or "") != str(bid or ""):
        raise KeyError("题目不在该题库内")
    correct = answers_equal(user_answer, str(item.get("answer") or ""))
    wrote = record_attempt_with_kc(bid, item_id, user_answer=user_answer,
                                   correct=correct, mode=mode,
                                   session_id=session_id, secs=secs,
                                   confidence=confidence)
    synced = 0
    if not correct and sync_mistake:
        from . import library as lib  # 局部导入：回流通道与押题卷共用
        bank = get_bank(bid) or {}
        q = dict(item)
        q["id"] = str(item.get("id") or "")
        q["user_answer"] = str(user_answer or "")
        # 回流必须带上**题库科目**：否则错题与派生知识点落进「未分类」——
        # 既不便在错题本按科目检索，也会让「铺卡（薄弱点全部入队）」把别处的卡铺进来
        # （2026-10-10 浏览器用例实测踩到：`#rv_body` 里多出「未分类 × …」的卡）。
        q["subject"] = str(item.get("subject") or bank.get("subject") or "")
        q["source_ref"] = {"bank_id": bid, "item_id": item_id}
        synced = lib.sync_from_paper([q], pid=None, source="bank")
    return {
        "correct": correct,
        "answer": str(item.get("answer") or ""),
        "analysis": str(item.get("analysis") or ""),
        "question": str(item.get("question") or ""),
        "options": list(item.get("options") or []),
        "starred": bool(item.get("starred")),
        # 笔记随判分结果回传：练习页在判分前**不展示**笔记（可能写着答案），判完才给
        "note": str(item.get("note") or ""),
        # A-1：出处（真题/模拟… + 年份）随判分结果回传，练习页在解析下方展示
        "source_type": str(item.get("source_type") or ""),
        "source_year": str(item.get("source_year") or ""),
        "synced_mistakes": synced,
        # v0.22 阶段 4：逐选项解析随判分结果回传（旧题为空值；前端有才渲染）
        "option_rationales": dict(item.get("option_rationales") or {}),
        "misconceptions": dict(item.get("misconceptions") or {}),
        "source_quote": str(item.get("source_quote") or ""),
        "difficulty": int(item.get("difficulty") or 0),
        # v0.22 阶段 3：这道题影响了哪些知识点、各自掌握度怎么变（未映射则 mapped=false）
        "kc": wrote["kc"],
        "attempt_id": str(wrote["attempt"].get("id") or ""),
    }


def answers_equal(user: Any, answer: Any) -> bool:
    """答案归一化比较：剥空白/逗号/顿号/分号/斜杠后**排序**比较（集合语义）。

    与 `render/qbank_html.py` 内联 JS 的 `answersEqual()` 同口径：
    `"B D"` == `"BD"` == `"D,B"`；空作答恒不等。
    """
    def _norm(s: Any) -> str:
        t = "".join(ch for ch in str(s or "").upper() if ch.isalnum())
        return "".join(sorted(t))

    u = _norm(user)
    if not u:
        return False
    return u == _norm(answer)
