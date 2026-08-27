"""WP-01 大纲覆盖度引擎（考试锚定，《结构化执行方案》§3 WP-01）。

- 存储：SQLite `syllabus_items` 表（迁移 v2：kind=chapter 章行 / kind=item 考点行）。
- 种子：`medkit/data/syllabus_seed_306.json`（由 docs/spikes/build_syllabus_seed.py 从
  GoldenSet 真题 + 知识库素材教材 chunks 元数据构建；ensure_seed 幂等导入）。
- 零 LLM 原则：条目解析（本地规则）/ 覆盖判定（本地匹配）/ 报告（md）全本地；
  仅「粘贴任文献 → 结构化草稿」的增强版才走 LLM（chat_json，见 routers/syllabus.py parse 的
  LLM 增强分支，默认规则路径零成本）。
- 覆盖口径：条目文本与学习库「知识点名/错题主题」匹配 →
  matched（covered）/ mastered（匹配的知识点 state ∈ solid|mastered）/ pending（未覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Optional

from . import db as dbs

SEED_FILE = Path(__file__).resolve().parents[2] / "data" / "syllabus_seed_306.json"

# 已知科目名（用于粘贴解析的主题探测）
KNOWN_SUBJECTS = [
    "生理学", "生物化学", "病理学", "内科学", "外科学", "诊断学",
    "临床医学人文精神", "医学心理学", "医学伦理学", "儿科学", "神经病学",
    "精神病学", "中医学", "中医心理学", "医患沟通", "皮肤性病学", "影像学",
    "认知神经科学", "药理学", "医学遗传学", "卫生法规",
]

_MASTERED_STATES = ("solid", "mastered")

_CH_RE = re.compile(r"^\s*(?:第\s*[0-9一二三四五六七八九十百零]+\s*[章节篇]\s*|"
                    r"(?:[一二三四五六七八九十百零]+|[（(][一二三四五六七八九十百零]+[）)])\s*[、.．])\s*(.+?)\s*$")
# 章 = 「第x章」或「中文数字+、」；条目 = 「阿拉伯数字+、」或「（x）/（一）」——中文数字单独占行视为章
_IT_RE = re.compile(r"^\s*(\d+|[（(][0-9一二三四五六七八九十百零]+[）)])\s*[、.．]\s*(.+?)\s*$")


# ---------------------------------------------------------------- 种子（幂等导入）
def _row_id(subject: str, chapter: str, item: str, kind: str, source: str = "seed") -> str:
    return hashlib.sha1(f"{source}|{kind}|{subject}|{chapter}|{item}".encode("utf-8")).hexdigest()[:16]


def ensure_seed(force: bool = False) -> dict[str, Any]:
    """把 bundled 种子导入 syllabus_items（幂等：已有同 id 跳过）。返回统计。"""
    dbs.migrate()
    if not SEED_FILE.exists():
        return {"imported": 0, "note": "seed missing"}
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    with dbs.tx(write=True) as cur:
        if force:
            # 重建种子：删除旧 seed 行（用户粘贴行 source=paste 不受影响）
            cur.execute("DELETE FROM syllabus_items WHERE source='seed'")
        rows = []
        for subj in seed.get("subjects", []):
            subject = subj.get("name", "")
            for ch in subj.get("chapters", []):
                chapter = ch.get("name", "")
                if not chapter:
                    continue
                if force or not cur.execute(
                        "SELECT 1 FROM syllabus_items WHERE id=?",
                        (_row_id(subject, chapter, "", "chapter", "seed"),)).fetchone():
                    rows.append({"id": _row_id(subject, chapter, "", "chapter", "seed"),
                                 "subject": subject, "chapter": chapter, "kind": "chapter",
                                 "item": "", "weight": 1.0, "source": "seed"})
                for it in ch.get("items", []):
                    if not it:
                        continue
                    if force or not cur.execute(
                            "SELECT 1 FROM syllabus_items WHERE id=?",
                            (_row_id(subject, chapter, it, "item", "seed"),)).fetchone():
                        rows.append({"id": _row_id(subject, chapter, it, "item", "seed"),
                                     "subject": subject, "chapter": chapter, "kind": "item",
                                     "item": it, "weight": 1.0, "source": "seed"})
        for r in rows:
            dbs.put_row(cur, "syllabus_items", r,
                        ("subject", "chapter", "kind", "item", "weight", "source"))
    return {"imported": len(rows),
            "exam": seed.get("exam"),
            "note": seed.get("note", ""),
            "subjects": sum(1 for s in seed.get("subjects", []))}


def seed_info() -> dict[str, Any]:
    """种子文件元信息（前端展示来源/说明）。"""
    if not SEED_FILE.exists():
        return {}
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    return {"exam": seed.get("exam"), "sources": seed.get("sources"),
            "note": seed.get("note"), "gs_subject_counts": seed.get("gs_subject_counts")}


# ---------------------------------------------------------------- 本地规则解析（粘贴任文献 → 草稿）
def parse_text(text: str, subject: str = "") -> list[dict[str, str]]:
    """把粘贴的「分章节条目」文本解析为 [subject, chapter, item] 草稿（零 LLM）。

    识别规则（宽松，供预览）：章 = 「一、xx / 第一章 xx / （一）xx」；条目 = 「1、xx / (2) xx」。
    科目：显式传参优先；否则从首行/已知科目名探测。
    """
    drafts: list[dict[str, str]] = []
    chapter = ""
    cur_subject = subject or ""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if not cur_subject:
            for name in KNOWN_SUBJECTS:
                if line.startswith(name) and len(line) <= len(name) + 4:
                    cur_subject = name
                    break
        m = _CH_RE.match(line)
        if m and not _IT_RE.match(line):
            candidate = m.group(1).strip()
            if not re.match(r"^\d+$", candidate):
                chapter = candidate
                continue
        m2 = _IT_RE.match(line)
        if m2:
            item = m2.group(2).strip()
            if len(item) >= 2:
                drafts.append({"subject": cur_subject, "chapter": chapter, "item": item})
    return drafts


# ---------------------------------------------------------------- 查询与覆盖
def _now() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _rows(subject: str = "", source: str = "all") -> list[dict[str, Any]]:
    where, params = [], []
    if subject:
        where.append("subject = ?")
        params.append(subject)
    if source != "all":
        where.append("source = ?")
        params.append(source)
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    order = "ORDER BY subject, chapter, kind, item"
    with dbs.tx(write=True) as cur:
        return dbs.list_rows(cur, "syllabus_items", f"{cond} {order}", tuple(params))


def chapter_items_text(subject: str = "", limit: int = 800) -> str:
    """科目大纲条目 → 注入文本（≤limit 字；章标题 + 条目，供出题 subtopic 对齐）。"""
    data = coverage(subject)
    lines: list[str] = []
    for ch in data["chapters"]:
        items = [it["item"] for it in ch["items"]]
        if not items:
            continue
        lines.append(f"· {ch['chapter']}：{'、'.join(items[:8])}")
        if sum(len(x) for x in lines) > limit:
            break
    return "\n".join(lines)[:limit]


def list_subjects(source: str = "all") -> list[dict[str, Any]]:
    """有大纲条目的科目清单（含章/条目计数；source 限定 all|seed|teacher）。"""
    where, params = "", ()
    if source != "all":
        where, params = "WHERE source = ?", (source,)
    with dbs.tx(write=True) as cur:
        rows = cur.execute(
            "SELECT subject, COUNT(*) AS n, "
            "SUM(CASE WHEN kind='chapter' THEN 1 ELSE 0 END) AS chapters, "
            "SUM(CASE WHEN kind='item' THEN 1 ELSE 0 END) AS items "
            f"FROM syllabus_items {where} GROUP BY subject ORDER BY subject", params).fetchall()
    return [{"subject": r[0], "total": r[1], "chapters": r[2], "items": r[3]} for r in rows]


# ---------------------------------------------------------------- 教师重点为纲（WP-01 扩展）
_BULLET_RE = re.compile(r"^\s*(?:[▪●■✦◦•\-*#>〕】．.、\d]+|[（(]\d+[）)]|[①②③④⑤⑥⑦⑧⑨⑩]+)\s*")


def _teacher_items(text: str, cap: int = 40) -> list[str]:
    """教师重点切片文本 → 考点条目（行拆分 + 要点符号清洗；≥6 字；去重保序）。"""
    seen: list[str] = []
    for line in (text or "").splitlines():
        line = _BULLET_RE.sub("", line).strip(" \u3000|·-—：:，。；;")
        if len(line) < 6:
            continue
        if line in seen:
            continue
        seen.append(line)
        if len(seen) >= cap:
            break
    return seen


def _proj_dir() -> Path:
    from . import config as cfg
    return Path(cfg.load().get("projects_dir") or (cfg.CONFIG_DIR / "projects"))


def sync_teacher() -> dict[str, Any]:
    """以教师重点为纲：扫描所有项目的 teacher 切片 → 考点条目（source='teacher'）。

    幂等：同 (subject, chapter, item) 二次同步不重复（IDOR 更新）；不再有该项目的条目会保留
    （学生可随时在视图里订正/删除）。返回统计。
    """
    dbs.migrate()
    root = _proj_dir()
    stats = {"projects": 0, "slices": 0, "items": 0, "subjects": []}
    if not root.is_dir():
        return stats
    subject_set: set[str] = set()
    with dbs.tx(write=True) as cur:
        for proj in sorted(root.iterdir()):
            if not proj.is_dir():
                continue
            meta_path = proj / "meta.json"
            slices_path = proj / "slices.json"
            if not slices_path.exists():
                continue
            subject = ""
            if meta_path.exists():
                try:
                    subject = (json.loads(meta_path.read_text(encoding="utf-8"))
                               .get("subject", "") or "").strip()
                except Exception:  # noqa: BLE001
                    subject = ""
            subject = subject or "未分类"
            try:
                slices = json.loads(slices_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            found = 0
            for s in slices:
                if s.get("role") != "teacher":
                    continue
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                chapter = (s.get("title") or "").strip() or "教师重点"
                items = _teacher_items(text)
                stats["slices"] += 1
                found += 1
                for it in items:
                    rec = {"id": _row_id(subject, chapter, it, "item", "teacher"),
                           "subject": subject, "chapter": chapter, "kind": "item",
                           "item": it, "weight": 1.0, "source": "teacher",
                           "created_at": _now()}
                    dbs.put_row(cur, "syllabus_items", rec,
                                ("subject", "chapter", "kind", "item", "weight", "source"))
                    stats["items"] += 1
            if found:
                stats["projects"] += 1
                subject_set.add(subject)
    stats["subjects"] = sorted(subject_set)
    return stats


def _kp_pool() -> list[str]:
    """学习库知识点名 + 错题主题/tag（覆盖匹配池）。"""
    from . import library as lib
    names: list[str] = []
    for k in lib.list_knowledge():
        if k.get("name"):
            names.append(str(k["name"]))
        if k.get("topic"):
            names.append(str(k["topic"]))
        names += [str(t) for t in (k.get("slices") or []) if isinstance(t, str)]
    for m in lib.list_mistakes():
        if m.get("topic"):
            names.append(str(m["topic"]))
        names += [str(t) for t in (m.get("know_tags") or []) if isinstance(t, str)]
    return [n for n in names if n.strip()]


def match_status(item: str, pool: list[str]) -> tuple[str, Optional[str]]:
    """条目 vs 学习库：返回 (status, matched_kp_name)。

    mastered：命中知识点名且其状态 ∈ solid|mastered（真正掌握）；
    covered：命中任一名字（知识点/错题主题/tag，含「主题」级命中）；
    pending：未覆盖。
    """
    from . import library as lib
    item_n = re.sub(r"[\s·、/（）()\-——]", "", item or "")
    if not item_n:
        return "pending", None
    for k in lib.list_knowledge():
        name = str(k.get("name") or "")
        name_n = re.sub(r"[\s·、/（）()\-——]", "", name)
        if not name_n:
            continue
        if item_n in name_n or name_n in item_n:
            if k.get("state") in _MASTERED_STATES:
                return "mastered", name
            return "covered", name
    for name in pool:
        name_n = re.sub(r"[\s·、/（）()\-——]", "", name)
        if name_n and (item_n in name_n or name_n in item_n):
            return "covered", name
    return "pending", None


def coverage(subject: str = "", source: str = "all") -> dict[str, Any]:
    """科目覆盖度报表（树 + 计数）。零 LLM。source 限定 all|seed|teacher。"""
    rows = _rows(subject, source)
    pool = _kp_pool()
    chapters: dict[str, dict[str, Any]] = {}
    for r in rows:
        ch = chapters.setdefault(r.get("chapter") or "（未分章）", {
            "chapter": r.get("chapter") or "（未分章）",
            "items": [], "total": 0, "covered": 0, "mastered": 0, "pending": 0,
        })
        if r.get("kind") == "item":
            status, matched = match_status(r.get("item") or "", pool)
            entry = {"id": r.get("id"), "item": r.get("item"), "status": status,
                     "matched": matched, "weight": r.get("weight", 1.0)}
            if status == "mastered":
                ch["mastered"] += 1
            elif status == "covered":
                ch["covered"] += 1
            else:
                ch["pending"] += 1
            ch["total"] += 1
            ch["items"].append(entry)
    total = sum(c["total"] for c in chapters.values())
    covered_n = sum(c["covered"] + c["mastered"] for c in chapters.values())
    return {
        "subject": subject or "全部",
        "chapters": sorted(chapters.values(), key=lambda c: -c["total"]),
        "totals": {
            "items": total,
            "covered": covered_n,
            "mastered": sum(c["mastered"] for c in chapters.values()),
            "pending": sum(c["pending"] for c in chapters.values()),
        },
    }


def report_md(subject: str = "", source: str = "all") -> str:
    """未覆盖清单 → markdown（充当「考前清单」）。"""
    data = coverage(subject, source)
    std = {"all": "全部标准", "seed": "官方大纲", "teacher": "教师重点"}.get(source, source)
    lines = [f"# 大纲覆盖报告 · {data['subject']}（{std}）", "",
             f"> 共 {data['totals']['items']} 个考点：已覆盖 {data['totals']['covered']} · "
             f"已掌握 {data['totals']['mastered']} · 未覆盖 {data['totals']['pending']}", ""]
    for ch in data["chapters"]:
        pending = [it["item"] for it in ch["items"] if it["status"] == "pending"]
        if pending:
            lines.append(f"## {ch['chapter']}（未覆盖 {len(pending)}/{ch['total']}）")
            for it in pending:
                lines.append(f"- {it}")
            lines.append("")
    if not lines[3:-1]:
        lines.append("全部已覆盖 🎉")
    return "\n".join(lines)
