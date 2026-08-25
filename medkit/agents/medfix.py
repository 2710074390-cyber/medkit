"""MedFix：按质检 issue 定向修复（一次调用处理全部 fail 题）。"""

import json
from typing import Any

from . import load_prompt


def fix_questions(client: Any, questions: list[dict[str, Any]],
                  issues: list[dict[str, Any]],
                  slice_by_sid: dict[str, str]) -> dict[str, Any]:
    """输入全部题目 + issues；返回 {fixed: [...], trace: [...]}。

    fixed 为修复后的整题对象（仅含被修复的题）；trace 为追溯记录。
    """
    fail_ids = {x["q_id"] for x in issues if x.get("severity") in ("fail", "warn")}
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
    fixed = out.get("questions", []) if isinstance(out, dict) else []
    trace = [{"q_id": q.get("id"), "fixed": True,
              "reason": "QC fail 定向修复"} for q in fixed]
    return {"fixed": fixed, "trace": trace}


def make_client() -> Any:
    from . import get_client
    return get_client("gen")
