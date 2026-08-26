"""routers：出题管线（run / cancel / trial / 成本预估）。

U1/U2：可取消 + 断点续跑；成本预估接口统一前端 formula（S2：原 JS 内嵌公式删除）。
"""

import threading
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..agents import medgen
from ..agents import medgen as _mg
from ..core import config as cfg
from ..core import usage as usage_mod
from ..core.config import resolve_key
from ..core.cost import estimate_run
from ..core.llm import LLMClient
from ..gates import options_check, trace_check
from ..state import RUN_LOCK, RUNNING
from ._common import _log_project, _read_meta_checked, _safe_pid, _write_meta_atomic, proj_dir

router = APIRouter()


@router.post("/api/projects/{pid}/run")
def run_project(pid: str) -> dict[str, Any]:
    """启动出题管线（后台线程，前端轮询 /status）；断点存在时自动续跑。"""
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    meta_ = _read_meta_checked(base)
    if meta_.get("stage") == "done":
        raise HTTPException(409, "该项目已完成生成（如需重跑请删除后重建）")
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先在「① 连接服务商」保存 API Key（留空会保留旧值）")
    if not c.get("model_gen"):
        raise HTTPException(400, "请先配置生成模型")
    with RUN_LOCK:
        if RUNNING.get(pid):
            raise HTTPException(409, "该项目正在生成中（可点「停止」或稍候查看 /status）")
        ev = threading.Event()
        RUNNING[pid] = ev
    threading.Thread(target=_run_pipeline_thread, args=(pid, ev), daemon=True).start()
    return {"ok": True, "started": True}


@router.delete("/api/projects/{pid}/run")
def cancel_project(pid: str) -> dict[str, Any]:
    """U1：取消生成（保留已生成题目与断点，可再次「开始生成」续跑）。"""
    pid = _safe_pid(pid)
    with RUN_LOCK:
        ev = RUNNING.get(pid)
    if not ev:
        raise HTTPException(404, "项目当前未在生成中")
    ev.set()
    return {"ok": True, "cancelling": True}


def _run_pipeline_thread(pid: str, cancel_ev: threading.Event) -> None:
    try:
        from ..core.orchestrator import PipelineCancelled
        from ..core.orchestrator import run_project as _rp
        try:
            res = _rp(pid, cancel=cancel_ev)
            base = proj_dir(pid)
            if res.get("stage") == "cancelled":
                _log_project(base, "⏹ 管线已取消（断点已保留，可继续生成）")
        except PipelineCancelled:
            pass  # 正常取消路径
    except Exception as e:  # noqa: BLE001
        base = proj_dir(pid)
        try:
            _log_project(base, f"❌ 管线失败：{e}")
            meta = _read_meta_checked(base)
            meta["stage"] = "error"
            _write_meta_atomic(base, meta)
        except Exception:  # noqa: BLE001
            pass
    finally:
        with RUN_LOCK:
            RUNNING.pop(pid, None)


# ---------------------------------------------------------------- 试玩三件套（可玩性 迭代1）
class TrialBody(BaseModel):
    subject: str
    exam: str = "期末"
    requirements: str = ""
    knobs: dict[str, str] = {}
    ratios: dict[str, int] = {"A1": 40, "A2": 30, "B1": 20, "X": 10}
    slice_sid: str = "TRIAL"
    slice_title: str = ""
    slice_text: str
    teacher_text: str = ""
    exam_text: str = ""      # v0.5.2：自备真题（考点/风格校准）
    extra_text: str = ""     # v0.5.2：自备资料（补充上下文）


@router.post("/api/trial")
def trial(body: TrialBody) -> dict[str, Any]:
    """试出一题：不创建项目/不落盘/不跑管线；门禁即检，30~90 秒返回。"""
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先在「① 连接服务商」保存 API Key，再试出题")
    if not c.get("model_gen"):
        raise HTTPException(400, "请先配置生成模型")
    if not body.slice_text.strip():
        raise HTTPException(400, "切片内容为空")
    client = LLMClient(c.get("base_url", ""), resolve_key(c.get("api_key", "")),
                       c.get("model_gen", ""), timeout=90)
    slice_ = {"sid": body.slice_sid, "title": body.slice_title, "text": body.slice_text[:6000]}
    with usage_mod.context() as uctx:  # v0.5：试出独立记账（与并行管线互不串账）
        try:
            qs, _ = medgen.generate_slice(
                client, body.subject, body.exam, slice_, 1, body.ratios,
                body.teacher_text[: _mg.TEACHER_CHAR_LIMIT], requirements=body.requirements[:500],
                knobs=body.knobs,
                exam_text=body.exam_text[: _mg.EXAM_CHAR_LIMIT],
                extra_text=body.extra_text[: _mg.EXTRA_CHAR_LIMIT])
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"试出题失败：{e}") from e
    if not qs:
        raise HTTPException(502, "模型未返回有效题目，请重试或检查模型配置")
    issues = options_check.check_all(qs)["issues"]
    issues += trace_check.check_trace(qs, {body.slice_sid})["issues"]
    return {"question": qs[0], "issues": issues,
            "from_slice": f"{body.slice_sid} · {body.slice_title}",
            "usage": uctx.snapshot()}


# ---------------------------------------------------------------- 成本预估（单源公式）
class CostBody(BaseModel):
    chars_textbook: int = 0
    chars_teacher: int = 0
    n_slices: int = 1
    n_questions: int = 100


@router.post("/api/cost/estimate")
def cost_estimate(body: CostBody) -> dict[str, Any]:
    """前端成本预估统一走后端公式（core/cost.estimate_run；旧 JS 内嵌公式删除）。"""
    est = estimate_run(chars_textbook=max(int(body.chars_textbook or 0), 0),
                       chars_teacher=max(int(body.chars_teacher or 0), 0),
                       n_slices=max(int(body.n_slices or 1), 1),
                       n_questions=max(int(body.n_questions or 1), 1))
    return {"input_tokens": est["input_tokens"], "output_tokens": est["output_tokens"],
            "total_tokens": est["total_tokens"]}
