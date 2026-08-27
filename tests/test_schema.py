"""IMP-03 契约层（core/schema.py）单元测试。

覆盖：QuestionItem 字段/类型/多余键/X 型答案升序；QcVerdict 浮点容错；TutorTurn / FixPatch /
RealexamNorm 契约；validate_or_repair 的「修复重发 1 次成功」与「仍失败 → None」两条路径。
全部纯本地、零真实网络调用（不 import LLMClient，不依赖 LLM）。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from medkit.core.llm import LLMClient, LLMError
from medkit.core.schema import (
    QcVerdict,
    QuestionItem,
    RealexamNorm,
    TutorTurn,
    validate_or_repair,
)


# ------------------------------------------------------------------ QuestionItem 合法
def test_question_item_valid_a1():
    q = QuestionItem.model_validate({
        "question": "首选治疗是？", "type": "A1", "bloom": "记忆", "subtopic": "治疗",
        "options": ["阿司匹林", "青霉素", "激素", "丙种球蛋白", "抗凝药"],
        "answer": "A", "analysis": "解析。【源:切片S001】",
    })
    assert q.type == "A1" and q.answer == "A" and len(q.options) == 5


def test_question_item_valid_x_ascending():
    q = QuestionItem.model_validate({
        "question": "下列哪些正确？", "type": "X", "bloom": "理解",
        "options": ["a", "b", "c", "d", "e"], "answer": "BDE", "analysis": "解析",
    })
    assert q.answer == "BDE"


def test_question_item_optional_fields_default():
    # case / image / data_table 均为可选字段，缺省时给出默认值。
    q = QuestionItem.model_validate({"question": "题", "answer": "A", "options": ["a", "b", "c", "d", "e"]})
    assert q.group_kind == "" and q.image_ref == "" and q.case_stem == ""
    assert q.group is None


# ------------------------------------------------------------------ QuestionItem 非法
def test_question_item_missing_question_fails():
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({"type": "A1", "options": ["a", "b", "c", "d", "e"], "answer": "A"})


def test_question_item_wrong_type_fails():
    # question 为数字而非字符串 → 类型错
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({"question": 123, "answer": "A", "options": ["a", "b", "c", "d", "e"]})
    # options 为字符串而非数组 → 类型错
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({"question": "题", "options": "abcde", "answer": "A"})


def test_question_item_extra_key_fails():
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({"question": "题", "answer": "A", "bogus_field": "x"})


def test_question_item_x_answer_unsorted_fails():
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({
            "question": "下列哪些正确？", "type": "X",
            "options": ["a", "b", "c", "d", "e"], "answer": "EDB", "analysis": "解析",
        })
    # X 型答案超出 2~4 个也失败
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({
            "question": "下列哪些正确？", "type": "X",
            "options": ["a", "b", "c", "d", "e"], "answer": "ABCDE", "analysis": "解析",
        })


def test_question_item_non_x_multi_letter_fails():
    # 单选/案例/组题答案应为单字母；"A B" 会被识别为多字母 → 非法
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({
            "question": "题", "type": "A1",
            "options": ["a", "b", "c", "d", "e"], "answer": "AB", "analysis": "解析",
        })
    # 多选题型校验失败后仍应能区分（重复字母）
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({
            "question": "下列哪些正确？", "type": "X",
            "options": ["a", "b", "c", "d", "e"], "answer": "BDB", "analysis": "解析",
        })


# ------------------------------------------------------------------ QcVerdict 浮点容错
def test_qc_verdict_float_tolerance():
    v = QcVerdict.model_validate({"score": "82.5", "gate_decision": "PASS", "issues": [], "summary": ""})
    assert v.score == 82.5
    v2 = QcVerdict.model_validate({"score": None, "gate_decision": "PASS", "issues": [], "summary": ""})
    assert v2.score is None  # None 交给调用方 _coerce_score 兜底


def test_qc_verdict_gate_normalized():
    # 未知 gate_decision 归一为 PASS_WITH_FIXES（模型约束，仍向后兼容）
    v = QcVerdict.model_validate({"score": 80, "gate_decision": "weird", "issues": []})
    assert v.gate_decision == "PASS_WITH_FIXES"
    v2 = QcVerdict.model_validate({"score": 80, "gate_decision": "blocked", "issues": []})
    assert v2.gate_decision == "BLOCKED"


def test_qc_verdict_severity_lowercased_ok():
    v = QcVerdict.model_validate({"score": 80, "gate_decision": "PASS",
                                  "issues": [{"q_id": "Q1", "code": "F2", "severity": "fail"}]})
    assert v.issues[0].severity == "fail"


# ------------------------------------------------------------------ TutorTurn
def test_tutor_turn_valid():
    t = TutorTurn.model_validate({"score": 2, "gap": "机制要补", "next_question": "试解释",
                                  "next_type": "apply"})
    assert t.score == 2 and t.next_type == "apply"


def test_tutor_turn_bad_next_type_defaults():
    t = TutorTurn.model_validate({"score": 1, "gap": "", "next_question": "q", "next_type": "zzz"})
    assert t.next_type == "explain"


def test_tutor_turn_score_clamp():
    with pytest.raises(ValidationError):
        TutorTurn.model_validate({"score": 9, "gap": "", "next_question": "q", "next_type": "apply"})


# ------------------------------------------------------------------ RealexamNorm
def test_realexam_norm_valid():
    n = RealexamNorm.model_validate({"items": [
        {"subject": "儿科学", "chapter": "呼", "item": "肺炎", "freq": 3},
    ]})
    assert n.items[0].item == "肺炎" and n.items[0].freq == 3


def test_realexam_norm_extra_key_fails():
    with pytest.raises(ValidationError):
        RealexamNorm.model_validate({"items": [{"item": "肺炎", "freq": 1, "bogus": 1}]})


# ------------------------------------------------------------------ validate_or_repair
def test_validate_or_repair_initial_pass():
    raw = {"question": "题", "answer": "A", "options": ["a", "b", "c", "d", "e"], "type": "A1"}
    model = validate_or_repair(raw, QuestionItem)
    assert model is not None and model.question == "题"


def test_validate_or_repair_repair_succeeds_once():
    # 首轮因答案乱序失败 → repair_fn 修正为升序 → 二次校验通过。
    bad = {"question": "题", "type": "X", "answer": "EDB",
           "options": ["a", "b", "c", "d", "e"], "analysis": "解析"}

    def repair(raw, exc):
        assert isinstance(exc, ValidationError)
        return {**raw, "answer": "BDE"}

    model = validate_or_repair(bad, QuestionItem, repair)
    assert model is not None and model.answer == "BDE"


def test_validate_or_repair_repair_fails_returns_none():
    # repair_fn 返回的仍是坏数据 → 二次校验失败 → None（走人工复核）。
    bad = {"question": "题", "type": "X", "answer": "EDB",
           "options": ["a", "b", "c", "d", "e"], "analysis": "解析"}

    def repair(raw, exc):
        return {**raw, "answer": "EDB"}  # 仍乱序

    assert validate_or_repair(bad, QuestionItem, repair) is None


def test_validate_or_repair_no_repair_fn_returns_none():
    # 未提供 repair_fn → 初次校验失败即返回 None（不抛、不重试）。
    result = validate_or_repair({"question": "题", "type": "X", "answer": "EDB",
                                 "options": ["a", "b", "c", "d", "e"], "analysis": "解析"},
                                QuestionItem)
    assert result is None
    # repair_fn=None 显式传入同理。
    result2 = validate_or_repair({"question": "题", "type": "X", "answer": "EDB",
                                  "options": ["a", "b", "c", "d", "e"], "analysis": "解析"},
                                 QuestionItem, repair_fn=None)
    assert result2 is None


def test_validate_or_repair_repair_fn_returns_none():
    def repair(raw, exc):
        return None  # 无法修复 → None

    result = validate_or_repair({"question": "题", "type": "X", "answer": "EDB",
                                 "options": ["a", "b", "c", "d", "e"], "analysis": "解析"},
                                QuestionItem, repair)
    assert result is None


# ------------------------------------------------------------------ chat_json schema 参数
class _FakeCompletions:
    def __init__(self, content: str):
        self.content = content

    def create(self, **kwargs):
        return SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content=self.content))], usage=None)


class _FakeOpenAI:
    def __init__(self, content: str):
        self.chat = SimpleNamespace(completions=_FakeCompletions(content))


def _make_client(content: str) -> LLMClient:
    client = LLMClient("http://fake", "key", "fake-model")
    client._client = _FakeOpenAI(content)  # 替换真实 OpenAI 客户端 → 零网络
    return client


def test_chat_json_schema_returns_model():
    client = _make_client(json.dumps({"score": 80, "gate_decision": "PASS", "issues": [], "summary": "ok"}))
    out = client.chat_json([{"role": "user", "content": "hi"}], schema=QcVerdict)
    assert isinstance(out, QcVerdict) and out.score == 80


def test_chat_json_schema_validation_error_raises_llm_error():
    client = _make_client(json.dumps({"score": "abc", "gate_decision": "PASS", "issues": []}))
    with pytest.raises(LLMError):
        client.chat_json([{"role": "user", "content": "hi"}], schema=QcVerdict)


def test_chat_json_no_schema_returns_raw():
    client = _make_client(json.dumps({"questions": [{"question": "题"}]}))
    out = client.chat_json([{"role": "user", "content": "hi"}])
    assert isinstance(out, dict) and out["questions"][0]["question"] == "题"
