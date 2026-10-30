"""vault 索引层 —— L2。**可弃缓存，不是真身。**

## 为什么它可弃

真身是 `<vault>/mistakes/**/*.md`（见 `core/vault.py`）。本模块只做两件事：
按内容寻址缓存文件树，以及把 front matter 摊平进 SQL 便于筛选/聚合。
**`DELETE FROM vault_items` 之后全量重建，得到的结果与重建前逐行相等**（幂等）——
这就是"删库 0 丢失"的可断言形式。

## 三态增量重建

```
sha1 比对（mtime/size 只作快路径，不作判据）
  ├─ path 不在索引里        → NEW     解析 → 插入
  ├─ sha1 变                → REPARSE 重新解析 → 更新   ← 用户手改走这条
  ├─ sha1 同                → SKIP    不动
  └─ 索引有、文件没了        → REMOVE  删行（**不动文件**）
```

**判据用 sha1 而不是 mtime**：同步盘（OneDrive/坚果云）会改 mtime 但内容没变；
反过来，用户把内容改回去时 mtime 也会变。只有内容寻址不会骗人。
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import db as dbs
from . import library as lib
from . import vault
from .library import compute_priority, compute_score, compute_state

# 复习调度数学的**单一真相来源**在 core/scheduler.py（ADR-004 重新接线，v0.22.0）：
# FSRS 是默认实现，SM-2 是 legacy。本模块只「取状态 → 调调度器 → 写回文件」。
from .scheduler import SM2Scheduler, get_scheduler

_ITEM_COLS = (
    "path TEXT PRIMARY KEY, sha1 TEXT NOT NULL, mtime REAL, fwarn TEXT, "
    "id TEXT, subject TEXT, chapter TEXT, topic TEXT, tags TEXT, status TEXT, "
    "created TEXT, source TEXT, source_type TEXT, source_year TEXT, bloom TEXT, "
    "miss_count INTEGER, last_tried TEXT, due TEXT, question TEXT, options TEXT, "
    "answer TEXT, analysis TEXT, understanding TEXT, doubts TEXT, reply TEXT, "
    "variant_pass INTEGER, variant_fail INTEGER, variant_last TEXT"
)

# v2 新增列（旧库补列用；`CREATE TABLE IF NOT EXISTS` 不会给已存在的表加列）
_ADD_COLS = (("last_tried", "TEXT"), ("due", "TEXT"),
             ("variant_pass", "INTEGER"), ("variant_fail", "INTEGER"),
             ("variant_last", "TEXT"))

_ENSURE = (
    "CREATE TABLE IF NOT EXISTS vault_items (\n    " + _ITEM_COLS + "\n)",
    "CREATE INDEX IF NOT EXISTS idx_vi_subject ON vault_items(subject, chapter, topic)",
    "CREATE INDEX IF NOT EXISTS idx_vi_status ON vault_items(status)",
)


def ensure_index() -> None:
    """建表 + 补列（幂等）。库未就绪时静默跳过——不阻塞纯文件操作。"""
    try:
        with dbs.tx(write=True) as cur:
            for sql in _ENSURE:
                cur.execute(sql)
            cols = {r[1] for r in cur.execute("PRAGMA table_info(vault_items)").fetchall()}
            for name, decl in _ADD_COLS:
                if name not in cols:
                    cur.execute(f"ALTER TABLE vault_items ADD COLUMN {name} {decl}")
    except sqlite3.Error:
        pass


def relative(path: Path, root: Optional[Path] = None) -> str:
    """索引键 = 相对 vault 根的 posix 路径（换机器/换盘后仍稳定）。"""
    base = Path(root) if root else vault.vault_root()
    try:
        return Path(path).resolve().relative_to(Path(base).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _flatten(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec.get("id", ""), "subject": rec.get("subject", ""),
        "chapter": rec.get("chapter", ""), "topic": rec.get("topic", ""),
        "tags": ", ".join(rec.get("know_tags") or []),
        "status": rec.get("status", "unlearned"), "created": rec.get("created", ""),
        "source": rec.get("source", ""), "source_type": rec.get("source_type", ""),
        "source_year": rec.get("source_year", ""), "bloom": rec.get("bloom", ""),
        "miss_count": int(rec.get("miss_count") or 1),
        "last_tried": rec.get("last_tried", ""), "due": rec.get("due", ""),
        "question": rec.get("question", ""),
        "options": "\n".join(rec.get("options") or []),
        "answer": rec.get("answer", ""), "analysis": rec.get("analysis", ""),
        "understanding": rec.get("my_reasoning", ""), "doubts": rec.get("doubts", ""),
        "reply": rec.get("agent_reply", ""), "fwarn": rec.get("_warn", ""),
        "variant_pass": int(rec.get("variant_pass") or 0),
        "variant_fail": int(rec.get("variant_fail") or 0),
        "variant_last": rec.get("variant_last", ""),
    }


def rebuild(root: Optional[Path] = None, *, progress: Optional[Any] = None) -> dict[str, int]:
    """增量重建。返回 {scanned, added, reparsed, removed, skipped, warnings}。

    **只读 vault**：除 `vault_items` 外不写任何东西。任何单文件解析失败只记
    `fwarn`，不中断、不改用户文件。
    """
    ensure_index()
    base = Path(root) if root else vault.vault_root()
    out = {"scanned": 0, "added": 0, "reparsed": 0, "removed": 0, "skipped": 0, "warnings": 0}
    seen: set[str] = set()
    with dbs.tx(write=True) as cur:
        for path in vault.iter_mistake_files(base):
            rel = relative(path, base)
            seen.add(rel)
            out["scanned"] += 1
            try:
                sha = vault.sha1_file(path)
            except OSError:
                continue                       # 正在被同步盘写：下次重建再收
            row = cur.execute("SELECT sha1 FROM vault_items WHERE path=?", (rel,)).fetchone()
            if row and row["sha1"] == sha:
                out["skipped"] += 1
                continue
            rec = vault.read_record(path)
            flat = _flatten(rec)
            flat["path"] = rel
            flat["sha1"] = sha
            flat["mtime"] = path.stat().st_mtime
            cols = ",".join(flat)
            marks = ",".join("?" for _ in flat)
            cur.execute(f"INSERT OR REPLACE INTO vault_items ({cols}) VALUES ({marks})",
                        tuple(flat.values()))
            if row:
                out["reparsed"] += 1
            else:
                out["added"] += 1
            if flat["fwarn"]:
                out["warnings"] += 1
            if progress:
                progress(out)
        for (old,) in cur.execute("SELECT path FROM vault_items").fetchall():
            if old not in seen:
                cur.execute("DELETE FROM vault_items WHERE path=?", (old,))
                out["removed"] += 1
    return out


def _rows(where: str = "", params: tuple = ()) -> list[dict[str, Any]]:
    ensure_index()
    sql = "SELECT * FROM vault_items" + (f" WHERE {where}" if where else "")
    sql += " ORDER BY created DESC, path ASC"
    with dbs.tx() as cur:
        return [dict(r) for r in cur.execute(sql, params).fetchall()]


def paths_of(mid: str) -> list[str]:
    """按错题 id 反查索引登记的全部 vault 相对路径（错题删除联动用）。

    可能多于一条：题干被编辑过会留下旧 slug 文件（同 id 不同文件名）。
    库未就绪 / 查询失败 → []（调用方按 best-effort 处理）。
    **库未建立时不查**：`dbs.tx()` 会凭空建出 medkit.db，把学习库从 JSON 轨翻到
    SQL 轨（与 `reindex_file` 同款护栏）。
    """
    if not Path(dbs.DB_PATH).exists():
        return []
    try:
        return [str(r["path"]) for r in _rows("id = ?", (str(mid),))]
    except sqlite3.Error:
        return []


def drop_paths(paths: Iterable[str]) -> None:
    """从索引摘除指定相对路径（文件已归档/删除后调用）。失败静默（下次 rebuild 会收编）。
    库未建立时同样不碰 DB（见 `paths_of` 的护栏说明）。"""
    rels = [str(p) for p in (paths or []) if str(p or "").strip()]
    if not rels or not Path(dbs.DB_PATH).exists():
        return
    try:
        ensure_index()
        with dbs.tx(write=True) as cur:
            for rel in rels:
                cur.execute("DELETE FROM vault_items WHERE path=?", (rel,))
    except sqlite3.Error:
        pass


def _like_pattern(term: str) -> str:
    """把用户输入转成安全的 LIKE 模式：**转义**通配符，而不是**剥除**它们。

    旧实现 `.replace('%','').replace('_','')` 会静默改写查询——搜「外科学_vault检索」
    时 `_`（LIKE 的单字符通配）被删掉，模式从此匹配不到含下划线的原文
    （实测浏览器守卫就是这么红的）。转义后 `_`/`%` 按字面量匹配，查询不被篡改。
    """
    e = str(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{e}%"


def list_items(subject: str = "", status: str = "", q: str = "",
               chapter: str = "", topic: str = "") -> list[dict[str, Any]]:
    """错题列表。筛选全可选；`q` 命中题干/解析/我的理解/我的疑问（LIKE，通配符转义）。

    `chapter`/`topic` 供「薄弱项表 → 点一行看这组错题」用（精确等值，与统计的分组口径一致）。
    """
    where, params = [], []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if chapter:
        where.append("chapter = ?")
        params.append(chapter)
    if topic:
        where.append("topic = ?")
        params.append(topic)
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        clause, sp = _search_clause(_search_tokens(q))
        where.append(clause)
        params.extend(sp)
    return _rows(" AND ".join(where), tuple(params))


# 检索命中面：题干/解析/我的理解/我的疑问/答复 + 主题/章节/科目
_SEARCH_FIELDS = ("question", "analysis", "understanding", "doubts", "reply",
                  "topic", "chapter", "subject")


def _search_tokens(q: str) -> list[str]:
    """检索分词：jieba 词 + CJK 二元组（复用 `db.fts_tokens`），按序去重、上限 8 个。

    **必须分词**：模型常一次给出「慢阻肺 病因 感染」这样的多词 query——旧实现把整串当
    **一个**子串去 LIKE，多词必然 0 命中，Agent 于是判「无依据」而拒答（真实 provider 实测踩过）。
    `db.fts_tokens` 自带兜底（jieba 缺失→仅二元组），不抛错，故此处无需再包一层。
    """
    toks: list[str] = []
    for t in dbs.fts_tokens(str(q or "")):
        t = str(t).strip()
        if t and t not in toks:
            toks.append(t)
    if not toks:
        toks = [t for t in re.split(r"\s+", str(q).strip()) if t]
    return toks[:8]


def _search_clause(tokens: list[str]) -> tuple[str, list[str]]:
    """每个词一个「跨字段 OR」组，组间 AND（词全要命中，字段任中其一）。"""
    groups, params = [], []
    for tok in tokens:
        like = _like_pattern(tok)
        groups.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in _SEARCH_FIELDS) + ")")
        params.extend([like] * len(_SEARCH_FIELDS))
    return " AND ".join(groups), params


def get_item(rel_path: str) -> Optional[dict[str, Any]]:
    rows = _rows("path = ?", (rel_path,))
    return rows[0] if rows else None


def count(subject: str = "") -> int:
    where, params = ("subject = ?", (subject,)) if subject else ("", ())
    with dbs.tx() as cur:
        return int(cur.execute(f"SELECT COUNT(*) FROM vault_items {('WHERE ' + where) if where else ''}",
                               params).fetchone()[0])


# ------------------------------------------------------------------ 写回（用户确认后才调）
def update_section(rel_path: str, key: str, text: str, root: Optional[Path] = None) -> dict[str, Any]:
    """改一个文件里的某个小节，写回后只重建那一条。返回 {ok, path, record}。

    **只暴露给"用户确认过"的写入**（我的理解/我的疑问/Agent 的答复）——
    题干/答案/解析不允许在这里改（那是导入侧的事，要改请直接编辑文件）。
    `rel_path` 必须是相对**同一个 root** 的 posix 路径（与 `relative()` 同源）。
    """
    if key not in (vault.SECTION_UNDERSTANDING, vault.SECTION_DOUBTS, vault.SECTION_AGENT_REPLY):
        raise ValueError(f"不允许通过接口修改小节 {key!r}")
    base = Path(root) if root else vault.vault_root()
    path = base / rel_path
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    fm, sec = vault.parse(path.read_text(encoding="utf-8", errors="replace"))
    sec[key] = str(text or "").strip()
    rec = vault.to_record(fm, sec)
    vault._write_atomic(path, vault.serialize(rec))
    _reindex_one(path, base)
    rec = vault.read_record(path)
    rec["path"] = str(path)
    return {"ok": True, "path": str(path), "record": rec}


def reindex_file(path: Path, root: Optional[Path] = None) -> None:
    """单文件增量入索引（公开入口）。供「判错自动补写 vault」后立即让列表可见。

    **库尚未建立时不入索引**：`dbs.tx()` 会凭空建出 `medkit.db`，从而把学习库从 JSON 轨
    翻到 SQL 轨（那时 `mistakes` 表还不存在——旧数据会「消失」，测试里必现）。文件已写好，
    索引留待首次 `rebuild()` 统一补齐即可。
    """
    if not Path(dbs.DB_PATH).exists():
        return
    base = Path(root) if root else vault.vault_root()
    _reindex_one(Path(path), base)


def reconcile_from_library() -> dict[str, int]:
    """把旧库（`library.mistakes`）里 vault 尚无 md 的记录**一次性**补写成文件。

    只做一次（`.medkit-vault-migrated` 存在即跳过）：否则用户手删过的错题会在每次启动
    复活成幽灵条目。返回 {written, skipped}；`skipped=-1` 表示已补过、本次未动作。
    """
    root = vault.vault_root()
    if not vault.is_vault(root):
        vault.init_vault(str(root))
    if vault.is_reconciled(root):
        return {"written": 0, "skipped": -1}
    recs = lib.list_mistakes()
    existing = {str(r.get("id") or "") for r in list_items()}
    written = 0
    for rec in recs:
        if not str(rec.get("question") or "").strip():
            continue
        mid = str(rec.get("id") or "")
        if mid and mid in existing:
            continue
        try:
            create_item(rec, root)
            written += 1
        except Exception:  # noqa: BLE001  单条补写失败不阻断其余（文件层 wrote 幂等）
            continue
    vault.mark_reconciled(root)
    return {"written": written, "skipped": len(recs) - written}


def _reindex_one(path: Path, base: Path) -> None:
    ensure_index()          # 单条写入也必须先保证表在（新建/评分可能发生在首次 rebuild 之前）
    rel = relative(path, base)
    rec = vault.read_record(path)
    flat = _flatten(rec)
    flat["path"] = rel
    flat["sha1"] = vault.sha1_file(path)
    flat["mtime"] = path.stat().st_mtime
    cols, marks = ",".join(flat), ",".join("?" for _ in flat)
    with dbs.tx(write=True) as cur:
        cur.execute(f"INSERT OR REPLACE INTO vault_items ({cols}) VALUES ({marks})",
                    tuple(flat.values()))


# ------------------------------------------------------------------ 统计（L3，零 LLM）
def stats(subject: str = "") -> dict[str, Any]:
    """薄弱项：按 科目/章节/主题 聚合，复用 `library.compute_*`（不重写调度口径）。

    一张表，不给五张卡——EP-01 的五张统计卡实测 0 使用（见设计文档 §4）。

    **掌握度分子取「已掌握条数」而不是「有答案条数」**：这是错题本，几乎每条都带答案
    （标准答案本身就在题干里），拿「有答案」当「做对」会把满是未掌握错题的章节算成
    「较熟练/已掌握」——与语义正相反（实测缺陷，判据见 `test_vault.py`）。
    """
    rows = _rows("subject = ?" if subject else "", (subject,) if subject else ())
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        k = (r.get("subject") or "", r.get("chapter") or "", r.get("topic") or "")
        groups.setdefault(k, []).append(r)

    out: list[dict[str, Any]] = []
    for (sub, ch, tp), items in groups.items():
        total = len(items)
        learned = sum(1 for r in items if r.get("status") == "learned")
        unlearned = total - learned
        miss = sum(int(r.get("miss_count") or 1) for r in items)
        # 「最近一次」= 上次复习时间（`last_tried`）；没有就退回入库时间。
        latest = max((str(r.get("last_tried") or r.get("created") or "") for r in items),
                     default="")
        score = compute_score(learned, total, latest)
        out.append({
            "subject": sub, "chapter": ch, "topic": tp, "total": total,
            "unlearned": unlearned, "miss_count": miss,
            "state": compute_state(score), "score": round(score, 4),
            "priority": round(compute_priority(score, miss, latest), 4),
            "latest": latest,
        })
    out.sort(key=lambda g: -g["priority"])
    return {"total": len(rows),
            "unlearned": sum(1 for r in rows if r.get("status") != "learned"),
            "groups": out}


def subjects() -> list[str]:
    with dbs.tx() as cur:
        return [r[0] for r in cur.execute(
            "SELECT DISTINCT subject FROM vault_items WHERE subject <> '' "
            "ORDER BY subject").fetchall()]


# ------------------------------------------------------------------ 新建（直接成 md）
def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_iso() -> str:
    return date.today().isoformat()


def create_item(rec: dict[str, Any], root: Optional[Path] = None) -> dict[str, Any]:
    """新建一条错题：补 id/时间/待复习 → 原子写文件 → 增量入索引。返回带 `path` 的记录。"""
    base = Path(root) if root else vault.vault_root()
    rec = dict(rec)
    if not str(rec.get("id") or "").strip():
        rec["id"] = vault.new_id()
    if not rec.get("created_at"):
        rec["created_at"] = _now_iso()
    if not str(rec.get("status") or "").strip():
        rec["status"] = "unlearned"
    rec.setdefault("due", _today_iso())        # 新建即可复习
    rec.setdefault("state", "new")
    path = vault.write_record(rec, base)
    _reindex_one(path, base)
    out = vault.read_record(path)
    out["path"] = relative(path, base)
    return out


# ------------------------------------------------------------------ 复习调度（本域只调用，不实现）
# 2026-10-15（奥卡姆精简 T2）：复习卡域（`cards` / `review` / `scheduler`）零使用被删后，
#   SM-2 曾被搬进本模块（「一个算法一份实现」）。
# 2026-10-27（v0.22.0 · ADR-004 重新接线）：**SM-2 与 FSRS 的数学统一搬到 core/scheduler.py**
#   ——调度算法从此有第二个实现，「一个算法一份实现」必须升级成「一类算法一份实现」。
#   本模块只负责「取状态 → 调调度器 → 写回文件」，不再自己算间隔。
#   SM-2 路径的**行为逐值不变**（`tests/test_scheduler.py` 用参数化黄金值钉死）。
def _grade_card_of(rec: dict[str, Any], quality: int) -> dict[str, Any]:
    """按 quality(0~5) 推进一条错题的 SM-2 状态，返回要写回文件的字段。

    保留此函数是为了**兼容与可测**：它是 SM-2 legacy 路径的纯函数入口，
    内部转调 `core/scheduler.SM2Scheduler`（数学的单一出处）。
    新代码请直接用 `get_scheduler().schedule(...)`。
    """
    return SM2Scheduler().schedule(rec, quality)


def due_items(subject: str = "") -> list[dict[str, Any]]:
    """今日到期的错题（没有 due 的视为立即到期，即刚新建/刚迁移的）。"""
    where = "(due IS NULL OR due = '' OR due <= ?)"
    params: list[Any] = [_today_iso()]
    if subject:
        where += " AND subject = ?"
        params.append(subject)
    rows = _rows(where, tuple(params))
    rows.sort(key=lambda r: (str(r.get("due") or ""), str(r.get("created") or "")))
    return rows


def review_stats(subject: str = "") -> dict[str, int]:
    rows = _rows("subject = ?" if subject else "", (subject,) if subject else ())
    today = _today_iso()
    return {"total": len(rows),
            "due": sum(1 for r in rows if (r.get("due") or "") <= today)}


# 复习评分写回的字段（两种调度器共用一套 vault 语义）
_CARD_FIELDS = ("due", "state", "ease", "interval", "reps", "lapses",
                "stability", "difficulty", "fsrs_state", "fsrs_step", "last_review")


def grade_item(rel_path: str, quality: int, root: Optional[Path] = None) -> dict[str, Any]:
    """写回一次复习结果：调度器推进 → 原子写文件 → 增量入索引。返回更新后的记录。

    调度器由配置 `scheduler` 决定（默认 FSRS，SM-2 为 legacy），
    详见 `core/scheduler.py` 的模块 docstring（参数标定与字段职责）。

    掌握度收敛（**两套算法下完全一致**）：答错 → `status=unlearned`；
    **连续记住两次**（reps≥2）→ `status=learned`，于是「复习」会真的改变薄弱项表（闭环）。
    """
    base = Path(root) if root else vault.vault_root()
    path = base / rel_path
    if not path.is_file():
        raise FileNotFoundError(rel_path)
    rec = vault.read_record(path)
    q = max(0, min(int(quality), 5))
    sch = get_scheduler()
    card = sch.schedule(rec, q)
    rec["last_tried"] = _now_iso()
    for k in _CARD_FIELDS:
        if k in card:
            rec[k] = card[k]
    if q < 3:
        rec["status"] = "unlearned"
    elif int(card["reps"]) >= 2:
        rec["status"] = "learned"
    vault._write_atomic(path, vault.serialize(rec))   # 就地改写，保住用户文件名
    _reindex_one(path, base)
    out = vault.read_record(path)
    out["path"] = relative(path, base)
    out["scheduler"] = sch.name
    return out
