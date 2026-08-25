"""MedQC：LLM-as-judge 分批质检（无金标准模式；U3：批次并发 ≤3）。"""

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import load_prompt

BATCH_SIZE = 20
MAX_WORKERS = 3


def _question_payload(q: dict[str, Any], source_text: str) -> dict[str, Any]:
    return {
        "id": q.get("id", ""), "type": q.get("type", ""), "bloom": q.get("bloom", ""),
        "question": q.get("question", ""), "options": q.get("options", []),
        "answer": q.get("answer", ""), "analysis": q.get("analysis", ""),
        "source_slice": source_text[:1500],
    }


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
        return {
            "issues": out.get("issues", []),
            "score": int(out.get("score", 70)),
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
