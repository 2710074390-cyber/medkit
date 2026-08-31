"""WP-01 大纲覆盖度引擎（考试锚定，《结构化执行方案》§3 WP-01）。

- 存储：SQLite `syllabus_items` 表（迁移 v2：kind=chapter 章行 / kind=item 考点行）。
- 大纲标准二选一（本版起）：① 软件内置西综306 大纲（source='seed'，bundled 种子幂等导入）；
  ② 教师重点（source='teacher'，用户导入文件/粘贴/项目 teacher 切片自动处理而来）。
  历史 source='paste' 由迁移 v4 归一为 'teacher'（用户自供内容统一归教师重点）。
- 种子：`data/syllabus_seed_306.json`（由 docs/spikes/build_syllabus_seed.py 从
  GoldenSet 真题 + 知识库素材教材 chunks 元数据构建；ensure_seed 幂等导入）。
- 零 LLM 原则：教师重点自动处理（两档解析：章/条目结构化 ↔ 要点行 flat）/ 覆盖判定（本地
  匹配）/ 报告（md）全本地。唯一 LLM 触点：官方大纲文件导入的契约抽取（K3/IMP-13，
  ``extract_outline``，逐科 chat_json + OutlineSubject；失败回退本地规则）。
- 覆盖口径：条目文本与学习库「知识点名/错题主题」匹配 →
  matched（covered）/ mastered（匹配的知识点 state ∈ solid|mastered）/ pending（未覆盖）。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

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
        # WP-12：纯净安装包不内置种子——明确提示可上传官方 306 大纲
        return {"imported": 0,
                "note": "未内置大纲（纯净版）：可上传官方 306 大纲(md/txt) 或使用教师重点"}
    seed = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    with dbs.tx(write=True) as cur:
        if force:
            # 重建种子：删除旧 seed 行（教师重点行 source=teacher 不受影响）
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


def delete_item(item_id: str) -> bool:
    """删除一条大纲条目（seed/teacher 均可；误删可经 ensure(force) 重建种子或重新导入）。"""
    dbs.migrate()
    with dbs.tx(write=True) as cur:
        n = cur.execute("DELETE FROM syllabus_items WHERE id=?", (item_id,)).rowcount
    return bool(n)


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



# ---------------------------------------------------------------- 官方大纲 → 结构化草稿（K3/IMP-13 契约抽取）
_OUTLINE_ANCHOR = "考查内容"
_OUTLINE_TOP_RE = re.compile(r"^\s*(?:#{1,6}\s*)?([一二三四五六七八九十]+)、\s*(.+?)\s*$")


def split_subjects(text: str) -> list[tuple[str, str]]:
    """按「考查内容」锚点 + 中文数字顶级标题切分 → [(科目名, 该科正文)]。

    仅识别 306 大纲式结构（「一、生理学」顶级，兼容 Markdown 标题前缀）；锚点缺失时
    整篇参与切分；无顶级标题 → 空列表（调用方走本地规则兜底）。
    """
    body = text or ""
    idx = body.find(_OUTLINE_ANCHOR)
    if idx >= 0:
        body = body[idx:]
    out: list[tuple[str, str]] = []
    cur_name, cur_lines = "", []
    for raw in body.splitlines():
        m = _OUTLINE_TOP_RE.match(raw)
        if m and len(out) <= 16:
            if cur_name:
                out.append((cur_name, "\n".join(cur_lines)))
            cur_name, cur_lines = m.group(2).strip(), []
        elif cur_name:
            cur_lines.append(raw)
    if cur_name:
        out.append((cur_name, "\n".join(cur_lines)))
    return out


def extract_outline(text: str, client: Any = None,
                    schema: Optional[type[BaseModel]] = None) -> Optional[dict[str, Any]]:
    """官方大纲 md → 结构化 outline（chat_json + OutlineSubject 契约，K3/IMP-13）。

    按科目分块逐科调用（避免长文截断/降质），合并为 ``{exam, subjects, errors}``；
    任一科失败仅记入 errors，其余照常返回；全部失败返回 ``None``（调用方走本地规则兜底）。

    max_tokens=16000：探测确认当前推理型模型（deepseek-v4-flash）会把 reasoning_tokens
    计入 max_tokens，6000 时内科/外科被推理吃满返回空（finish=length）；16000 后 6/6 稳定。

    ``client`` 注入便于离线测试；``schema`` 供测试注入 mock 契约校验（默认逐科契约
    :class:`OutlineSubject`，与 prompt 输出 ``{name, chapters}`` 对齐）。
    """
    from ..agents import get_client, load_prompt
    from .llm import LLMError
    from .schema import OutlineSubject

    subjects = split_subjects(text)
    if not subjects:
        return None
    client = client or get_client("gen")
    model = schema or OutlineSubject
    prompt = load_prompt("syllabus_extract.md")
    merged: list[dict[str, Any]] = []
    errors: list[str] = []
    for name, body in subjects:
        if not body.strip():
            continue
        try:
            # 标题行随正文下发：模型按提示词第 6 条取「原文科目名」，不猜名
            raw = client.chat_json(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": f"{name}\n{body}"}],
                temperature=0.1, max_tokens=16000, schema=model)
        except LLMError as e:
            errors.append(f"{name}: {e}")
            continue
        except Exception as e:  # noqa: BLE001 —— 网络/配置异常一律按科失败处理
            errors.append(f"{name}: {e}")
            continue
        if raw is None:
            errors.append(f"{name}: 契约校验失败（无有效输出）")
            continue
        merged.append(raw.model_dump())
    if not merged:
        return None
    # 科目名归一（去尾部括号注释，如「外科学(含骨科学)」→「外科学」，与种子/知识库命名对齐）
    for s in merged:
        nm = s.get("name") or ""
        m = re.search(r"[（(][^）)]*[）)]$", nm)
        if m and m.start() > 0:
            s["name"] = nm[:m.start()].strip()
    # 科目保序去重（同名科目合并章节，保留首次出现位置）
    seen: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for s in merged:
        key = s.get("name") or ""
        if key in seen:
            seen[key]["chapters"] += s.get("chapters", [])
        else:
            seen[key] = s
            ordered.append(s)
    first_line = next((ln.strip() for ln in (text or "").splitlines()
                       if ln.strip() and not ln.startswith("#")), "")
    exam = first_line[:60] if len(first_line) <= 60 else ""
    return {"exam": exam, "subjects": ordered, "errors": errors}


def outline_drafts(outline: dict[str, Any]) -> list[dict[str, str]]:
    """结构化 outline → 与 parse_text 同形状的草稿（subject/chapter/item），供确认落库。"""
    return [{"subject": s.get("name") or "", "chapter": c.get("name") or "",
             "item": it}
            for s in outline.get("subjects", [])
            for c in s.get("chapters", [])
            for it in c.get("items", [])]


def add_seed_items(drafts: list[dict[str, str]]) -> dict[str, Any]:
    """官方大纲草稿 → 落库（source='seed'，幂等 IDOR 更新）。返回统计。

    seed = 官方大纲标准（与 ensure_seed 同源标签）；teacher = 教师重点/用户自供内容。
    """
    dbs.migrate()
    added = 0
    subjects: set[str] = set()
    chapters: set[str] = set()
    with dbs.tx(write=True) as cur:
        for d in drafts:
            subj = (d.get("subject") or "").strip()
            chap = (d.get("chapter") or "").strip()
            item = (d.get("item") or "").strip()
            if not subj or len(item) < 2:
                continue
            rec = {"id": _row_id(subj, chap, item, "item", "seed"),
                   "subject": subj, "chapter": chap, "kind": "item",
                   "item": item, "weight": 1.0, "source": "seed",
                   "created_at": _now()}
            exists = cur.execute("SELECT 1 FROM syllabus_items WHERE id=?",
                                 (rec["id"],)).fetchone()
            dbs.put_row(cur, "syllabus_items", rec,
                        ("subject", "chapter", "kind", "item", "weight", "source"))
            if not exists:
                added += 1
            subjects.add(subj)
            chapters.add(chap)
    return {"added": added, "total": len(drafts),
            "subjects": sorted(subjects), "chapters": len(chapters)}
# ---------------------------------------------------------------- AI 结构化（WP-10：原文 + 结构化双存储）
def structurize_outline(text: str, subject: str = "",
                        client: Any = None) -> dict[str, Any]:
    """官方/任意大纲原文 → AI 结构化（LLM 契约抽取；不丢失则校验通过）。

    返回 {ok, structured, stats, diff, original_path, note}；
    - 原文按 sha1 存 ~/.medkit/outline_originals/<sha1>.md（双存储，可审计）；
    - ok = 结构化条目数 ≥ 原文条目数 × 95%（任一科失败/不达标 → ok=False，不静默替换）。
    """
    from . import config as cfg

    orig = parse_text(text, subject)
    outline = extract_outline(text, client=client)
    digest = hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]
    originals = cfg.CONFIG_DIR / "outline_originals"
    originals.mkdir(parents=True, exist_ok=True)
    path = originals / f"{digest}.md"
    path.write_text(text or "", encoding="utf-8")
    if outline is None:
        return {"ok": False, "structured": None,
                "stats": {"source_items": len(orig), "structured_items": 0,
                          "subjects": [], "chapters": 0},
                "diff": {"original_items": len(orig), "structured_items": 0,
                         "missing": len(orig)},
                "original_path": str(path),
                "note": "LLM 结构化失败（或原文无法分科）——原文已保留，不替换"}
    drafts = outline_drafts(outline)
    stats = {
        "source_items": len(orig),
        "structured_items": len(drafts),
        "subjects": [s.get("name") or "" for s in outline.get("subjects", [])],
        "chapters": sum(len(s.get("chapters") or []) for s in outline.get("subjects", [])),
    }
    target = int(math.ceil(len(orig) * 0.95)) if orig else 0
    missing = max(0, len(orig) - len(drafts))
    ok = len(drafts) >= target
    # R4-05：完整性通过即幂等确立为官方大纲（source='seed'），付费产物有了可回读出口；不达标绝不动原文
    if ok:
        saved = add_seed_items(drafts)
        added = saved["added"]
        source = "seed"
        note = (f"结构化 {len(drafts)} 条 / 原文 {len(orig)} 条（缺失 {missing}），通过完整性校验，"
                f"已确立为官方大纲（新增 {added} 条）")
    else:
        added, source = 0, "teacher"
        note = (f"结构化 {len(drafts)} 条 / 原文 {len(orig)} 条（缺失 {missing}），"
                "未达 95% 完整性，保留原文未替换")
    return {"ok": ok, "structured": outline, "stats": stats,
            "diff": {"original_items": len(orig), "structured_items": len(drafts),
                     "missing": missing},
            "source": source, "added": added,
            "original_path": str(path), "note": note}


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
    with dbs.tx(write=False) as cur:   # R4-08：纯读路径不开写事务（避免抢写锁）
        return dbs.list_rows(cur, "syllabus_items", f"{cond} {order}", tuple(params))


def chapter_items_text(subject: str = "", limit: int = 800,
                        source: str = "all") -> str:
    """科目大纲条目 → 注入文本（≤limit 字；章标题 + 条目，供出题 subtopic 对齐）。

    WP-10：source 限定 teacher（主要依据）/ seed（官方 306 补充）/ all（聚合）。
    """
    data = coverage(subject, source)
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
    with dbs.tx(write=False) as cur:   # R4-08：纯读路径不开写事务（避免抢写锁）
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


# ---------------------------------------------------------------- 教师重点文件自动处理（大纲二选一）
_KP_CLEAN_RE = re.compile(
    r"^\s*(?:重点掌握|考试大纲要求|应掌握|需掌握|重点|考点|掌握|熟悉|了解)\s*[:：]?\s*")


def extract_teacher_kps(drafts: list[dict[str, str]], cap: int = 200) -> list[dict[str, str]]:
    """教师重点草稿 → 知识点名（「知识点提取」步骤，零 LLM）。

    规范化：去首部「重点/掌握/熟悉…」前缀与尾部标点噪声；压缩空白；超 40 字在最后一个
    「、」处收束（保留主干）；按 (subject, name) 去重保序。返回
    ``[{subject, chapter, name, item}]``。

    设计边界（详见 AGENT_HANDOFF）：本步骤产出「知识点名」供导入预览人核、后续出题/记忆卡
    （WP-05）锚点与覆盖匹配使用；**不写入学习库掌握度状态机**（避免凭空生成 weak 知识点
    涌入推荐池，掌握度仅由真实错题/判分事件驱动）。
    """
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for d in drafts or []:
        name = _KP_CLEAN_RE.sub("", d.get("item") or "").strip()
        name = re.sub(r"\s{2,}", " ", name)
        if len(name) > 40:
            cut = name.rfind("、", 0, 40)
            name = name[:cut] if cut >= 2 else name[:40]
        name = name.strip(" \u3000|·-—：:，。；;、【】[]")
        if len(name) < 2:
            continue
        key = ((d.get("subject") or "").strip(), name)
        if key in seen:
            continue
        seen.add(key)
        out.append({"subject": key[0], "chapter": (d.get("chapter") or "").strip(),
                    "name": name, "item": (d.get("item") or "").strip()})
        if len(out) >= cap:
            break
    return out


def import_teacher_text(text: str, subject: str = "",
                        chapter_hint: str = "教师重点",
                        structured_cap: int = 500) -> dict[str, Any]:
    """教师重点全文 → 结构化考点草稿（自动处理主流程，零 LLM）。

    两档策略（自动判定，无需用户选择）：
    - structured：文本带「章 + 编号条目」结构 → ``parse_text`` 提取 章/条目 层级；
    - flat：无显式结构（讲义/PPT 式段落与要点符号）→ ``_teacher_items`` 行级要点提取，
      全部挂到 ``chapter_hint`` 章下。
    structured 草稿 <2 条视为结构识别失败，落 flat 兜底；structured 超 ``structured_cap``
    条取前 cap（文件型导入防超量）；两档均无结果返回空 drafts。

    返回 ``{mode, subject, drafts:[{subject, chapter, item}], knowledge, note}``
    （零副作用，不落库）。
    """
    body = (text or "").strip()
    if not body:
        return {"mode": "none", "subject": subject, "drafts": [], "knowledge": [],
                "note": "文件未提取到文本（扫描件请先 OCR）"}
    drafts = parse_text(body, subject)
    if len(drafts) >= 2:
        if len(drafts) > structured_cap:
            drafts = drafts[:structured_cap]
            note = f"识别到章/条目结构（超 {structured_cap} 条，取前 {structured_cap} 条）"
        else:
            note = f"识别到章/条目结构（{len(drafts)} 条）"
        subj = drafts[0]["subject"] or subject or "未分类"
        for d in drafts:
            d["subject"] = d["subject"] or subj
            d["chapter"] = d["chapter"] or chapter_hint
        return {"mode": "structured", "subject": subj, "drafts": drafts,
                "knowledge": extract_teacher_kps(drafts), "note": note}
    items = _teacher_items(body, cap=200)
    if not items:
        return {"mode": "none", "subject": subject, "drafts": [], "knowledge": [],
                "note": "未提取到考点条目（行文本均需 ≥6 字）"}
    subj = subject or "未分类"
    drafts = [{"subject": subj, "chapter": chapter_hint, "item": it} for it in items]
    return {"mode": "flat", "subject": subj, "drafts": drafts,
            "knowledge": extract_teacher_kps(drafts),
            "note": f"按要点行提取（{len(items)} 条，归入「{chapter_hint}」章）"}


def add_teacher_items(drafts: list[dict[str, str]]) -> dict[str, Any]:
    """教师重点草稿 → 落库（source='teacher'，幂等 IDOR 更新）。返回统计。"""
    dbs.migrate()
    added = 0
    subjects: set[str] = set()
    chapters: set[str] = set()
    with dbs.tx(write=True) as cur:
        for d in drafts:
            subj = (d.get("subject") or "未分类").strip()
            chap = (d.get("chapter") or "教师重点").strip()
            item = (d.get("item") or "").strip()
            if len(item) < 2:
                continue
            rec = {"id": _row_id(subj, chap, item, "item", "teacher"),
                   "subject": subj, "chapter": chap, "kind": "item",
                   "item": item, "weight": 1.0, "source": "teacher",
                   "created_at": _now()}
            exists = cur.execute("SELECT 1 FROM syllabus_items WHERE id=?",
                                 (rec["id"],)).fetchone()
            dbs.put_row(cur, "syllabus_items", rec,
                        ("subject", "chapter", "kind", "item", "weight", "source"))
            if not exists:
                added += 1
            subjects.add(subj)
            chapters.add(chap)
    return {"added": added, "total": len(drafts),
            "subjects": sorted(subjects), "chapters": len(chapters),
            "knowledge": extract_teacher_kps(drafts)}


def _parse_teacher_file(path: str | Path, subject: str = "",
                        chapter_hint: str = "教师重点") -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """教师重点文件 → 文本抽取 + 两档解析（零 LLM）。返回 (parsed, error_note)。"""
    from .extract import ExtractError, extract_text
    try:
        blocks = extract_text(Path(path))
    except ExtractError as e:
        return None, str(e)
    text = "\n".join(b.get("text", "") for b in blocks)
    return import_teacher_text(text, subject, chapter_hint), None


def import_teacher_file(path: str | Path, subject: str = "",
                        chapter_hint: str = "教师重点") -> dict[str, Any]:
    """教师重点文件（PDF 文本层 / DOCX / MD / TXT）→ 自动处理全流程（零 LLM）。

    文本抽取 → ``import_teacher_text`` 两档解析（结构化/要点行）+ ``extract_teacher_kps``
    知识点提取 → ``add_teacher_items`` 幂等入库。
    返回 ``{mode, subject, added, total, drafts, knowledge, note, error?}``；扫描件 PDF 等
    抽取失败时 mode='error'（不落库，note 携带可读提示）。
    """
    parsed, err = _parse_teacher_file(path, subject, chapter_hint)
    if err is not None:
        return {"mode": "error", "subject": subject, "drafts": [],
                "added": 0, "total": 0, "knowledge": [], "note": err, "error": True}
    base: dict[str, Any] = {"mode": parsed["mode"], "subject": parsed["subject"],
                            "drafts": parsed["drafts"], "knowledge": parsed.get("knowledge", []),
                            "note": parsed["note"]}
    if parsed["mode"] == "none":
        base.update(added=0, total=0)
        return base
    saved = add_teacher_items(parsed["drafts"])
    base["added"] = saved["added"]
    base["total"] = saved["total"]
    return base


def import_teacher_file_preview(path: str | Path, subject: str = "",
                                chapter_hint: str = "教师重点") -> dict[str, Any]:
    """教师重点文件 → 仅解析预览（不落库），返回与 import_teacher_file 同构（added=0）。

    R3-25：前端「草稿→确认入库」两段式与粘贴导入同门槛——解析后渲染草稿列表，
    用户点确认才经 /api/syllabus/confirm 批量入库。
    """
    parsed, err = _parse_teacher_file(path, subject, chapter_hint)
    if err is not None:
        return {"mode": "error", "subject": subject, "drafts": [],
                "added": 0, "total": 0, "knowledge": [], "note": err, "error": True}
    base: dict[str, Any] = {"mode": parsed["mode"], "subject": parsed["subject"],
                            "drafts": parsed["drafts"], "knowledge": parsed.get("knowledge", []),
                            "note": parsed["note"]}
    base["added"] = 0
    base["total"] = len(parsed["drafts"])
    base["preview"] = True
    return base


def _kp_pool(knowledge: Optional[list[dict[str, Any]]] = None,
             mistakes: Optional[list[dict[str, Any]]] = None) -> list[str]:
    """学习库知识点名 + 错题主题/tag（覆盖匹配池）。

    D-12：接受预加载的知识点/错题列表（coverage 一次加载，循环内复用，不再逐条重载全表）。
    """
    from . import library as lib
    if knowledge is None:
        knowledge = lib.list_knowledge()
    if mistakes is None:
        mistakes = lib.list_mistakes()
    names: list[str] = []
    for k in knowledge:
        if k.get("name"):
            names.append(str(k["name"]))
        if k.get("topic"):
            names.append(str(k["topic"]))
        names += [str(t) for t in (k.get("slices") or []) if isinstance(t, str)]
    for m in mistakes:
        if m.get("topic"):
            names.append(str(m["topic"]))
        names += [str(t) for t in (m.get("know_tags") or []) if isinstance(t, str)]
    return [n for n in names if n.strip()]


def match_status(item: str, pool: list[str],
                 knowledge: Optional[list[dict[str, Any]]] = None) -> tuple[str, Optional[str]]:
    """条目 vs 学习库：返回 (status, matched_kp_name)。

    mastered：命中知识点名且其状态 ∈ solid|mastered（真正掌握）；
    covered：命中任一名字（知识点/错题主题/tag，含「主题」级命中）；
    pending：未覆盖。
    D-12：knowledge 可预加载传入（coverage 一次加载、循环内复用，避免每条目重载全表）。
    """
    from . import library as lib
    item_n = re.sub(r"[\s·、/（）()\-——]", "", item or "")
    if not item_n:
        return "pending", None
    if knowledge is None:
        knowledge = lib.list_knowledge()
    for k in knowledge:
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
    from . import library as lib
    rows = _rows(subject, source)
    # D-12：一次加载知识点/错题全表，循环内复用（原实现每条目重载全表）
    knowledge = lib.list_knowledge()
    mistakes = lib.list_mistakes()
    pool = _kp_pool(knowledge, mistakes)
    chapters: dict[str, dict[str, Any]] = {}
    for r in rows:
        ch = chapters.setdefault(r.get("chapter") or "（未分章）", {
            "chapter": r.get("chapter") or "（未分章）",
            "items": [], "total": 0, "covered": 0, "mastered": 0, "pending": 0,
        })
        if r.get("kind") == "item":
            status, matched = match_status(r.get("item") or "", pool, knowledge)
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
