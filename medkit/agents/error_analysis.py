"""ErrorAnalysis：单道错题 → 归因 / 考点定位 / 修正陈述 / 反事实问题（EP-01）。

契约（ADR-003）：``chat_json(schema=ErrorAnalysis)`` 硬校验——输出未过契约抛 LLMError，
调用方走人工复核（**不静默丢弃**：错题本体已在库里，只是少了 AI 归因，不影响记录）。

红线（《总纲》§3.2）：**正确答案必须由用户提供，AI 只负责解释和定位。**
本模块**不接受** `correct` 以外的答案来源，也不把模型输出写回 `correct`/`answer` 字段。
提示词里明确「不得改写 correct」；契约 `ErrorAnalysis` 里根本没有 answer 字段——
**双层保证模型没通道改答案**。

零后端：只消费错题记录，不写库——落库由 `core/errorpipe.py` 负责。
"""

import logging
from typing import Any

from ..core.schema import ErrorAnalysis
from . import render_prompt

logger = logging.getLogger(__name__)

# 题干截断上限：控 token。超长题干（案例组）截断后仍足以归因，
# 且在 evidence 里会体现"依据答案偏离方向"的降级判据。
STEM_MAX_CHARS = 3000

# 用户原话（my_reasoning）上限：提示词要求一句话，契约侧留余量。
REASONING_MAX_CHARS = 200


def _payload(rec: dict[str, Any]) -> dict[str, str]:
    """错题记录 → 提示词占位符（控 token；不臆造字段）。"""
    options = rec.get("options") or []
    if isinstance(options, (list, tuple)):
        opts_text = "\n".join(str(o) for o in options)
    else:
        opts_text = str(options)
    return {
        "stem": str(rec.get("question") or "")[:STEM_MAX_CHARS],
        "options": opts_text[:2000],
        "my_answer": str(rec.get("user_answer") or ""),
        # correct 由用户提供，原样传入，不做任何纠偏（红线）
        "correct": str(rec.get("answer") or ""),
        "confidence": str(rec.get("confidence") if rec.get("confidence") is not None else "未填"),
        "my_reasoning": str(rec.get("my_reasoning") or "")[:REASONING_MAX_CHARS] or "（未填写）",
    }


def analyze(client: Any, rec: dict[str, Any]) -> dict[str, Any]:
    """单道错题 → 归因结果 dict（契约校验失败抛 LLMError）。

    返回的 dict 已 `model_dump()`，调用方直接合并进错题记录即可。
    """
    from ..core.llm import LLMError

    system = render_prompt("error_analysis.md", **_payload(rec))
    data = client.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": "请按上述要求，对这道错题做归因分析，输出 JSON。"}],
        temperature=0.2, max_tokens=2000, schema=ErrorAnalysis)
    if not isinstance(data, ErrorAnalysis):
        try:
            data = ErrorAnalysis.model_validate(data)
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"ErrorAnalysis 输出未通过契约: {e}") from e
    out = data.model_dump()
    # 防御：即便模型在 JSON 里塞了 correct/answer，也不允许它进入归因结果
    for forbidden in ("correct", "answer", "user_answer"):
        out.pop(forbidden, None)
    return out


def make_client() -> Any:
    from . import get_client
    return get_client("gen")
