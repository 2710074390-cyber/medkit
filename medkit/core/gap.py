"""WP-03 缺陷驱动智能组卷（《结构化执行方案》§3 WP-03）：一键刷薄弱。

- `plan()` 纯本地配题：薄弱/未覆盖知识点按「priority × 考频权重 × 未覆盖」加权，单知识点 ≤3 题；
- `create_gap_project()` 复用既有课题创建通道（routers.projects.create_project）：
  复制来源项目的教材+教师重点切片，追加「薄弱点清单」教师切片 + requirements 注入，
  卷面/判错回流（sync-paper）零新代码；
- 幂等：同科目未完成的 gap 项目（24h 内）直接复用，不重复创建（重复点击不产生重复课题）；
- 成本预估前置：返回 est（元），前端 toast 后再启动。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import config as cfg
from . import library as lib
from . import realexams as rex
from .cost import estimate_cny, estimate_run, format_estimate

KP_QUESTIONS_CAP = 3       # 单知识点最多 3 题（方案口径）
GAP_WINDOW_HOURS = 24      # 幂等窗口


# ---------------------------------------------------------------- 配题（纯本地，可单测）
def _minute_norm(freq: float) -> float:
    return min(max(freq, 0.0), 1.0)


def plan(subject: str = "", count: int = 50, w_freq: float = 0.15,
         use_syllabus: bool = True, kps: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
    """薄弱知识点 → 题量配额。

    weight_i = priority_i + w_freq × 考频命中(条目归一) + w_syllabus × 未覆盖惩罚…
    实际实现：以 priority 为主键（≥0.3 才入选），考频为加权项；配额 ∝ weight，单 kp ≤3 题。
    """
    from . import syllabus as syl
    count = max(1, min(int(count), 500))
    kps = kps if kps is not None else lib.recommend(limit=40)
    freq = rex.freq_map(subject)
    pending: set[str] = set()
    if use_syllabus:
        cov = syl.coverage(subject)
        pending = {it["item"] for ch in cov["chapters"] for it in ch["items"]
                   if it["status"] == "pending"}
    scored: list[dict[str, Any]] = []
    for kp in kps:
        name = str(kp.get("name") or "")
        if not name:
            continue
        priority = float(kp.get("priority") or 0)
        if priority < 0.3:          # 只刷真薄弱
            continue
        f = 0.0
        for name_cand in (name,):   # 条目命中（含模糊：条目包含在 kp 名或反之）
            f = max(f, freq.get(name_cand, 0.0))
        if not f:
            for entry, fr in freq.items():
                if entry and (entry in name or name in entry):
                    f = max(f, fr)
                    break
        w = priority + _minute_norm(w_freq) * f
        if use_syllabus:
            t = 0.0
            # kp 名与未覆盖条目同句 → 未覆盖加成 0.1
            for p in pending:
                if p and (p in name or name in p):
                    t = 0.1
                    break
            w += t
        scored.append({"kp": name, "kp_id": str(kp.get("id") or ""), "weight": round(w, 3)})
    scored.sort(key=lambda x: -x["weight"])
    total_w = sum(s["weight"] for s in scored) or 1.0
    alloc: list[dict[str, Any]] = []
    remaining = count
    for s in scored:
        q = round(count * s["weight"] / total_w)
        q = min(q, KP_QUESTIONS_CAP, remaining)
        if q <= 0:
            continue
        s["questions"] = q
        alloc.append(s)
        remaining -= q
    # 余量补给权重最前者（仍 ≤3）
    i = 0
    while remaining > 0 and alloc:
        if alloc[i % len(alloc)]["questions"] < KP_QUESTIONS_CAP:
            alloc[i % len(alloc)]["questions"] += 1
            remaining -= 1
        i += 1
        if i > 1000:
            break
    return {"plan": alloc, "total": sum(a["questions"] for a in alloc),
            "weak_top": [a["kp"] for a in alloc[:5]]}


# ---------------------------------------------------------------- 来源项目选择
def pick_source_project(subject: str = "") -> Optional[str]:
    """挑一个可作为教材/教师重点来源的项目：科目匹配、含 teacher 切片、优先已完成。"""
    root = Path(cfg.load().get("projects_dir") or (cfg.CONFIG_DIR / "projects"))
    if not root.is_dir():
        return None
    cands: list[tuple[str, int, str, bool]] = []
    for d in root.iterdir():
        if not d.is_dir() or not (d / "meta.json").exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if subject and (meta.get("subject") or "") != subject:
            continue
        if meta.get("scope") == "gap":      # gap 项目不作为来源（防套娃）
            continue
        slices = []
        try:
            slices = json.loads((d / "slices.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
        if not any(s.get("role") == "teacher" and s.get("text") for s in slices):
            continue
        done = meta.get("stage") == "done"
        order = 1 if done else 0
        cands.append((d.name, order, meta.get("created") or "", done))
    if not cands:
        return None
    cands.sort(key=lambda x: (-x[1], x[3]), reverse=False)     # 已完成优先，其次创建时间
    return cands[0][0]


def _load_slices(pid: str) -> list[dict[str, Any]]:
    root = Path(cfg.load().get("projects_dir") or (cfg.CONFIG_DIR / "projects"))
    try:
        return json.loads((root / pid / "slices.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- 建课题（复用通道 + 幂等）
def recent_gap_project(subject: str = "") -> Optional[str]:
    """最近 24h 内未完成（stage≠done）的同科目 gap 项目（幂等复用）。"""
    root = Path(cfg.load().get("projects_dir") or (cfg.CONFIG_DIR / "projects"))
    if not root.is_dir():
        return None
    cutoff = datetime.now() - timedelta(hours=GAP_WINDOW_HOURS)
    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir() or not (d / "meta.json").exists():
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if meta.get("scope") != "gap" or meta.get("stage") == "done":
            continue
        if subject and (meta.get("subject") or "") != subject:
            continue
        try:
            created = datetime.fromisoformat(meta.get("created") or "")
        except Exception:  # noqa: BLE001
            continue
        if created >= cutoff:
            return d.name
    return None


def create_gap_project(subject: str = "", count: int = 50, w_freq: float = 0.15,
                       source_pid: str = "") -> dict[str, Any]:
    """一键刷薄弱：配题 → 复制来源项目切片 → 追加薄弱清单 → 走 create_project 通道。"""
    from ..routers._common import _read_meta_checked, proj_dir
    from ..routers.projects import ProjectBody, create_project

    plan_data = plan(subject, count, w_freq)
    if not plan_data["plan"]:
        return {"ok": False, "pid": "", "plan": plan_data,
                "msg": "当前没有可刷的薄弱知识点（priority≥0.3）——先做错题/复习积累，或调低阈值"}
    reused = recent_gap_project(subject)
    if reused:
        return {"ok": True, "pid": reused, "reused": True, "plan": plan_data,
                "msg": f"已有未完成的薄弱组卷（{reused}），直接复用——可在其详情页继续/删除"}

    src = source_pid or pick_source_project(subject)
    if not src:
        return {"ok": False, "pid": "", "plan": plan_data,
                "msg": "未找到可复用的来源项目（需含教材 + 教师重点切片）——请先在「② 新建课题」建过项目"}
    slices = _load_slices(src)
    textbooks = [s for s in slices if s.get("role") == "textbook"]
    teachers = [s for s in slices if s.get("role") == "teacher"]
    if not textbooks or not teachers:
        return {"ok": False, "pid": "", "plan": plan_data, "msg": "来源项目切片不完整，请换一个来源项目"}

    weak_list = "、".join(plan_data["weak_top"])
    teacher_text = "\n".join(s.get("text", "") or "" for s in teachers)
    teacher_text += f"\n【本次薄弱点清单（优先覆盖，单知识点≤3题）】\n{weak_list}"

    body = ProjectBody(
        subject=subject or _read_meta_checked(proj_dir(src)).get("subject", ""),
        exam="薄弱专项",
        target=count,
        toggles={"qbank": True, "paper": True, "review": True},
        textbook_slices=textbooks,
        teacher_slices=teachers,
        teacher_text=teacher_text,
        requirements=f"优先覆盖薄弱点：{weak_list}；同一知识点不超过 3 题；卷面标注「薄弱点专项」",
        knobs={"k_realexam_weight": str(w_freq), "k_gap": "1"},
        web_search=False,
    )
    created = create_project(body)
    pid = created["pid"]
    meta = _read_meta_checked(proj_dir(pid))
    meta["scope"] = "gap"
    meta["gap_plan"] = plan_data
    meta["gap_source"] = src
    from ._common import _write_meta_atomic
    _write_meta_atomic(proj_dir(pid), meta)

    chars_t = sum(len(s.get("text", "") or "") for s in textbooks)
    chars_k = sum(len(s.get("text", "") or "") for s in teachers)
    est = estimate_run(chars_textbook=chars_t, chars_teacher=chars_k,
                       n_slices=max(len(textbooks), 1), n_questions=count)
    cny = estimate_cny(cfg.load().get("provider", ""), est["input_tokens"], est["output_tokens"])
    return {"ok": True, "pid": pid, "reused": False, "plan": plan_data,
            "est": {"cny": round(cny, 2) if cny is not None else None,
                    "total_tokens": est["total_tokens"]},
            "msg": f"已创建薄弱组卷课题（{pid}）：{plan_data['total']} 题 · "
                   f"预计 {format_estimate(est['total_tokens'], cny)}"}
