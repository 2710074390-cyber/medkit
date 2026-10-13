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

import sqlite3
from pathlib import Path
from typing import Any, Optional

from . import db as dbs
from . import vault
from .library import compute_priority, compute_score, compute_state

_ITEM_COLS = (
    "path TEXT PRIMARY KEY, sha1 TEXT NOT NULL, mtime REAL, fwarn TEXT, "
    "id TEXT, subject TEXT, chapter TEXT, topic TEXT, tags TEXT, status TEXT, "
    "created TEXT, source TEXT, source_type TEXT, source_year TEXT, bloom TEXT, "
    "miss_count INTEGER, question TEXT, options TEXT, answer TEXT, analysis TEXT, "
    "understanding TEXT, doubts TEXT, reply TEXT"
)

_ENSURE = (
    "CREATE TABLE IF NOT EXISTS vault_items (\n    " + _ITEM_COLS + "\n)",
    "CREATE INDEX IF NOT EXISTS idx_vi_subject ON vault_items(subject, chapter, topic)",
    "CREATE INDEX IF NOT EXISTS idx_vi_status ON vault_items(status)",
)


def ensure_index() -> None:
    """建表（幂等）。库未就绪时静默跳过——不阻塞纯文件操作。"""
    try:
        with dbs.tx(write=True) as cur:
            for sql in _ENSURE:
                cur.execute(sql)
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
        "question": rec.get("question", ""),
        "options": "\n".join(rec.get("options") or []),
        "answer": rec.get("answer", ""), "analysis": rec.get("analysis", ""),
        "understanding": rec.get("my_reasoning", ""), "doubts": rec.get("doubts", ""),
        "reply": rec.get("agent_reply", ""), "fwarn": rec.get("_warn", ""),
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


def _like_pattern(term: str) -> str:
    """把用户输入转成安全的 LIKE 模式：**转义**通配符，而不是**剥除**它们。

    旧实现 `.replace('%','').replace('_','')` 会静默改写查询——搜「外科学_vault检索」
    时 `_`（LIKE 的单字符通配）被删掉，模式从此匹配不到含下划线的原文
    （实测浏览器守卫就是这么红的）。转义后 `_`/`%` 按字面量匹配，查询不被篡改。
    """
    e = str(term).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{e}%"


def list_items(subject: str = "", status: str = "", q: str = "") -> list[dict[str, Any]]:
    """错题列表。筛选全可选；`q` 命中题干/解析/我的理解/我的疑问（LIKE，通配符转义）。"""
    where, params = [], []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if status:
        where.append("status = ?")
        params.append(status)
    if q:
        like = _like_pattern(q)
        where.append("(question LIKE ? ESCAPE '\\' OR analysis LIKE ? ESCAPE '\\' "
                     "OR understanding LIKE ? ESCAPE '\\' OR doubts LIKE ? ESCAPE '\\' "
                     "OR reply LIKE ? ESCAPE '\\')")
        params.extend([like] * 5)
    return _rows(" AND ".join(where), tuple(params))


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


def _reindex_one(path: Path, base: Path) -> None:
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
        latest = max((str(r.get("created") or "") for r in items), default="")
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
