"""MedCards：讲解产物 → 3~6 张医学记忆卡（WP-05/NX-04）。

契约（ADR-003）：``chat_json(schema=CardDrafts)`` 硬校验——LLM 输出未过契约抛 LLMError
（调用方回退：不生成，提示重试/人工复核）。卡片类型：value（数值）/mnemonic（口诀）/
contrast（鉴别）/concept（概念）。

零后端：只消费讲解产物文本（content + 标题 + 科目），不写库——落库由 core/cards.py 负责。
"""

import logging
from typing import Any

from ..core.schema import CardDrafts
from . import load_prompt

logger = logging.getLogger(__name__)

MIN_CARDS = 3
MAX_CARDS = 6


def _explain_payload(rec: dict[str, Any]) -> dict[str, Any]:
    """讲解产物 → 摘要化请求（控 token，全文交提示词红线约束不臆造）。"""
    return {
        "subject": rec.get("subject") or "",
        "kp_name": rec.get("kp_name") or "",
        "content": (rec.get("content") or "")[:6000],
    }


def generate_cards(client: Any, rec: dict[str, Any]) -> list[dict[str, Any]]:
    """一篇讲解 → 记忆卡草稿（契约校验失败抛 LLMError；调用方负责提示与兜底）。"""
    from ..core.llm import LLMError

    system = load_prompt("medcards.md")
    user = _explain_payload(rec)
    data = client.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": f"科目：{user['subject']}\n知识点：{user['kp_name']}\n"
                                     f"讲解全文：\n【内容】\n{user['content']}\n[/内容]"}],
        temperature=0.3, max_tokens=4000, schema=CardDrafts)
    if not isinstance(data, CardDrafts):  # chat_json(schema=) 正常返回已校验模型
        try:
            data = CardDrafts.model_validate(data)
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"MedCards 输出未通过 CardDrafts 契约: {e}") from e
    cards = [c.model_dump() for c in data.cards]
    if len(cards) < MIN_CARDS:
        logger.warning("MedCards 仅生成 %d 张（期望 %d~%d），按实有保存", len(cards), MIN_CARDS, MAX_CARDS)
    return cards


def make_client() -> Any:
    from . import get_client
    return get_client("gen")
