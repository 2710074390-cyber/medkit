"""MedQC：LLM-as-judge 分批质检（无金标准模式；U3：批次并发 ≤3）。"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from pydantic import ValidationError

from ..core.schema import QcVerdict, validate_or_repair
from . import load_prompt

logger = logging.getLogger(__name__)

BATCH_SIZE = 20
MAX_WORKERS = 3


def _question_payload(q: dict[str, Any], source_text: str) -> dict[str, Any]:
    return {
        "id": q.get("id", ""), "type": q.get("type", ""), "bloom": q.get("bloom", ""),
        "question": q.get("question", ""), "options": q.get("options", []),
        "answer": q.get("answer", ""), "analysis": q.get("analysis", ""),
        "source_slice": source_text[:1500],
    }


def _coerce_score(value: Any) -> tuple[int, str]:
    """score 容错：float/数字字符串 → int；None/非法 → 50 + 说明（warn）。"""
    if value is None:
        return 50, "score 缺失（None），回退 50 分"
    try:
        num = float(value)
        if num != num or num in (float("inf"), float("-inf")):  # NaN / Inf
            raise ValueError
        return int(num), ""
    except (TypeError, ValueError):
        return 50, f"score 非法（{value!r}），回退 50 分"


def _normalize_issues(raw: Any) -> list[dict[str, Any]]:
    """severity 统一小写；过滤非 dict 项。"""
    issues: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return issues
    for it in raw:
        if not isinstance(it, dict):
            continue
        it = dict(it)
        sev = it.get("severity")
        if sev is not None:
            it["severity"] = str(sev).lower()
        issues.append(it)
    return issues


def _repair_verdict(client: Any, system: str, raw: Any, exc: ValidationError) -> Any:
    """NX-03（R-2）：契约校验失败 → 把错误明细带回重发一次（ADR-003 修复-重试闭环）。

    返回可再校验的 dict；重发异常/非 dict → None（调用方走 score=-1 不计分 + 人工复核）。
    """
    errs = [{"loc": list(e.get("loc") or []), "msg": str(e.get("msg", e))}
            for e in exc.errors()[:10]]
    try:
        out = client.chat_json([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({
                "notice": "你上一轮输出未满足质检报告 JSON 契约，请按契约修复后完整重发（仅 JSON）",
                "previous_output": raw if isinstance(raw, dict) else {"raw": raw},
                "validation_errors": errs}, ensure_ascii=False)},
        ], temperature=0.2)
        return out if isinstance(out, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _qc_batch_once(client: Any, batch: list[dict[str, Any]],
                   slice_by_sid: dict[str, str]) -> dict[str, Any]:
    payload = [{"q": _question_payload(q, slice_by_sid.get(q.get("sid", ""), ""))}
               for q in batch]
    system = load_prompt("medqc.md")
    try:
        out = client.chat_json([
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(
                {"questions": payload}, ensure_ascii=False)},
        ], temperature=0.2)
        out = out if isinstance(out, dict) else {}
        # NX-03（R-2 返工）：契约硬闭环——校验失败 → 带错误重发 1 次修复 → 仍失败 →
        # score=-1 不计分 + fail 问题进人工复核（既有「score=-1 不计分」兜底语义，对齐 TutorTurn）。
        verdict = validate_or_repair(out, QcVerdict,
                                     lambda raw, exc: _repair_verdict(client, system, raw, exc))
        if verdict is None:
            return {
                "issues": [{"q_id": "QC_CONTRACT", "code": "QC_CONTRACT", "severity": "fail",
                            "reason": "质检输出两次未通过 QcVerdict 契约（首次+带错误重发仍失败）"
                                      "→ score=-1 不计分，待人工复核"}],
                "score": -1, "decision": "BLOCKED",
                "summary": "契约校验失败（已带错误重发一次仍失败），待人工复核",
            }
        out = verdict.model_dump()
        issues = _normalize_issues(out.get("issues"))
        score, score_warn = _coerce_score(out.get("score"))
        if score_warn:
            issues.append({"q_id": "QC_SCORE", "code": "QC_SCORE", "severity": "warn",
                           "reason": score_warn})
        return {
            "issues": issues,
            "score": score,
            "decision": out.get("gate_decision", "PASS_WITH_FIXES"),
            "summary": out.get("summary", ""),
        }
    except Exception as e:  # noqa: BLE001  单批失败不中断整体
        return {
            "issues": [{"q_id": "QC_ERR", "code": "QC_ERR", "severity": "warn",
                        "reason": f"质检批次异常：{e}"}],
            "score": 50, "decision": "PASS_WITH_FIXES", "summary": "",
        }


def qc_batch(client: Any, questions: list[dict[str, Any]],
             slice_by_sid: dict[str, str],
             concurrency: int = MAX_WORKERS,
             on_progress: Optional[Callable[[int, int], None]] = None) -> dict[str, Any]:
    """分批质检（并发），聚合报告（按批次顺序）。

    ``on_progress(done, total)``：每完成一批回调（长任务进度可见性；QL-2026-08）。
    """
    if not questions:
        # 空题库：跳过该批 + warn（原逻辑会把空列表判成 PASS 0 分）
        return {"score": 0, "gate_decision": "PASS_WITH_FIXES",
                "issues": [{"q_id": "ALL", "code": "EMPTY_BANK", "severity": "warn",
                            "reason": "空题库，跳过质检（0 分不视为 PASS）"}],
                "summary": "空题库"}
    batches = [questions[i:i + BATCH_SIZE] for i in range(0, len(questions), BATCH_SIZE)]
    results: list[dict[str, Any]] = [None] * len(batches)  # type: ignore[list-item]

    def run(i: int, batch: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        return i, _qc_batch_once(client, batch, slice_by_sid)

    def _call_progress(done: int) -> None:
        if on_progress:
            try:
                on_progress(done, len(batches))
            except Exception:  # noqa: BLE001  进度回调失败不阻断质检
                pass

    if len(batches) <= 1 or concurrency <= 1:
        for i, b in enumerate(batches):
            results[i] = _qc_batch_once(client, b, slice_by_sid)
            _call_progress(i + 1)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            for i, r in ex.map(run, range(len(batches)), batches):
                results[i] = r
                _call_progress(i + 1)

    issues: list[dict[str, Any]] = []
    scores: list[int] = []
    decisions: set[str] = set()
    summaries: list[str] = []
    for r in results:
        issues += r["issues"]
        scores.append(r["score"])
        decisions.add(r["decision"])
        summaries.append(r["summary"])

    # 聚合决策：存在 fail → BLOCKED；否则有 warn 或有 QC_ERR → PASS_WITH_FIXES
    has_fail = any(x.get("severity") == "fail" for x in issues)
    has_warn = any(x.get("severity") == "warn" for x in issues)
    decision = "BLOCKED" if has_fail else ("PASS_WITH_FIXES" if has_warn else "PASS")
    # NX-03：契约失败批次 score=-1 不计入平均分；全部不可计分 → 整体 -1
    countable = [s for s in scores if s >= 0]
    score = round(sum(countable) / max(len(countable), 1), 1) if countable else -1
    return {
        "score": score,
        "gate_decision": decision,
        "issues": issues,
        "summary": "；".join(x for x in summaries if x)[:600],
    }


def make_client() -> Any:
    from . import get_client
    return get_client("qc")
