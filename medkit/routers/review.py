"""routers：逐题审核台（列表 / keep-drop-edits 重渲染 / 单题重掷）。

运行中（RUNNING）时 review/regen 返回 409（v0.5），避免与出题线程并发写盘。
"""

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents import medgen
from ..core import config as cfg
from ..core import usage as usage_mod
from ..core.config import resolve_key
from ..core.fsutil import write_json_atomic
from ..gates import options_check
from ..state import RUNNING
from ._common import _log_project, _read_meta_checked, _safe_pid, _write_meta_atomic, proj_dir

router = APIRouter()


@router.get("/api/projects/{pid}/questions")
def project_questions(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成，请先「开始生成」")
    return {"questions": json.loads(f.read_text(encoding="utf-8"))}


def _rerender_project(base, questions: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    """审核后重渲染全部产物（复用渲染层；复习手册若有旧 MD 则重转 HTML）。"""
    from ..core.orchestrator import _sample_paper
    from ..render import qbank_html, review_html
    subject = meta.get("subject", "")
    toggles = meta.get("toggles", {})
    out_dir = base / "最终产物"
    out_dir.mkdir(exist_ok=True)
    qbank_md = qbank_html.export_md(questions, f"{subject} 题库")
    (out_dir / "qbank.md").write_text(qbank_md, encoding="utf-8")
    (out_dir / "qbank.html").write_text(
        qbank_html.export_html(questions, f"{subject} 题库"), encoding="utf-8")
    rendered = ["qbank.md", "qbank.html"]
    if toggles.get("paper", True):
        paper_qs = _sample_paper(questions, min(50, len(questions)))
        (out_dir / "押题卷.html").write_text(
            qbank_html.export_paper_html(paper_qs, f"{subject} 押题卷"), encoding="utf-8")
        rendered.append("押题卷.html")
    review_md_path = out_dir / "复习手册.md"
    if review_md_path.exists():
        (out_dir / "复习手册.html").write_text(
            review_html.review_to_html(review_md_path.read_text(encoding="utf-8"),
                                       f"{subject} 复习手册"), encoding="utf-8")
        rendered.append("复习手册.html")
    (out_dir / "anki_export.txt").write_text(
        qbank_html.export_anki(questions, f"{subject} 题库"), encoding="utf-8")
    rendered.append("anki_export.txt")
    meta["final_count"] = len(questions)
    _write_meta_atomic(base, meta)
    _log_project(base, f"✏️ 审核后重渲染：{len(questions)} 题（{', '.join(rendered)}）")
    return rendered


class ReviewBody(BaseModel):
    keep: list[str] = []
    drop: list[str] = []
    edits: list[dict[str, Any]] = []


@router.post("/api/projects/{pid}/questions/review")
def review_questions(pid: str, body: ReviewBody) -> dict[str, Any]:
    """keep/drop/edits → 覆盖题库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中审核 → 409（旧实现与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可审核（请等待完成或先停止）")
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    keep_set = set(body.keep) if body.keep else None          # 空 = 全保留
    drop_set = set(body.drop)
    edits = {e.get("id"): e for e in body.edits if e.get("id")}
    out: list[dict[str, Any]] = []
    for q in questions:
        qid = q.get("id")
        if keep_set is not None and qid not in keep_set:
            continue
        if qid in drop_set:
            continue
        if qid in edits and edits[qid]:
            e = edits[qid]
            for k in ("question", "options", "answer", "analysis", "bloom", "type", "subtopic"):
                if k in e:
                    q[k] = e[k]
        out.append(q)
    if not out:
        raise HTTPException(400, "保留题数为 0，请至少保留一题")
    write_json_atomic(f, out)  # v0.5：原子写（旧实现裸 write_text）
    rendered = _rerender_project(base, out, meta)
    return {"ok": True, "questions": len(out), "rendered": rendered}


class RegenBody(BaseModel):
    id: str


@router.post("/api/projects/{pid}/regen")
def regen_question(pid: str, body: RegenBody) -> dict[str, Any]:
    """按 q.sid 找回原切片，单题重掷（generate_slice count=1），替换入库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中重掷 → 409（避免与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可重掷（请等待完成或先停止）")
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    q = next((x for x in questions if x.get("id") == body.id), None)
    if q is None:
        raise HTTPException(404, f"题目 {body.id} 不存在")
    slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    sid = q.get("sid", "")
    slice_ = next((s for s in slices if s.get("sid") == sid), None)
    if slice_ is None:
        raise HTTPException(400, f"原切片 {sid} 不存在，无法重掷（可按编辑改用同章节其他题）")
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先配置 API Key")
    teacher_text = "\n".join(s.get("text", "") for s in slices if s.get("role") == "teacher")
    client = medgen.make_client()
    with usage_mod.context() as uctx:  # v0.5：重掷独立记账（不再污染下一轮 run）
        try:
            new_qs, _ = medgen.generate_slice(
                client, meta.get("subject", ""), meta.get("exam", "期末"), slice_, 1,
                meta.get("ratios", {}), teacher_text,
                requirements=meta.get("requirements", ""), knobs=meta.get("knobs", {}),
                bloom=meta.get("bloom", None))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"重掷失败：{e}") from e
    if not new_qs:
        raise HTTPException(502, "模型未返回有效题目，请重试")
    new_q = new_qs[0]
    new_q["id"] = q["id"]
    idx = questions.index(q)
    questions[idx] = new_q
    write_json_atomic(f, questions)  # v0.5：原子写（旧实现裸 write_text）
    rendered = _rerender_project(base, questions, meta)
    return {"ok": True, "question": new_q, "rendered": rendered,
            "usage": uctx.snapshot(),  # v0.5：重掷独立记账随响应返回
            "issues": options_check.check_all([new_q])["issues"]}
