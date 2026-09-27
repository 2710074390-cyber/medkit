"""SocraticReview：苏格拉底式错题复习（EP-01 阶段 3）。

与 ``agents/medtutor.py`` 的分工：

- ``medtutor``：从一个**知识点**出发，用概念阶梯引导学生学透（起点=未知）。
- 本模块：从一道**做错且已归因的错题**出发，用追问把学生逼回**他当初犯错的那个岔路口**
  （起点=学歪了）。故本模块的输入必须带 ``my_reasoning`` 与 ``error_tag``。

**红线（《总纲》§3.2）**：正确答案只用于"让模型别问偏"，**不得转述给学生**。
本模块在提示词层（`socratic_review.md`）与契约层（`schema.SocraticScore` 无 answer 字段）
各设一道防线；第三道是路由层的端到端守卫（用真答案串做注入，断言不出现在任何返回文本里）。

判分口径与 medtutor 一致：**无法判定 → -1**（不计分、不记轮次、请重答），
绝不为了"给个反馈"而编一个分数出来。
"""

from typing import Any, Optional

from ..core import errors as errs
from ..core.llm import LLMError
from ..core.schema import SocraticScore, validate_or_repair
from . import render_prompt

# 与 core/tutor.QUESTION_TYPES 同源（此处复制一份常量用于防御性校验；
# 提示词的 {qtype} 由调用方给出，模型不得自造）。
_QTYPES = ("explain", "apply", "contrast", "predict", "trace")


def needs_client_and_price(stem: str, my_reasoning: str) -> int:
    """粗估输入 token（供 cost toast，不含输出；含题干 + 原始想法 + 历史约 3 轮）。"""
    return 700 + len(stem) * 2 + len(my_reasoning) * 2


def _history_digest(history: Optional[list[dict[str, Any]]]) -> str:
    """仅最近 3 轮（防止上下文膨胀，与 medtutor 同规）。"""
    rounds = list(history or [])[-3:]
    if not rounds:
        return "（无）"
    lines: list[str] = []
    for r in rounds:
        lines.append(f"- 第 {r.get('round', '?')} 轮[{r.get('type', '?')}] "
                     f"问：{str(r.get('question') or '').strip()}")
        lines.append(f"  答：{str(r.get('user_answer') or '').strip()}")
        lines.append(f"  判分：{r.get('score', '?')}；差距：{str(r.get('gap') or '').strip()}")
    return "\n".join(lines)


def build_first_messages(rec: dict[str, Any], qtype: str, state: str,
                         stuck_rounds: int = 0) -> list[dict[str, str]]:
    """构造"第一问"的 LLM 请求（携带错题上下文锚定到错误岔路口）。"""
    system = _system(rec, qtype=qtype, state=state, task="first",
                     user_answer="", history=None, stuck_rounds=stuck_rounds)
    return [{"role": "system", "content": system}]


def build_score_messages(rec: dict[str, Any], qtype: str, state: str,
                         user_answer: str, history: Optional[list[dict[str, Any]]] = None,
                         stuck_rounds: int = 0) -> list[dict[str, str]]:
    """构造"判分 + 下一问"的 LLM 请求。"""
    system = _system(rec, qtype=qtype, state=state, task="score",
                     user_answer=user_answer, history=history, stuck_rounds=stuck_rounds)
    return [{"role": "system", "content": system}]


def _system(rec: dict[str, Any], qtype: str, state: str, task: str,
            user_answer: str, history: Optional[list[dict[str, Any]]],
            stuck_rounds: int) -> str:
    """渲染 socratic_review.md（占位符必须全部提供，否则 render_prompt 抛错）。"""
    opts = rec.get("options")
    if isinstance(opts, dict):
        opt_text = "\n".join(f"{k}. {v}" for k, v in opts.items())
    elif isinstance(opts, (list, tuple)):
        opt_text = "\n".join(str(x) for x in opts)
    else:
        opt_text = str(opts or "")
    return render_prompt(
        "socratic_review.md",
        stem=str(rec.get("question") or ""),
        options=opt_text or "（无选项，非选择题）",
        answer=str(rec.get("answer") or ""),
        my_reasoning=str(rec.get("my_reasoning") or ""),
        confidence=rec.get("confidence") if rec.get("confidence") is not None else "未填",
        error_tag=str(rec.get("error_tag") or "（未归因）"),
        fix=str(rec.get("fix") or "（无）"),
        task=task,
        qtype=qtype if qtype in _QTYPES else _QTYPES[0],
        state=state or "weak",
        stuck_rounds=int(stuck_rounds),
        user_answer=user_answer or "（本轮未作答）",
        history=_history_digest(history),
    )


def start_applying(client: Any, rec: dict[str, Any], qtype: str = "explain",
                   state: str = "weak", stuck_rounds: int = 0) -> str:
    """出第一问：返回问题正文（不判分）。"""
    msg = client.chat(build_first_messages(rec, qtype, state, stuck_rounds),
                      temperature=0.6)
    return (msg or "").strip()


def score_answer(client: Any, rec: dict[str, Any], user_answer: str,
                 qtype: str = "explain", state: str = "weak",
                 history: Optional[list[dict[str, Any]]] = None,
                 stuck_rounds: int = 0) -> dict[str, Any]:
    """判分本轮并出下一问。返回 {score, gap, next_question, hit_crossroad, where_uncertain}。

    ``score == -1`` 表示**无法判定**（模型返回不合法/异常）→ 调用方不计分、不记轮次，
    请学生围绕考点重答。绝不把解析失败伪装成"0 分"（那会冤枉学生、也会污染掌握度）。
    """
    try:
        raw = client.chat_json(build_score_messages(
            rec, qtype, state, user_answer, history, stuck_rounds), temperature=0.3)
    except LLMError as e:
        # 与 medtutor 同规：JSON 解析失败 → 兜底 -1（重答）；断网/Key/限流上抛（路由转 502）
        if "JSON" in str(e) or "解析" in str(e):
            return _unjudgeable("模型返回不是合法 JSON，请围绕考点再试一次。")
        raise
    except Exception as e:  # noqa: BLE001  其它异常 → 兜底但留痕
        errs.record("socratic.score", f"判分异常：{type(e).__name__}", exc=str(e)[:120])
        return _unjudgeable("判分异常，请围绕考点再试一次。")

    obj = validate_or_repair(raw, SocraticScore)
    if obj is None:
        return _unjudgeable("模型返回不符合判分契约，请围绕考点再试一次。")
    return {
        "score": obj.score,
        "gap": obj.gap,
        "next_question": obj.next_question,
        "hit_crossroad": obj.hit_crossroad,
        "where_uncertain": list(obj.where_uncertain),
    }


def _unjudgeable(msg: str) -> dict[str, Any]:
    """无法判定：score=-1，调用方据此走 retry（不计分、不记轮次）。"""
    return {"score": -1, "gap": msg, "next_question": "",
            "hit_crossroad": False, "where_uncertain": []}
