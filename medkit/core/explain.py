"""M3：教材切片索引 + 讲解产物存储（个人学习库的学习讲解资产）。

- slice_index.json：把各项目的 textbook 切片按科目汇总（跨项目共享），供讲解/检索 grounding。
- explains.json：讲解产物（一次生成持久化为可查看/按科目归档/导出 md 的资产，借鉴 WQN subject 组织）。
零向量库；原子写复用 fsutil.write_json_atomic；仅生成时才调 LLM（在 agent 层）。

存储：~/.medkit/library/{explains.json, slice_index.json}
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import config as cfg
from .fsutil import write_json_atomic
from .library import LIBRARY_DIR, _load, _save

SLICE_INDEX_FILE = LIBRARY_DIR / "slice_index.json"
EXPLAINS_FILE = LIBRARY_DIR / "explains.json"

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


def index_slices() -> dict[str, Any]:
    """扫描 ~/.medkit/projects/* 的 meta.subject + textbook 切片，重建按科索引。"""
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
    out = {"subjects": index, "scanned_at": datetime.now().isoformat(timespec="seconds")}
    write_json_atomic(SLICE_INDEX_FILE, out)
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


def retrieve(pid_ignore: Optional[list[str]] = None, subject: str = "",
             query: str = "", k: int = SLICE_INJECT_LIMIT) -> list[dict[str, Any]]:
    """命中检索：优先 subject 且按 bigram 命中排序；返回去掉 _norm 的切片。"""
    index = _load_index()
    groups = index.get("subjects", {})
    if not groups:
        return []
    cand = groups.get(subject) or groups.get("未分类") or []
    if not cand:
        # 科目不匹配 → 全库按关键词兜底
        cand = [s for lst in groups.values() for s in lst]
    cand = _retrieve_hits(cand, _sig(query))
    chosen = cand[: max(k, 1)]
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
    recs = _load(EXPLAINS_FILE)
    recs = [r for r in recs if r.get("id") != rec.get("id")]
    recs.append(rec)
    _save(EXPLAINS_FILE, recs)
    return rec


def delete_explain(eid: str) -> bool:
    recs = _load(EXPLAINS_FILE)
    new = [r for r in recs if r.get("id") != eid]
    if len(new) == len(recs):
        return False
    _save(EXPLAINS_FILE, new)
    return True


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
