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
- 「何时该复习」一律交给既有的知识点级调度（`review` / `cards`），**题库域不实现第二套 SM-2/FSRS**
  （两套调度器并存是维护灾难）。`bank_attempts` 只回答「这道题做过几次、对了几次」。
"""

from __future__ import annotations

import itertools
import time
from datetime import datetime
from typing import Any, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import write_json_atomic

_BANK_COLS = ("name", "subject", "source", "created_at", "updated_at")
_ITEM_COLS = ("bank_id", "qtype", "topic", "answer", "origin", "created_at")
_ATT_COLS = ("bank_id", "item_id", "session_id", "correct", "mode", "occurred_at")
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
                origin: str = "manual") -> dict[str, Any]:
    """建题库（可同时灌入题目）。返回落库后的题库记录（含 items 计数）。"""
    ensure_ready()
    name = (name or "").strip() or "未命名题库"
    bid = _new_id("bk")
    now = _now()
    rec = {"id": bid, "name": name, "subject": (subject or "").strip(),
           "source": (source or "").strip(), "created_at": now, "updated_at": now}
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, "banks", rec, _BANK_COLS)
    if items:
        add_items(bid, items, origin=origin)
    return get_bank(bid) or rec


def update_bank(bid: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    ensure_ready()
    allowed = {"name", "subject", "source"}
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
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM bank_items WHERE bank_id=?", (bid,))
        cur.execute("DELETE FROM bank_attempts WHERE bank_id=?", (bid,))
    stats = add_items(bid, rows, origin="md_import", dedupe="new")
    save_source(bid, raw, fmt, bank_iq.PARSER_VERSION)   # 记下本次用的解析器版本
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


def add_items(bid: str, items: list[dict[str, Any]], origin: str = "md_import",
              dedupe: str = "update") -> dict[str, Any]:
    """批量入题。dedupe ∈ {update, skip, new}（对应 Anki 的重复项三选一）。

    单条失败不阻断整批（照 `library.batch_add` 的模式）。返回 {added, updated, skipped, errors}。
    """
    ensure_ready()
    from . import bank_iq  # 局部导入：避免 core 内循环依赖（bank_iq 会 import library）

    stats: dict[str, Any] = {"added": 0, "updated": 0, "skipped": 0, "errors": []}
    if not items:
        return stats
    if dedupe not in ("update", "skip", "new"):
        dedupe = "update"
    existing = list_items(bid)
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
def record_attempt(bank_id: str, item_id: str, user_answer: str = "",
                   correct: bool = False, mode: str = "practice",
                   session_id: str = "", secs: int = 0) -> dict[str, Any]:
    """记一条作答流水（append-only）。返回落库记录。"""
    ensure_ready()
    rec = {
        "id": _new_id("at"),
        "bank_id": str(bank_id or ""), "item_id": str(item_id or ""),
        "session_id": str(session_id or ""), "user_answer": str(user_answer or ""),
        "correct": bool(correct), "mode": str(mode or "practice"),
        "secs": int(secs or 0), "occurred_at": _now(),
    }
    with dbs.tx(write=True) as cur:
        dbs.put_row(cur, "bank_attempts", rec, _ATT_COLS)
    return rec


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
          sync_mistake: bool = True) -> dict[str, Any]:
    """判一道题：比对答案 → 记流水 → （判错时）回流错题本。返回判分结果。

    ## 判分口径（与押题卷同源，见 `render/qbank_html.py` 的 `answersEqual`）

    剥空白/逗号/顿号后**排序**再比较——`"B D"` 等价 `"BD"`。X 型（多选）同样适用。
    这里**刻意不引入第二种判分规则**：应用内判分与导出押题卷必须同题同判。

    ## 回流

    `sync_mistake=True` 且判错 → 复用 `library.sync_from_paper()`（题面 + `user_answer`），
    与押题卷「同步错题到错题本」走同一条通道 ⇒ 不会绕过任何既有口径。
    """
    ensure_ready()
    item = get_item(item_id)
    if item is None or str(item.get("bank_id") or "") != str(bid or ""):
        raise KeyError("题目不在该题库内")
    correct = answers_equal(user_answer, str(item.get("answer") or ""))
    record_attempt(bid, item_id, user_answer=user_answer, correct=correct,
                   mode=mode, session_id=session_id, secs=secs)
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
