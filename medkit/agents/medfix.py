"""MedFix：按质检 issue 定向修复（一次调用处理全部 fail 题）。"""

import json
import logging
from typing import Any

from . import load_prompt

logger = logging.getLogger(__name__)

# 合并策略下取原题的字段（溯源/结构），其余内容字段取新题
PROVENANCE_KEYS = ("sid", "module", "subtopic", "type", "case_id")


def fix_questions(client: Any, questions: list[dict[str, Any]],
                  issues: list[dict[str, Any]],
                  slice_by_sid: dict[str, str]) -> dict[str, Any]:
    """输入全部题目 + issues；返回 {fixed: [...], trace: [...]}。

    fixed 为修复后的整题对象（仅含被修复的题）；trace 为追溯记录。
    合并策略：sid/module/subtopic/type/case_id 取原题（源切片追溯不被覆盖），
    内容字段（题面/选项/答案/解析）取新题。
    """
    by_id = {q.get("id"): q for q in questions if q.get("id")}
    fail_ids = {x["q_id"] for x in issues if x.get("severity") in ("fail", "warn")}
    unknown = sorted(fail_ids - set(by_id))
    if unknown:
        logger.warning("MedFix 跳过未命中现有题目的 issue（q_id=%s）", unknown)
    fail_ids &= set(by_id)
    target = [q for q in questions if q.get("id") in fail_ids]
    if not target:
        return {"fixed": [], "trace": []}

    payload = [{
        "id": q.get("id", ""), "type": q.get("type", ""), "bloom": q.get("bloom", ""),
        "question": q.get("question", ""), "options": q.get("options", []),
        "answer": q.get("answer", ""), "analysis": q.get("analysis", ""),
        "issues": [x for x in issues if x.get("q_id") == q.get("id")],
        "source_slice": slice_by_sid.get(q.get("sid", ""), "")[:1500],
    } for q in target]

    system = load_prompt("medfix.md")
    out = client.chat_json([{"role": "system", "content": system},
                            {"role": "user",
                             "content": json.dumps({"questions": payload},
                                                   ensure_ascii=False)}],
                           temperature=0.3)
    fixed_raw = out.get("questions", []) if isinstance(out, dict) else []
    fixed: list[dict[str, Any]] = []
    for fq in fixed_raw:
        if not isinstance(fq, dict):
            continue
        orig = by_id.get(fq.get("id"))
        if orig is None:
            logger.warning("MedFix 返回未知题目 id，已跳过：%s", fq.get("id"))
            continue
        merged = dict(fq)
        for k in PROVENANCE_KEYS:
            if orig.get(k):
                merged[k] = orig[k]
        fixed.append(merged)
    trace = [{"q_id": q.get("id"), "fixed": True,
              "reason": "QC fail 定向修复"} for q in fixed]
    return {"fixed": fixed, "trace": trace}


def make_client() -> Any:
    from . import get_client
    return get_client("gen")
