"""MedTutor：苏格拉底式提问学习（M4，借鉴 Cogniloop 状态机 + 五类提问）。

出问与判分都属于生成长文本 → 交给 LLM（每轮一次调用，独立小账）；
「是否连续答对 / 概念状态是否晋升 / 下一问题型」等布尔判定是纯本地，
由 core/tutor.apply_score / next_question_type 完成，不额外调模型。

判分依赖 LLM 返回的 JSON（chat_json 稳健解析：剥围栏、截取首个 JSON）。
"""

from typing import Any, Optional

from . import render_prompt


def needs_client_and_price(subject: str, kp_name: str) -> int:
    """粗估输入 token（供 cost toast，不含输出；历史约 3 轮）。"""
    return 500 + len(subject) * 2 + len(kp_name) * 2 + 700


def start_applying(client: Any, subject: str, kp_name: str, state: str,
                   qtype: str, slices_text: str = "") -> str:
    """出第一问：返回问题正文（不判分）。"""
    system = _system(subject, kp_name, state, qtype, task="first")
    body = _materials_body(slices_text, history=None, user_answer="")
    user = "\n\n".join(["## 素材", body]) if body else "（当前无教材切片，请用通用医学常识引导）"
    msg = client.chat([{"role": "system", "content": system},
                       {"role": "user", "content": user}], temperature=0.6)
    return (msg or "").strip()


def score_answer(client: Any, subject: str, kp_name: str, state: str,
                 qtype: str, question: str, user_answer: str,
                 slices_text: str = "", history: Optional[list[dict[str, Any]]] = None
                 ) -> dict[str, Any]:
    """判分本回答并出下一问。返回 {score, gap, next_question, next_type}。

    score 用本地规则兜底：模型未返回合法分数时返回 -1（无法判定 → 不计分重答），
    避免启发式被套话刷分污染掌握度。
    """
    system = _system(subject, kp_name, state, qtype, task="score")
    body = _materials_body(slices_text, history, user_answer)
    user = "\n\n".join(["## 素材", body, f"## 本轮问题（{qtype}）\n{question}",
                        f"## 学生作答\n{user_answer or '（学生未作答）'}"])
    try:
        raw = client.chat_json([{"role": "system", "content": system},
                                {"role": "user", "content": user}], temperature=0.3)
        obj = raw if isinstance(raw, dict) else {}
        score = _num(obj.get("score", -1), default=-1)
        gap = str(obj.get("gap") or "").strip()
        nq = str(obj.get("next_question") or "").strip()
        nt = str(obj.get("next_type") or "").strip()
    except Exception:  # noqa: BLE001  解析失败 → 启发式兜底
        score, gap, nq, nt = -1, "", "", ""
    if score < 0 or score > 3:
        score = _heuristic_score(user_answer, question)
        gap = gap or ("方向有偏，试着重讲机制。")
    next_type = nt if nt in ("explain", "apply", "contrast", "predict", "trace") else qtype
    if not nq:
        nq = f"再想深一层：围绕 {kp_name}，结合你刚才的作答，重新组织一次更完整的回答。"
    return {"score": score, "gap": gap, "next_question": nq, "next_type": next_type}


def _num(v: Any, default: int = -1) -> int:
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _heuristic_score(answer: str, question: str) -> int:
    """零模型兜底：无法定量判定 → 一律返回 -1（由路由层处理为不计分重答）。

    兜底启发式容易被几个机制词 + 一段长话「刷穿」而虚增掌握分。宁可不计分，
    也不把套话当成真答对；真正判分交给 LLM（medtutor.py 的 judge）做。
    """
    a = (answer or "").strip()
    if not a or len(a) < 8:
        return -1  # 空答 / 过短 → 无法判定
    return -1  # 有内容但仍无法可靠判定 → 等 LLM judge 再定


def _system(subject: str, kp_name: str, state: str, qtype: str, task: str) -> str:
    return render_prompt("medtutor.md", subject=subject, kp_name=kp_name,
                         state=state, qtype=qtype, task=task)


def _materials_body(slices_text: str,
                    history: Optional[list[dict[str, Any]]],
                    user_answer: str) -> str:
    out: list[str] = []
    if slices_text.strip():
        out.append(f"## 教材切片（供引用的事实来源）\n{slices_text.strip()}")
    if history:
        recent = history[-3:]
        lines = [f"- R{h.get('round')} [{h.get('type','')}] Q: {(h.get('question') or '')[:80]} "
                 f"→ A: {(h.get('user_answer') or '')[:50]} → 判分 {h.get('score')}"
                 for h in recent]
        out.append("## 历史问答（最近几轮，供参考状态）\n" + "\n".join(lines))
    return "\n\n".join(out)
