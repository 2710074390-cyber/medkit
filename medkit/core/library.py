"""个人学习库（M1/M2）：错题沉淀 + 知识点掌握度建模。跨项目、跨考试的个人资产。

存储：~/.medkit/library/{mistakes.json, knowledge.json}（非项目目录，与出题解耦）。
无外部依赖；原子写复用 fsutil.write_json_atomic；不调 LLM（掌握度/优先级纯本地规则）。

数据模型（对齐设计文档 v0.7 §2）：
- mistakes: 一次错题记录（来源：押题卷同步/文本/图片OCR/文件）
- knowledge: 由错题的 know_tags/topic 归并出的知识点，含掌握度状态机 weak→shaky→solid→mastered
"""

import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from . import config as cfg
from .fsutil import read_json_list, write_json_atomic

LIBRARY_DIR = cfg.CONFIG_DIR / "library"
MISTAKES_FILE = LIBRARY_DIR / "mistakes.json"
KNOWLEDGE_FILE = LIBRARY_DIR / "knowledge.json"

# 掌握度状态机阈值（score ∈ [0,1]）——对偶《设计文档 §2.2 / §4.3》
STATE_THRESHOLDS = [("mastered", 0.95), ("solid", 0.80), ("shaky", 0.60)]
"""由高到低，(state, min_score)；低于 0.60 → weak"""

# 最近正确率窗口（分子为窗口内答对次数；窗口外只影响衰减）
RECENT_WINDOW = 20

# 提问式学习判分：≥2 记「答对」（与 core/tutor.PASS_SCORE 同口径）
TUTOR_PASS_SCORE = 2


# ---------------------------------------------------------------- 原子读写
def _load(path: Path) -> list[dict[str, Any]]:
    """读 JSON 数组；缺失/损坏 → 空（复用 fsutil 统一容错）。"""
    return read_json_list(path)


def _save(path: Path, data: list[dict[str, Any]]) -> None:
    write_json_atomic(path, data)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today() -> str:
    return date.today().isoformat()


def _days_ago(iso: Optional[str]) -> int:
    if not iso:
        return 0
    try:
        return (date.today() - date.fromisoformat(iso[:10])).days
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------- 状态与得分（纯函数，可测/可解释）
def compute_state(score: float) -> str:
    """score → weak/shaky/solid/mastered。"""
    for name, threshold in STATE_THRESHOLDS:
        if score >= threshold:
            return name
    return "weak"


def compute_score(correct: int, total: int, last_tried: Optional[str]) -> float:
    """综合掌握分 score∈[0,1]。

    口径（《设计文档 §4.3》，可解释）：
    60% 权重 = 近 {RECENT_WINDOW} 题正确率；30% = 距上次碰面的衰减（越久未碰且曾因挫折而来→越薄弱）；
    10% = 基础置信（样本量不足时打折，避免一两道题就定性）。
    """
    if total <= 0:
        return 0.0
    correct_rate = correct / total
    recency = min(_days_ago(last_tried), 14) / 14.0     # 0=刚碰过,1=≥14天
    recency_kept = 1.0 - 0.6 * recency                 # 越久没复盘越可能已遗忘→削弱“掌握”假象
    confidence = min(total / float(max(RECENT_WINDOW, 1)), 1.0)
    score = 0.60 * correct_rate + 0.30 * (0.4 + 0.6 * recency_kept) + 0.10 * confidence
    return round(min(max(score, 0.0), 1.0), 3)


def compute_priority(score: float, miss_count: int, last_tried: Optional[str]) -> float:
    """推荐优先级 priority∈[0,1]（越高越该先学）。

    50% = 掌握度缺口 (1-score)；30% = 错题密度；20% = 刚错过优先（近期失败最该马上复习）。
    """
    miss_density = min(miss_count / 6.0, 1.0)
    recent_miss = 1.0 - min(_days_ago(last_tried), 7) / 7.0
    return round(min(max(0.50 * (1 - score) + 0.30 * miss_density + 0.20 * recent_miss, 0.0), 1.0), 3)


# ---------------------------------------------------------------- 错题 CRUD
def list_mistakes() -> list[dict[str, Any]]:
    return _load(MISTAKES_FILE)


def _find(records: list[dict[str, Any]], mid: str) -> Optional[dict[str, Any]]:
    return next((r for r in records if r.get("id") == mid), None)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}"


def add_mistake(data: dict[str, Any]) -> dict[str, Any]:
    """新增一条错题并派生/更新知识点。返回落库后的错题记录。"""
    records = list_mistakes()
    seq = str(len(records) + len(_today()))
    mid = data.get("id") or _new_id("m")
    record = {
        "id": mid,
        "source": data.get("source", "manual"),
        "source_ref": data.get("source_ref") or {},
        "subject": str(data.get("subject", "") or "").strip(),
        "chapter": str(data.get("chapter", "") or "").strip(),
        "topic": str(data.get("topic", "") or "").strip(),
        "question": str(data.get("question", "") or "").strip(),
        "options": list(data.get("options") or []),
        "answer": str(data.get("answer", "") or "").strip(),
        "user_answer": str(data.get("user_answer", "") or "").strip(),
        "correct": bool(data.get("correct", False)),
        "analysis": str(data.get("analysis", "") or "").strip(),
        "error_reason": str(data.get("error_reason", "") or "").strip(),
        "know_tags": [str(t).strip() for t in (data.get("know_tags") or []) if str(t).strip()],
        "bloom": str(data.get("bloom", "") or "").strip(),
        "miss_count": max(int(data.get("miss_count", 1) or 1), 1),
        "learned": bool(data.get("learned", False)),
        # 出错样本入聚点时的归类快照：_touch_knowledge 按 learned 记 correct/miss。
        # mark_learned 之后 learned 会变，但归因仍以「入账当时」为准，删除时才不会误减。
        "_correct_sample": bool(data.get("learned", False)),
        "created_at": _now(),
        "last_tried": _now(),
        "mastery": compute_state(compute_score(0, 1, _now())),
        "_seq": seq,
    }
    dup = _find(records, mid)
    if dup:
        records[records.index(dup)] = record
    else:
        records.append(record)
    _save(MISTAKES_FILE, records)
    _touch_knowledge(record)
    return record


def batch_add(records: list[dict[str, Any]]) -> int:
    """批量入错题（导入/同步）；返回成功条数。"""
    added = 0
    for r in records:
        try:
            add_mistake(r)
            added += 1
        except Exception:  # noqa: BLE001  单条失败不阻断批量
            continue
    return added


def sync_from_paper(questions: list[dict[str, Any]], pid: Optional[str] = None) -> int:
    """押题卷判错同步：把判错的（结构化）题目转成错题记录。返回新增条数。

    questions 元素复用出题结构的题目字段(id, subject, sid, question, options, answer,
    analysis, subtopic, bloom…)，外加 user_answer。已存在的（同 question 前 40 字）视为重复，不重复入库。
    """
    existing = list_mistakes()
    sigs = {m.get("question", "")[:40] for m in existing if m.get("question")}
    rows: list[dict[str, Any]] = []
    for q in questions:
        qtext = str(q.get("question", "") or "").strip()
        if not qtext:
            continue
        if qtext[:40] in sigs:      # 去重
            continue
        rows.append({
            "source": "paper",
            "source_ref": {"pid": pid, "question_id": q.get("id"),
                           "sid": q.get("sid")},
            "subject": q.get("subject") or str((q.get("source_ref") or {}).get("subject") or ""),
            "chapter": q.get("chapter") or str(q.get("sid") or ""),
            "topic": str(q.get("subtopic") or "").strip(),
            "question": qtext,
            "options": list(q.get("options") or []),
            "answer": str(q.get("answer") or "").strip(),
            "user_answer": str(q.get("user_answer") or "").strip(),
            "analysis": str(q.get("analysis") or "").strip(),
            "know_tags": [t for t in (q.get("know_tags") or []) if str(t).strip()]
                         or ([str(q.get("subtopic") or "").strip()] if q.get("subtopic") else []),
            "bloom": q.get("bloom") or "",
            "error_reason": str(q.get("error_reason") or "").strip() or "reasoning",
            "correct": False,
        })
    return batch_add(rows)


def update_mistake(mid: str, patch: dict[str, Any]) -> Optional[dict[str, Any]]:
    records = list_mistakes()
    cur = _find(records, mid)
    if cur is None:
        return None
    allowed = {"subject", "chapter", "topic", "question", "options", "answer",
               "user_answer", "analysis", "error_reason", "know_tags", "bloom", "learned", "correct"}
    for k, v in patch.items():
        if k in allowed:
            cur[k] = v
    cur["last_tried"] = _now()
    # correct/learned 会反写掌握度 → 重算该错题派生的知识点
    before = list(_load(KNOWLEDGE_FILE))
    _save(MISTAKES_FILE, records)
    _touch_knowledge(cur, recompute_existing=before)
    return cur


def delete_mistake(mid: str) -> bool:
    records = list_mistakes()
    cur = _find(records, mid)
    if cur is None:
        return False
    records = [r for r in records if r.get("id") != mid]
    _save(MISTAKES_FILE, records)
    _drop_knowledge_of(cur)
    return True


def mark_learned(mid: str, learned: bool = True) -> Optional[dict[str, Any]]:
    """「标记已掌握」只翻转错题上的 learned 标记，**不改写掌握度计数**。

    旧实现 `update_mistake(mid, {"learned": l, "correct": not l})` 会经 _touch_knowledge
    再注入一个样本，导致同一道错题在 knowledge 里被记两次 attempts 且把 miss 刷成 correct，
    是统计虚高的来源。correct/miss 只应由「真实作答」（quiz/review）驱动。
    """
    records = list_mistakes()
    cur = _find(records, mid)
    if cur is None:
        return None
    cur["learned"] = bool(learned)
    _save(MISTAKES_FILE, records)
    return cur


# ---------------------------------------------------------------- 知识点掌握度
def list_knowledge() -> list[dict[str, Any]]:
    return _load(KNOWLEDGE_FILE)


def log_knowledge_event(kp_name: str, event: str, note: str = "") -> str | None:
    """向命中知识点追加一条 history 事件（如 explain / review），返回 kp id 或 None。"""
    kps = list_knowledge()
    hit = next((k for k in kps if k.get("name") == kp_name), None)
    if hit is None:
        return None
    hist = hit.get("history") or []
    hist.append({"t": _now(), "event": event, "note": note})
    hit["history"] = hist[-50:]
    _save(KNOWLEDGE_FILE, kps)
    return hit.get("id")


def record_quiz(kp_name: str, score: int) -> str | None:
    """提问式学习判分回写：score≥2 记「答对」，否则记「答错」，并重算掌握度。返回 kp id 或 None。"""
    kps = list_knowledge()
    hit = next((k for k in kps if k.get("name") == kp_name), None)
    if hit is None:
        return None
    pass_ = int(score or 0) >= TUTOR_PASS_SCORE
    hit["attempts"] = int(hit.get("attempts", 0)) + 1
    if pass_:
        hit["correct"] = int(hit.get("correct", 0)) + 1
    else:
        hit["miss"] = int(hit.get("miss", 0)) + 1
    hit["last_tried"] = _now()
    hit["score"] = compute_score(hit["correct"], hit["attempts"], hit["last_tried"])
    hit["state"] = compute_state(hit["score"])
    hit["priority"] = compute_priority(hit["score"], hit.get("miss", 0), hit["last_tried"])
    hist = hit.get("history") or []
    hist.append({"t": _now(), "event": "quiz", "note": f"score={int(score or 0)}"})
    hit["history"] = hist[-50:]
    _save(KNOWLEDGE_FILE, kps)
    return hit.get("id")


REVIEW_PASS_SCORE = 3   # 与 core.review.GRADE_PASS 同口径：quality≥3 记「成功提取」


def record_review(kp_name: str, quality: int) -> str | None:
    """复习判分回写：quality≥3 记「成功提取」，否则记 miss；刷新 last_tried/last_reviewed。

    这是「复习→掌握度」回路的最后一环：按 SM-2 认真做完一张到期卡后，
    掌握分随这次既真实又及时的提取而抬升，而不是分数只随时间阴跌、推荐永不更新。
    """
    kps = list_knowledge()
    hit = next((k for k in kps if k.get("name") == kp_name), None)
    if hit is None:
        return None
    passed = int(quality or 0) >= REVIEW_PASS_SCORE
    hit["attempts"] = int(hit.get("attempts", 0)) + 1
    if passed:
        hit["correct"] = int(hit.get("correct", 0)) + 1
    else:
        hit["miss"] = int(hit.get("miss", 0)) + 1
    hit["last_tried"] = _now()
    hit["last_reviewed"] = _now()
    hit["score"] = compute_score(hit["correct"], hit["attempts"], hit["last_tried"])
    hit["state"] = compute_state(hit["score"])
    hit["priority"] = compute_priority(hit["score"], hit.get("miss", 0), hit["last_tried"])
    hist = hit.get("history") or []
    hist.append({"t": _now(), "event": "review",
                 "note": f"quality={int(quality or 0)} / {'pass' if passed else 'fail'}"})
    hit["history"] = hist[-50:]
    _save(KNOWLEDGE_FILE, kps)
    return hit.get("id")


# ---------------------------------------------------------------- 文本结构化（本地规则，零 LLM）
_OPT_RE = re.compile(r"^[（(]?([A-Ha-h])[)）、.．\s]+(?=\S)")


def parse_question_text(text: str) -> dict[str, Any]:
    """把一段错题文本按常见标记拆结构（题干/选项/答案/解析）。非严格、可被用户二次编辑。

    支持标记：答案【答案】；解析【解析】；选项行 A./A、/（A）。
    无法识别时整体放入 question。返回可直接入库的结构（best-effort）。
    """
    text = (text or "").strip()
    ans_match = re.search(r"[【\[(]?(?:答案|答|正确答案)[】\])]?\s*[:：]?\s*([A-Ha-h])", text)
    ana_match = re.search(r"[【\[(]?解析[】\])]?\s*[:：]?(.*)$", text, re.S)

    answer = ans_match.group(1).upper() if ans_match else ""
    analysis = ana_match.group(1).strip() if ana_match else ""
    body = text
    if ana_match:
        body = text[: ana_match.start()].strip()
    if ans_match:
        body = body[: ans_match.start()].strip()

    lines = body.splitlines()
    options: list[str] = []
    option_begun = False
    q_lines: list[str] = []
    for line in lines:
        m = _OPT_RE.match(line.strip())
        if m:
            options.append(line.strip())
            option_begun = True
        elif option_begun and line.strip():
            if options:
                options[-1] = options[-1] + " " + line.strip()
        elif not option_begun:
            q_lines.append(line)

    question = ("\n".join(q_lines) if q_lines else text).strip()
    return {
        "question": question,
        "options": options,
        "answer": answer,
        "analysis": analysis,
    }


def _kp_key(rec: dict[str, Any]) -> list[str]:
    """错题 → 应派生的知识点名集合（tag 优先，退化到 topic/chapter）。"""
    tags = [t for t in rec.get("know_tags") or [] if t]
    if tags:
        return tags
    topic = (rec.get("topic") or "").strip()
    if topic:
        return [f"{rec.get('chapter') or ''}·{topic}".strip("·") or topic]
    chapter = (rec.get("chapter") or "").strip()
    if chapter:
        return [chapter]
    return []


def _touch_knowledge(rec: dict[str, Any],
                     recompute_existing: Optional[list[dict[str, Any]]] = None) -> None:
    """错题入/改库后刷新由它命名的知识点（新增一个错题样本 + 重算掌握度）。"""
    kps = recompute_existing if recompute_existing is not None else _load(KNOWLEDGE_FILE)
    kp_by_name = {k.get("name"): k for k in kps}
    learned = bool(rec.get("learned"))
    for name in _kp_key(rec):
        kp = kp_by_name.get(name)
        if kp is None:
            kp = {
                "id": _new_id("kp"), "name": name,
                "subject": rec.get("subject") or "",
                "chapter": rec.get("chapter") or "",
                "score": 0.0, "state": "weak", "priority": 0.0,
                "attempts": 0, "correct": 0, "miss": 0,
                "last_tried": None, "last_reviewed": None,
                "slices": [], "mistakes": [], "history": [],
            }
            kps.append(kp)
        # 归并一个样本：learned/答对 → 记 correct；未掌握 → 记 miss
        if learned:
            kp["correct"] = kp.get("correct", 0) + 1
        else:
            kp["miss"] = kp.get("miss", 0) + 1
        kp["attempts"] = kp.get("attempts", 0) + 1
        kp["last_tried"] = _now()
        mid = rec.get("id")
        if mid and mid not in kp.get("mistakes", []):
            kp["mistakes"] = kp.get("mistakes", []) + [mid]
        kp["score"] = compute_score(kp["correct"], kp["attempts"], kp["last_tried"])
        kp["state"] = compute_state(kp["score"])
        kp["priority"] = compute_priority(kp["score"], kp["miss"], kp["last_tried"])
        kp["history"] = (kp.get("history") or [])[-50:]
    _save(KNOWLEDGE_FILE, kps)
    return None


def _drop_knowledge_of(rec: dict[str, Any]) -> None:
    """删除错题后：从对应知识点的 mistakes 列表移除，并按入账快照回退计数。

    旧实现只减 attempts 不减 correct，删除错题会让 correct 被「固化」在聚点上，
    正确率虚高、优先级失真。回退口径 = 入账当时的归类（_correct_sample）。
    """
    kps = _load(KNOWLEDGE_FILE)
    removed = False
    mid = rec.get("id")
    was_correct = bool(rec.get("_correct_sample"))
    for kp in kps:
        if mid in kp.get("mistakes", []):
            kp["mistakes"] = [m for m in kp["mistakes"] if m != mid]
            kp["attempts"] = max(int(kp.get("attempts", 0)) - 1, 0)
            if was_correct:
                kp["correct"] = max(int(kp.get("correct", 0)) - 1, 0)
            else:
                kp["miss"] = max(int(kp.get("miss", 0)) - 1, 0)
            kp["score"] = compute_score(kp.get("correct", 0), kp.get("attempts", 0),
                                        kp.get("last_tried"))
            kp["state"] = compute_state(kp["score"])
            kp["priority"] = compute_priority(kp["score"], kp.get("miss", 0),
                                              kp.get("last_tried"))
            removed = True
    if removed:
        _save(KNOWLEDGE_FILE, kps)


def get_mastery_view() -> dict[str, Any]:
    """掌握度驾驶舱：知识点全貌 + 全局统计。"""
    kps = _load(KNOWLEDGE_FILE)
    for kp in kps:
        kp.setdefault("score", 0.0)
        kp["state"] = compute_state(kp["score"])
        kp["priority"] = compute_priority(kp["score"], kp.get("miss", 0), kp.get("last_tried"))
    stats = {
        "total_knowledge": len(kps),
        "weak": sum(1 for k in kps if k["state"] == "weak"),
        "shaky": sum(1 for k in kps if k["state"] == "shaky"),
        "solid": sum(1 for k in kps if k["state"] == "solid"),
        "mastered": sum(1 for k in kps if k["state"] == "mastered"),
        "total_mistakes": len(list_mistakes()),
    }
    kps.sort(key=lambda k: k["priority"], reverse=True)
    return {"knowledge": kps, "stats": stats}


def recommend(limit: int = 10) -> list[dict[str, Any]]:
    """「接下来重点学什么」：先筛「薄弱或有错」的知识点，再按 priority 降序。

    早期实现曾以 `(state=="weak", priority)` 排序，导致 weak 一票否决 shaky——
    即使 shaky 因错过更频繁、掌握分更低也排不上去。改为纯 priority 单键排序。
    """
    view = get_mastery_view()
    kps = [k for k in view["knowledge"] if k["miss"] > 0 or k["state"] in ("weak", "shaky")]
    kps.sort(key=lambda k: k["priority"], reverse=True)
    return kps[: max(1, int(limit))]


# ---------------------------------------------------------------- 数据卫生（乱码识别与修复，v0.7.1）
# 历史导入曾出现编码损坏：① UTF-8 字节被按 cp1252 误读（可逆：æ¯… → 中文）
# ② 中间环节已丢失字符（不可逆：???）。本组函数只做「可逆修复 + 不可逆标记」，绝不删除用户数据。
_MOJIBAKE_QRUN = re.compile(r"\?{3,}")
_TEXT_FIELDS = ("name", "subject", "chapter", "topic", "question",
                "answer", "analysis", "user_answer", "error_reason")


def heal_mojibake(s: str) -> str | None:
    """cp1252→utf-8 可逆修复；不可逆/无中文 → None（不动原值）。"""
    if not isinstance(s, str) or not s:
        return None
    try:
        raw = s.encode("cp1252")
        fixed = raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
        return None
    if not fixed or not any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
        return None
    return fixed if fixed != s else None


def _bad_str(s: Any) -> bool:
    if not isinstance(s, str):
        return False
    if _MOJIBAKE_QRUN.search(s or ""):
        return True
    return heal_mojibake(s) is not None


def scan_corrupted() -> dict[str, Any]:
    """统计错题/知识点中疑似编码损坏的记录数（'?' 连续串 或 可逆 cp1252 误读）。"""
    count = 0
    for m in list_mistakes():
        fields = [m.get(k) for k in ("subject", "chapter", "topic", "question", "answer")] \
            + list(m.get("know_tags") or [])
        if any(_bad_str(f) for f in fields):
            count += 1
    for k in list_knowledge():
        if any(_bad_str(k.get(f)) for f in ("name", "subject", "chapter", "topic")):
            count += 1
    return {"corrupted": count}


def _heal_rec_text(rec: dict[str, Any]) -> int:
    """就地修复记录内可逆乱码字段，返回改动字段数。"""
    n = 0
    for key in _TEXT_FIELDS:
        fixed = heal_mojibake(rec.get(key))
        if fixed is not None:
            rec[key] = fixed
            n += 1
    tags = rec.get("know_tags")
    if isinstance(tags, list):
        for i, t in enumerate(tags):
            fixed = heal_mojibake(t)
            if fixed is not None:
                tags[i] = fixed
                n += 1
    return n


def _rec_broken(rec: dict[str, Any]) -> bool:
    """修复后仍含不可逆乱码（'?' 连续串）→ 标记，供前端提示「数据损坏」。"""
    if not isinstance(rec, dict):
        return False
    fields = [rec.get(k) for k in _TEXT_FIELDS] + list(rec.get("know_tags") or [])
    return any(isinstance(f, str) and _MOJIBAKE_QRUN.search(f) for f in fields)


def heal_encoding() -> dict[str, Any]:
    """一次修复：备份 → 可逆乱码还原 → 不可逆记录打 data_broken 标记（不删数据）。"""
    import shutil

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backups: list[str] = []
    for path in (MISTAKES_FILE, KNOWLEDGE_FILE):
        if path.exists():
            bak = path.with_name(f"{path.stem}.pre-heal-{ts}.bak")
            try:
                shutil.copy2(path, bak)
                backups.append(str(bak))
            except OSError:
                pass

    healed = flagged = 0
    for path in (MISTAKES_FILE, KNOWLEDGE_FILE):
        recs = _load(path)
        if not recs:
            continue
        changed = False
        for rec in recs:
            if not isinstance(rec, dict):
                continue
            if _heal_rec_text(rec) > 0:
                healed += 1
                changed = True
            if _rec_broken(rec) and not rec.get("data_broken"):
                rec["data_broken"] = True
                flagged += 1
                changed = True
        if changed:
            _save(path, recs)
    return {"healed": healed, "flagged": flagged, "backups": backups}
