"""routers：逐题审核台（列表 / keep-drop-edits 重渲染 / 单题重掷）。

运行中（RUNNING）时 review/regen 返回 409（v0.5），避免与出题线程并发写盘。
"""

import json
import re
import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents import medgen
from ..core import config as cfg
from ..core import usage as usage_mod
from ..core.config import resolve_key
from ..core.fsutil import safe_filename, write_json_atomic
from ..gates import options_check
from ..state import RUNNING
from ._common import _log_project, _read_meta_checked, _safe_pid, _write_meta_atomic, proj_dir

router = APIRouter()

# R3-07：审核/重掷/重渲染 per-pid 写锁——三者「读→改→原子写→重渲染」串行化，
# 防止快速连点/双标签并发后写覆盖先写（静默丢编辑）。
_LOCK_GUARD = threading.Lock()
_PID_LOCKS: dict[str, threading.Lock] = {}


def _pid_lock(pid: str) -> threading.Lock:
    with _LOCK_GUARD:
        lock = _PID_LOCKS.get(pid)
        if lock is None:
            lock = _PID_LOCKS[pid] = threading.Lock()
        return lock


@router.get("/api/projects/{pid}/questions")
def project_questions(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成，请先「开始生成」")
    questions = json.loads(f.read_text(encoding="utf-8"))
    # v0.8.1 真题标注兜底：老项目题目无 source 字段 → 读取时实时标注（不写回文件）
    from ..core import realexams as _rex
    try:
        _rex.annotate_questions(questions, meta.get("subject", ""))
    except Exception:  # noqa: BLE001  标注失败不阻断题目读取
        pass
    return {"questions": questions}


def _rerender_project(base, questions: list[dict[str, Any]], meta: dict[str, Any],
                      which: str | None = None) -> list[str]:
    """审核后重渲染产物（复用渲染层；复习手册若有旧 MD 则重转 HTML）。

    which（B17「仅重渲染此产物」）：None=全部；"qbank"=题库 md/html；"paper"=押题卷；
    "review"=复习手册 html；"anki"=anki_export.txt + .apkg。

    C-09：渲染前对已有产物文件做字节快照；渲染抛异常时恢复快照、删除渲染中
    新建的文件，再上抛——磁盘题库/押题卷/Anki 与「已回滚」文案保持一致。
    """
    from ..core.orchestrator import build_image_index
    subject = meta.get("subject", "")
    toggles = meta.get("toggles", {})
    # v0.8.1 真题标注：渲染前补齐 source_type/source_year（老项目产物同样带标签；幂等）
    try:
        from ..core import realexams as _rex
        _rex.annotate_questions(questions, subject)
    except Exception:  # noqa: BLE001
        pass
    out_dir = base / "最终产物"
    out_dir.mkdir(exist_ok=True)
    # R3S-02：从 slices.json 重建 image_index（初跑管线传索引，重渲染必须同口径，否则图题全丢图）
    image_index, _ = build_image_index(base)
    snap: dict[str, bytes] = {}
    for _p in out_dir.iterdir():
        if _p.is_file():
            try:
                snap[_p.name] = _p.read_bytes()
            except OSError:  # noqa: BLE001  读快照失败：该文件不纳入回滚面
                pass
    try:
        return _render_project_artifacts(base, questions, meta, which, subject, toggles,
                                         out_dir, image_index)
    except Exception:
        # C-09：回滚产物文件集——恢复快照中的原字节，删除本次渲染新建的文件
        try:
            for _p in out_dir.iterdir():
                if _p.is_file() and _p.name not in snap:
                    _p.unlink(missing_ok=True)
            for _name, _data in snap.items():
                (out_dir / _name).write_bytes(_data)
        except Exception:  # noqa: BLE001  回滚失败：不吞原始异常
            pass
        raise


def _render_project_artifacts(base, questions: list[dict[str, Any]], meta: dict[str, Any],
                              which: str | None, subject: str, toggles: dict[str, Any],
                              out_dir, image_index) -> list[str]:
    """_rerender_project 的渲染主体（C-09 快照回滚包裹在调用方）。"""
    from ..core.orchestrator import select_paper_stable
    from ..render import qbank_html, review_html

    rendered: list[str] = []
    full = which is None
    if full or which == "qbank":
        qbank_md = qbank_html.export_md(questions, f"{subject} 题库")
        (out_dir / "qbank.md").write_text(qbank_md, encoding="utf-8")
        (out_dir / "qbank.html").write_text(
            qbank_html.export_html(questions, f"{subject} 题库", pid=base.name,
                                   image_index=image_index),
            encoding="utf-8")
        rendered += ["qbank.md", "qbank.html"]
    if (full or which == "paper") and toggles.get("paper", True):
        # ME-7：押题卷复用上次抽样的题目 id（仅当题库变化致数量不足时重新抽样）——
        # 否则审核台只改一题解析也会让押题卷 50 题「换一批」。
        ids: list[str] = []
        ids_path = out_dir / "paper_ids.json"
        if ids_path.exists():
            try:
                ids = list(json.loads(ids_path.read_text(encoding="utf-8")).get("ids", []))
            except Exception:  # noqa: BLE001
                ids = []
        paper_qs = select_paper_stable(ids, questions)
        (out_dir / "押题卷.html").write_text(
            qbank_html.export_paper_html(paper_qs, f"{subject} 押题卷",
                                         pid=base.name, subject=subject,
                                         image_index=image_index), encoding="utf-8")
        try:
            ids_path.write_text(
                json.dumps({"ids": [q.get("id") for q in paper_qs]}, ensure_ascii=False),
                encoding="utf-8")
        except OSError:  # noqa: BLE001
            pass
        rendered.append("押题卷.html")
    review_md_path = out_dir / "复习手册.md"
    if (full or which == "review") and review_md_path.exists():
        (out_dir / "复习手册.html").write_text(
            review_html.review_to_html(review_md_path.read_text(encoding="utf-8"),
                                       f"{subject} 复习手册", out_dir=out_dir),
            encoding="utf-8")
        rendered.append("复习手册.html")
    if full or which == "anki":
        (out_dir / "anki_export.txt").write_text(
            qbank_html.export_anki(questions, f"{subject} 题库"), encoding="utf-8")
        rendered.append("anki_export.txt")
        try:  # S3：审核后同步重生成 .apkg（失败不阻断其余产物）
            from ..render.apkg import export_apkg

            apkg_path = out_dir / f"{safe_filename(subject)} 题库.apkg"
            export_apkg(questions, subject, base.name, apkg_path)
            rendered.append(apkg_path.name)
        except Exception as e:  # noqa: BLE001
            _log_project(base, f"⚠️ .apkg 重生成失败：{e}")
    if full:
        meta["final_count"] = len(questions)
        _write_meta_atomic(base, meta)
    _log_project(base, f"✏️ 审核后重渲染：{len(questions)} 题（{', '.join(rendered)}）")
    return rendered


class ReviewBody(BaseModel):
    # C-10：keep 未传 = 全保留；keep=[] = 明确「全部剔除」→ 拒绝（前端把剔除编码进 keep）
    keep: list[str] | None = None
    drop: list[str] = []
    edits: list[dict[str, Any]] = []


_ANSWER_PUNCT_RE = re.compile(r"[\s,，、;；]+")


def _norm_answer(s: Any) -> str:
    """C-11：答案归一化第三口径——去空格并剥离中英文逗号/顿号/分号（B,D 与 B，D → BD）。"""
    return _ANSWER_PUNCT_RE.sub("", str(s or "")).upper()


def _option_group_key(q: dict[str, Any]) -> Any:
    """选项组身份键：优先 group.id；无 id 回退选项元组（与渲染层 _case_blocks 同口径）。"""
    grp = q.get("group") if isinstance(q.get("group"), dict) else {}
    gid = grp.get("id")
    return gid if gid is not None else tuple(str(o) for o in (grp.get("options") or []))


def _sync_option_group(questions: list[dict[str, Any]], q: dict[str, Any],
                       new_options: list[Any]) -> None:
    """R3-11：把新选项写进 group.options 并同步组内所有成员（不再子题各存一套）。"""
    key = _option_group_key(q)
    for other in questions:
        if other.get("group_kind") != "option_group":
            continue
        if not isinstance(other.get("group"), dict):
            continue
        if _option_group_key(other) == key:
            other["group"]["options"] = list(new_options)


def _answer_issue(q: dict[str, Any]) -> str | None:
    """B10/C-11：行内编辑后的答案键/题型合法性校验（R0 口径；第三口径归一化容忍）。

    返回错误文案或 None。A1/A2/A3/A4/B1 → 单字母；X 型 → ≥2 字母且不重复、
    字母均在选项范围内（B1 用共享 group.options）。
    """
    answer = _norm_answer(q.get("answer", ""))
    qtype = str(q.get("type", "") or "")
    if qtype not in options_check.ALLOWED_TYPES:
        return f"题型非法「{qtype}」"
    if not answer:
        return "答案键不能为空"
    opts = q.get("options") or []
    if not opts and q.get("group_kind") == "option_group" and isinstance(q.get("group"), dict):
        opts = (q.get("group") or {}).get("options") or []
    n_opts = len([o for o in opts if isinstance(o, str)])
    if n_opts <= 0:
        return "题目没有选项，无法校验答案键"
    # R3-06：letters 按实际选项数（去掉 max(...,4) 地板）——3 选项题答案 D 不再被放行
    letters = "ABCDEFGHIJ"[:n_opts]
    if qtype != "X" and len(answer) != 1:
        return f"单选/案例题答案应为单字母（当前「{answer}」）"
    if qtype == "X" and len(answer) < 2:
        return f"X 型答案至少 2 个字母（当前「{answer}」）"
    if any(c not in letters for c in answer):
        return f"含选项字母范围外字符（选项 A~{letters[-1]}）"
    return None


@router.post("/api/projects/{pid}/questions/review")
def review_questions(pid: str, body: ReviewBody) -> dict[str, Any]:
    """keep/drop/edits → 覆盖题库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中审核 → 409（旧实现与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可审核（请等待完成或先停止）")
    with _pid_lock(pid):
        return _review_questions_locked(pid, body)


def _review_questions_locked(pid: str, body: ReviewBody) -> dict[str, Any]:
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    if body.keep is not None and not body.keep:
        # C-10：明确剔除全部（keep=[]）→ 400 并引导；未传 keep 才是「全保留」
        raise HTTPException(400, "保留题数为 0，请至少保留一题（如整卷作废可在项目列表删除项目）")
    keep_set = set(body.keep) if body.keep is not None else None   # 未传 = 全保留
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
            # R3-15：只校验本批实际改动到 answer/options/type 的题——未改动题即使
            # 历史非法键也不阻断保存（前端已逐行校验，后端只守「动过的」）
            touched = any(k in e for k in ("answer", "options", "type"))
            for k in ("question", "options", "answer", "analysis", "bloom", "type", "subtopic"):
                if k in e:
                    q[k] = e[k]
            # R3-11：编辑选项时写 group.options 并同步组内所有成员（渲染/聚合同口径）
            if q.get("group_kind") == "option_group" and "options" in e and isinstance(q.get("group"), dict):
                _sync_option_group(questions, q, e["options"])
            # C-11：答案保存为归一化紧凑形式（B,D → BD）
            if "answer" in e:
                q["answer"] = _norm_answer(e["answer"])
            # B10：编辑后答案键/题型合法性校验（防污染产物与判分）
            if touched:
                issue = _answer_issue(q)
                if issue:
                    raise HTTPException(400, f"题目 {qid} 答案键有误：{issue}（请修正或恢复原答案再保存）")
        out.append(q)
    if not out:
        raise HTTPException(400, "保留题数为 0，请至少保留一题")
    # 先备份旧题库再写；重渲染失败时回滚 JSON——避免「编辑已持久化但产物未更新」的不一致态
    bak = f.with_suffix(".json.review-bak")
    try:
        bak.write_bytes(f.read_bytes())
    except OSError:
        pass
    write_json_atomic(f, out)
    try:
        rendered = _rerender_project(base, out, meta)
    except Exception as e:  # noqa: BLE001
        if bak.exists():
            try:
                write_json_atomic(f, json.loads(bak.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001  回滚失败：保留备份文件供手工恢复
                pass
        raise HTTPException(500, f"重渲染失败，已回滚本次修改：{type(e).__name__}（见日志；可稍后重试）") from e
    finally:
        bak.unlink(missing_ok=True)
    return {"ok": True, "questions": len(out), "rendered": rendered}


class RegenBody(BaseModel):
    id: str


class RerenderBody(BaseModel):
    what: str = "all"


@router.post("/api/projects/{pid}/rerender")
def rerender_project(pid: str, body: RerenderBody) -> dict[str, Any]:
    """B17：仅重渲染指定产物（不重跑管线、无 token 消耗；题库内容不变）。

    what ∈ {qbank, paper, review, anki}（缺省/all = 全部，等价审核保存后的重渲染）。
    """
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # 与审核同策略：生成中禁止并发渲染
        raise HTTPException(409, "项目正在生成中，暂不可重渲染（请等待完成或先停止）")
    with _pid_lock(pid):
        return _rerender_project_locked(pid, body.what)


def _rerender_project_locked(pid: str, what_raw: str) -> dict[str, Any]:
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成，无法重渲染")
    questions = json.loads(f.read_text(encoding="utf-8"))
    what = what_raw if what_raw in ("qbank", "paper", "review", "anki") else None
    try:
        rendered = _rerender_project(base, questions, meta, what)
    except Exception as e:  # noqa: BLE001
        _log_project(base, f"❌ 仅重渲染失败：{type(e).__name__}: {e}")
        raise HTTPException(500, f"重渲染失败：{type(e).__name__}（详见日志）") from e
    if not rendered:
        raise HTTPException(400, "没有可重渲染的产物（复习手册.html 需要先有「复习手册.md」；押题卷需开启产物开关）")
    return {"ok": True, "rendered": rendered}


@router.post("/api/projects/{pid}/regen")
def regen_question(pid: str, body: RegenBody) -> dict[str, Any]:
    """按 q.sid 找回原切片，单题重掷（generate_slice count=1），替换入库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中重掷 → 409（避免与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可重掷（请等待完成或先停止）")
    with _pid_lock(pid):
        return _regen_question_locked(pid, body.id)


def _regen_question_locked(pid: str, qid: str) -> dict[str, Any]:
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    q = next((x for x in questions if x.get("id") == qid), None)
    if q is None:
        raise HTTPException(404, f"题目 {qid} 不存在")
    # B12：案例组/选项组子题与图/表题重掷会破坏组结构或 image_ref 引用——禁止并引导行内编辑
    if (q.get("group_kind") in ("case", "option_group") or q.get("case_id")
            or q.get("image_ref") or q.get("data_table")):
        raise HTTPException(400, "该题属于案例组/选项组或含图/表，重掷会破坏组结构或图题引用；请改用「编辑」修改题干/解析/选项")
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
    # C-16：重掷返回的新题若带案例组字段 → 剥离为独立题并记录（B12 只拦旧题，这里兜底新题）
    stripped_fields: list[str] = []
    for _f in ("case_id", "group_kind", "case_stem", "case_order"):
        if _f in new_q:
            del new_q[_f]
            stripped_fields.append(_f)
    if stripped_fields:
        _log_project(base, f"⚠️ 重掷 {qid}：新题含案例组字段（{', '.join(stripped_fields)}），已剥离为独立题，请检查题干是否完整")
    idx = questions.index(q)
    questions[idx] = new_q
    # 与审核保存同策略：备份 → 写 → 重渲染失败回滚
    bak = f.with_suffix(".json.review-bak")
    try:
        bak.write_bytes(f.read_bytes())
    except OSError:
        pass
    write_json_atomic(f, questions)  # v0.5：原子写（旧实现裸 write_text）
    try:
        rendered = _rerender_project(base, questions, meta)
    except Exception as e:  # noqa: BLE001
        if bak.exists():
            try:
                write_json_atomic(f, json.loads(bak.read_text(encoding="utf-8")))
            except Exception:  # noqa: BLE001
                pass
        raise HTTPException(500, f"重掷后重渲染失败，已回滚：{type(e).__name__}（见日志）") from e
    finally:
        bak.unlink(missing_ok=True)
    resp: dict[str, Any] = {"ok": True, "question": new_q, "rendered": rendered,
                          "usage": uctx.snapshot(),  # v0.5：重掷独立记账随响应返回
                          "issues": options_check.check_all([new_q])["issues"]}
    if stripped_fields:
        resp["warning"] = "新题含案例组字段，已剥离为独立题，请检查题干是否完整"
    return resp
