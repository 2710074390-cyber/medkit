"""M3：教材切片索引 + 讲解产物存储（个人学习库的学习讲解资产）。

- slice_index.json：把各项目的 textbook 切片按科目汇总（跨项目共享），供讲解/检索 grounding。
- explains.json：讲解产物（一次生成持久化为可查看/按科目归档/导出 md 的资产，借鉴 WQN subject 组织）。
零向量库；原子写复用 fsutil.write_json_atomic；仅生成时才调 LLM（在 agent 层）。

存储：~/.medkit/library/{explains.json, slice_index.json}
"""

import json
import re
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from . import config as cfg
from . import db as dbs
from .fsutil import read_json_list, write_json_atomic
from .library import LIBRARY_DIR

SLICE_INDEX_FILE = LIBRARY_DIR / "slice_index.json"
EXPLAINS_FILE = LIBRARY_DIR / "explains.json"

# SQL 模式（S0·方案 §2.3）：medkit.db 存在即行级事务（BEGIN IMMEDIATE 串行读-改-写）。
DB_FILE = dbs.DB_PATH
_LOCK = threading.RLock()
_E_COLS = ("subject", "kp_name", "created_at")


def _store_is_sql(path: Path) -> bool:
    return path == EXPLAINS_FILE and DB_FILE.exists()


def _load(path: Path) -> list[dict[str, Any]]:
    """读 JSON 数组；缺失/损坏 → 空。SQL 模式读 explains 表。"""
    if _store_is_sql(path):
        conn = dbs.get_conn()
        cur = conn.cursor()
        try:
            return dbs.list_rows(cur, "explains")
        finally:
            cur.close()
    return read_json_list(path)


def _save(path: Path, data: list[dict[str, Any]]) -> None:
    """写 JSON 数组；SQL 模式事务整组替换。"""
    if _store_is_sql(path):
        with dbs.tx(write=True) as cur:
            dbs.replace_all(cur, "explains", data, _E_COLS)
        return
    write_json_atomic(path, data)


@contextmanager
def _store() -> Iterator[dict[str, Any]]:
    """讲解产物读-改-写视图；退出时按 dirty 写回（SQL 单事务 / JSON RLock+原子写）。"""
    if _store_is_sql(EXPLAINS_FILE):
        with dbs.tx(write=True) as cur:
            st: dict[str, Any] = {"recs": dbs.list_rows(cur, "explains"),
                                  "cur": cur, "dirty": False}
            yield st
            if st["dirty"]:
                dbs.replace_all(cur, "explains", st["recs"], _E_COLS)
        return
    with _LOCK:
        st = {"recs": list(_load(EXPLAINS_FILE)), "cur": None, "dirty": False}
        yield st
        if st["dirty"]:
            _save(EXPLAINS_FILE, st["recs"])

# 讲解注入上限：命中切片/网络素材都裁剪，控成本（对齐出题管线 A6 全文仅注入一次）
SLICE_INJECT_LIMIT = 2       # ≤2 个命中切片
SLICE_TEXT_LIMIT = 900       # 每个切片注入 ≤900 字
WEB_MATERIALS_LIMIT = 4      # 联网补充素材 ≤4 条
WEB_SNIPPET_LIMIT = 240

# 惰性解析项目根（测试可整体替换）；cfg.load 在测试导入期可能未初始化 → 不用模块级常量
_PROJ_ROOT: Optional[Path] = None


def _proj_root() -> Path:
    if _PROJ_ROOT is not None:
        return _PROJ_ROOT
    return Path(cfg.load().get("projects_dir") or (cfg.CONFIG_DIR / "projects"))


# ---------------------------------------------------------------- 切片索引（按科目）
def _load_index() -> dict[str, Any]:
    try:
        data = json.loads(SLICE_INDEX_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("subjects"), dict):
            data.setdefault("scanned_at", None)
            return data
    except Exception:  # noqa: BLE001
        pass
    return {"subjects": {}, "scanned_at": None}


# D-13：按 (文件, mtime, size) 做内存缓存——文件写入后 mtime 变化自动失效重建；
# 命中时不再全量重扫项目目录 + FTS5 重建（讲解/提问/复习「查看提示」每请求都调用）。
_INDEX_CACHE: dict[str, Any] = {"key": None, "data": None}


def _index_cache_key() -> Optional[tuple[tuple[str, int, int], ...]]:
    """项目切片/元数据文件的 (路径, mtime_ns, size) 快照；无可索引文件 → None。"""
    root = _proj_root()
    parts: list[tuple[str, int, int]] = []
    if root.is_dir():
        for proj in sorted(root.iterdir(), key=lambda p: p.name):
            if not proj.is_dir():
                continue
            for name in ("slices.json", "meta.json"):
                f = proj / name
                if f.exists():
                    try:
                        st = f.stat()
                        parts.append((str(f), st.st_mtime_ns, st.st_size))
                    except OSError:
                        pass
    return tuple(parts) if parts else None


def index_slices(force: bool = False) -> dict[str, Any]:
    """扫描 ~/.medkit/projects/* 的 meta.subject + textbook 切片，重建按科索引。

    D-13：命中 (slices.json, meta.json) 的 mtime 缓存时直接复用，不重扫、不重建 FTS；
    文件写入后 mtime 变化自动失效重建。
    """
    key = _index_cache_key()
    if not force and _INDEX_CACHE["data"] is not None and _INDEX_CACHE["key"] == key:
        return _INDEX_CACHE["data"]
    index: dict[str, list[dict[str, Any]]] = {}
    pattern = re.compile(r"[^a-zA-Z0-9\u4e00-\u9fff]")
    root = _proj_root()
    if root.is_dir():
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            slices_path = proj / "slices.json"
            meta_path = proj / "meta.json"
            if not slices_path.exists():
                continue
            subject = ""
            if meta_path.exists():
                try:
                    subject = (json.loads(meta_path.read_text(encoding="utf-8"))
                               .get("subject", "") or "").strip()
                except Exception:  # noqa: BLE001
                    subject = ""
            if not subject:
                subject = "未分类"
            try:
                slices = json.loads(slices_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for s in slices:
                if s.get("role") != "textbook":
                    continue
                text = s.get("text") or ""
                if not text.strip():
                    continue
                index.setdefault(subject, []).append({
                    "pid": proj.name, "sid": s.get("sid", ""),
                    "title": s.get("title") or "", "source": s.get("source") or "",
                    "page": s.get("page") or "", "text": text[:2000],
                    "_norm": pattern.sub("", f"{s.get('title')} {text[:1500]}")
                    .replace(" ", "").lower(),
                })
    for lst in index.values():
        lst.sort(key=lambda x: x["sid"])
    # IMP-06：FTS5 辅表同步重建（仅 SQL 模式生效；失败不影响 JSON 索引主路径）
    try:
        dbs.reindex_slices([{"subject": subj, "text": s["text"], "title": s.get("title") or ""}
                            for subj, lst in index.items() for s in lst])
    except Exception:  # noqa: BLE001
        pass
    out = {"subjects": index, "scanned_at": datetime.now().isoformat(timespec="seconds")}
    write_json_atomic(SLICE_INDEX_FILE, out)
    _INDEX_CACHE["key"] = key
    _INDEX_CACHE["data"] = out
    return out


def _sig(query: str) -> str:
    """检索签名：纯中英数字、无空格、小写（供 bigram 命中计分）。"""
    return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fff]", "", query or "").lower()


def _retrieve_hits(cand: list[dict[str, Any]], sig: str) -> list[dict[str, Any]]:
    """按 bigram 命中打分（实体/错题题干无需连续子串匹配）。"""
    if not sig or len(sig) < 1:
        return cand
    grams = {sig[i:i + 2] for i in range(len(sig) - 1)} or {sig}
    scored: list[tuple[int, dict[str, Any]]] = []
    for s in cand:
        n = s.get("_norm", "")
        hit = sum(n.count(g) for g in grams)
        if hit > 0:
            scored.append((hit, s))
    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored]


def fts_search(subject: str, query: str, k: int = 5,
               cand: Optional[list[dict[str, Any]]] = None) -> Optional[list[dict[str, Any]]]:
    """FTS5+jieba 检索（IMP-06，K1 验证）：按 BM25 返回排序切片；不可用/无命中 → None（调用方回退）。

    cand 提供时用于把 FTS 命中的 text 回映射成完整切片记录（含 pid/sid/title/source/page），
    返回值与既有 retrieve 的切片 dict 同构。subject 给定时按科目过滤（UNINDEXED 列等值）。
    """
    try:
        if not dbs.DB_PATH.exists():
            return None
        expr = dbs.fts_match_expr(query or "")
        if not expr:
            return None
        conn = dbs.get_conn()
        cur = conn.cursor()
        try:
            if subject:
                rows = cur.execute(
                    "SELECT text FROM slices_fts WHERE subject=? AND slices_fts MATCH ? "
                    "ORDER BY bm25(slices_fts, 0.0, 0.2, 1.0) LIMIT ?",
                    (subject, expr, max(k * 6, 30))).fetchall()
            else:
                rows = cur.execute(
                    "SELECT text FROM slices_fts WHERE slices_fts MATCH ? "
                    "ORDER BY bm25(slices_fts, 0.0, 0.2, 1.0) LIMIT ?",
                    (expr, max(k * 6, 30))).fetchall()
        finally:
            cur.close()
    except Exception:  # noqa: BLE001  FTS 未初始化/查询语法异常 → 回退到 bigram top-k
        return None
    texts = [r[0] for r in rows if r[0]]
    if not texts:
        return None
    if cand is None:
        return texts[:max(k, 1)]
    by_text = {s.get("text", ""): s for s in cand}
    out = [by_text[t] for t in texts if t in by_text]
    return out[:max(k, 1)] or None


def retrieve(pid_ignore: Optional[list[str]] = None, subject: str = "",
             query: str = "", k: int = SLICE_INJECT_LIMIT) -> list[dict[str, Any]]:
    """命中检索（IMP-06 双轨）：FTS5+jieba 优先（SQL 模式），回退 bigram 命中排序；返回去掉 _norm 的切片。"""
    index = _load_index()
    groups = index.get("subjects", {})
    if not groups:
        return []
    cand = groups.get(subject) or groups.get("未分类") or []
    if not cand:
        # 科目不匹配 → 全库按关键词兜底
        cand = [s for lst in groups.values() for s in lst]
    hits = fts_search(subject, query, max(k, 1), cand)
    if hits is None:
        hits = _retrieve_hits(cand, _sig(query))
    chosen = hits[: max(k, 1)]
    return [{kk: vv for kk, vv in s.items() if kk != "_norm"} for s in chosen]


def slice_text_of(hits: list[dict[str, Any]]) -> str:
    """命中切片 → 注入文本（【源】标注 + 截断）。"""
    lines: list[str] = []
    for i, s in enumerate(hits, 1):
        src = " · ".join(x for x in (s.get("source"), s.get("page")) if x)
        tag = s.get("title") or s.get("sid") or f"切片{i}"
        head = f"【教材切片 {tag}】来自 {s.get('pid', '')}/{s.get('sid', '')}" + (f"（{src}）" if src else "")
        text = (s.get("text") or "")[:SLICE_TEXT_LIMIT]
        lines.append(f"{head}\n{text}")
    return "\n\n".join(lines)


# ---------------------------------------------------------------- 讲解产物
def list_explains(subject: str = "") -> list[dict[str, Any]]:
    recs = _load(EXPLAINS_FILE)
    if subject:
        recs = [r for r in recs if r.get("subject") == subject]
    recs.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return recs


def get_explain(eid: str) -> Optional[dict[str, Any]]:
    return next((r for r in _load(EXPLAINS_FILE) if r.get("id") == eid), None)


def save_explain(rec: dict[str, Any]) -> dict[str, Any]:
    with _store() as st:
        recs = st["recs"]
        recs = [r for r in recs if r.get("id") != rec.get("id")]
        recs.append(rec)
        st["recs"] = recs
        st["dirty"] = True
    return rec


def delete_explain(eid: str) -> bool:
    with _store() as st:
        recs = st["recs"]
        new = [r for r in recs if r.get("id") != eid]
        if len(new) == len(recs):
            return False
        st["recs"] = new
        st["dirty"] = True
    return True


def delete_by_subject(subject: str) -> int:
    """WP-4：删除某科目的全部讲解产物（空科目名不删「未分类」），返回删除数量。"""
    if not subject:
        return 0
    with _store() as st:
        recs = st["recs"]
        remain = [r for r in recs if r.get("subject") != subject]
        n = len(recs) - len(remain)
        if n:
            st["recs"] = remain
            st["dirty"] = True
    return n


def export_subject_md(subject: str = "") -> str:
    """当前科目全部讲解产物 → 合并 markdown（充当『个人复习手册』）。"""
    recs = list_explains(subject)
    if not recs:
        return ""
    lines = [f"# 学习讲解手册{subject and f' · {subject}' or ''}", ""]
    lines.append(f"> 共 {len(recs)} 篇讲解 · 由 MedKit「学习中心」生成")
    lines.append("")
    for i, r in enumerate(recs, 1):
        lines.append(f"## {i}. {r.get('kp_name') or '未命名知识点'}")
        lines.append(f"**科目**：{r.get('subject') or '-'} · **生成时间**：{r.get('created_at', '')}")
        if r.get("via_web"):
            lines.append("**来源**：教材切片 + 网络补充")
        else:
            lines.append("**来源**：教材切片")
        lines.append("")
        lines.append(r.get("content") or "")
        lines.append("")
    return "\n".join(lines)
