"""MedReview：生成复习手册 MD（教材浓缩型分层）。"""

from typing import Any

from . import load_prompt


def _compact_questions(questions: list[dict[str, Any]],
                       limit: int = 60) -> str:
    """轻量化题库：仅知识点+答案键+解析前 100 字（控制 token）。"""
    lines = []
    for q in questions[:limit]:
        lines.append(
            f"- [{q.get('type','')} | {q.get('bloom','')}] {q.get('subtopic','')}: "
            f"{q.get('question','')[:80]}… 答案 {q.get('answer','')} "
            f"解析：{(q.get('analysis','') or '')[:100]}")
    if len(questions) > limit:
        lines.append(f"…（共 {len(questions)} 题，已抽样展示 {limit} 题）")
    return "\n".join(lines)


def generate_review(client: Any, subject: str, exam: str,
                    questions: list[dict[str, Any]],
                    teacher_text: str,
                    slice_texts: str, limit: int = 60) -> str:
    system = load_prompt("medreview.md")
    return client.chat([
        {"role": "system", "content": system},
        {"role": "user", "content": (
            f"科目：{subject}\n适用考试：{exam}\n\n"
            f"## 修复后题库（知识盘点用）\n{_compact_questions(questions, limit)}\n\n"
            f"## 教师重点\n{teacher_text[:3000]}\n\n"
            f"## 教材章节切片（关键段落）\n{slice_texts[:6000]}\n\n"
            f"请按提示词结构输出完整复习手册 Markdown。")},
    ], temperature=0.4)


def make_client() -> Any:
    from . import get_client
    return get_client("gen")
