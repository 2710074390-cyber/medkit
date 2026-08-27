"""MedQC：LLM-as-judge 分批质检（无金标准模式；U3：批次并发 ≤3）。"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from pydantic import ValidationError

from ..core.schema import QcVerdict
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
        # IMP-03：QcVerdict 契约校验（软校验——校验失败仅记告警，不回退批次。
        # 浮点容错语义由下方 _coerce_score / _normalize_issues 保留，行为保持不变。）
        try:
            QcVerdict.model_validate(out)
        except ValidationError as e:
            first = e.errors()[0] if e.errors() else {}
            logger.warning("MedQC 输出未通过 QcVerdict 契约：%s", first.get("msg", str(e)))
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
             concurrency: int = MAX_WORKERS) -> dict[str, Any]:
    """分批质检（并发），聚合报告（按批次顺序）。"""
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

    if len(batches) <= 1 or concurrency <= 1:
        for i, b in enumerate(batches):
            results[i] = _qc_batch_once(client, b, slice_by_sid)
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as ex:
            for i, r in ex.map(run, range(len(batches)), batches):
                results[i] = r

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
    return {
        "score": round(sum(scores) / max(len(scores), 1), 1),
        "gate_decision": decision,
        "issues": issues,
        "summary": "；".join(x for x in summaries if x)[:600],
    }


def make_client() -> Any:
    from . import get_client
    return get_client("qc")
