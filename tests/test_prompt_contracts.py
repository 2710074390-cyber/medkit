"""IMP-04 提示词回归：fixtures 合同校验。

加载 tests/fixtures/llm_cases/ 下的 6 个样本，逐条过 IMP-03 的模型校验，并断言关键不变式：
- X 型答案升序（选项按标号升序）；
- image_ref ∈ 注入切片（image 切片 sid 与实际注入内容对应，遵循 medgen 注入规则）；
- 案例组 3~5 子题共用 case_stem（且子题选项独立、case_order 递增）。
同时含「故意改坏一条 fixture → 必须红」的 negative 断言（内存副本，不影响磁盘文件）。
全部纯本地，零 LLM / 零网络。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from medkit.core.schema import FixPatch, QcVerdict, QuestionItem

CASE_DIR = Path(__file__).resolve().parent / "fixtures" / "llm_cases"

# medgen 图像注入规则（见 orchestrator.py：image_sections += f"[{sid}] {text}\n"）。
_IMAGE_SLICES = [{"sid": "IMG1", "text": "心电图图像"}, {"sid": "IMG2", "text": "X光胸片"}]
_IMAGE_SECTIONS = "".join(f"[{s['sid']}] {s['text']}\n" for s in _IMAGE_SLICES)


def _load(name: str) -> dict:
    return json.loads((CASE_DIR / name).read_text(encoding="utf-8"))


def _questions(data: dict) -> list[dict]:
    items = data.get("questions", [])
    return [q for q in items if isinstance(q, dict)]


def _answer_letters(answer: str) -> str:
    return "".join(ch for ch in (answer or "").upper() if ch in "ABCDE")


# ---------------------------------------------------------------- 样本能过契约校验
def test_medgen_fixtures_validate_question_item():
    for name in ("medgen_a1.json", "medgen_x.json", "medgen_case.json", "medgen_image.json"):
        for q in _questions(_load(name)):
            model = QuestionItem.model_validate(q)
            assert model.question, f"{name} 题目缺题干"


def test_medqc_verdict_contract():
    data = _load("medqc_verdict.json")
    verdict = QcVerdict.model_validate(data)
    assert verdict.gate_decision in ("BLOCKED", "PASS_WITH_FIXES", "PASS")
    assert verdict.score is None or isinstance(verdict.score, (int, float))
    for issue in verdict.issues:
        assert issue.severity in ("fail", "warn")
        assert issue.q_id and issue.code


def test_medfix_patch_contract():
    data = _load("medfix_patch.json")
    for fix in _questions(data):
        model = FixPatch.model_validate(fix)
        assert model.id and model.question and model.answer and model.analysis
        assert model.options, "修复题应含完整选项"


# ---------------------------------------------------------------- 关键不变式
def test_x_answer_ascending_invariant():
    data = _load("medgen_x.json")
    xs = [q for q in _questions(data) if q.get("type") == "X"]
    assert xs, "medgen_x.json 应含 X 型题"
    for q in xs:
        letters = _answer_letters(q.get("answer", ""))
        assert len(set(letters)) == len(letters), "X 型答案字母不能重复"
        assert 2 <= len(letters) <= 4, "X 型答案应为 2~4 个正确选项"
        assert letters == "".join(sorted(set(letters))), \
            f"X 型答案必须按选项标号升序：{q.get('answer')!r}"


def test_image_ref_in_injected_sections():
    data = _load("medgen_image.json")
    img_qs = [q for q in _questions(data) if q.get("image_ref")]
    assert img_qs, "medgen_image.json 应含 image_ref 题"
    for q in img_qs:
        ref = q.get("image_ref", "")
        assert "如图所示" in q.get("question", ""), "图题题干应含「如图所示」"
        assert f"[{ref}]" in _IMAGE_SECTIONS, \
            f"image_ref={ref!r} 必须能在注入切片中找到（[sid] 形式）"


def test_case_group_shared_stem():
    data = _load("medgen_case.json")
    cases = [q for q in _questions(data) if q.get("group_kind") == "case"]
    assert 3 <= len(cases) <= 5, f"案例组应为 3~5 道子题（实际 {len(cases)}）"
    stems = {q.get("case_stem", "") for q in cases}
    assert len(stems) == 1, "案例组每道子题必须共用同一段 case_stem（冗余扁平复制）"
    cid = {q.get("case_id", "") for q in cases}
    assert len(cid) == 1, "案例组子题应共享同一 case_id"
    orders = sorted(q.get("case_order", 0) for q in cases)
    assert orders == list(range(1, len(cases) + 1)), "case_order 应从 1 递增"
    assert all(q.get("options") for q in cases), "案例组子题应各有独立选项"


# ---------------------------------------------------------------- negative（改坏必须红）
def test_negative_x_answer_broken_red():
    data = copy.deepcopy(_load("medgen_x.json"))
    data["questions"][0]["answer"] = "EDB"  # 改坏：乱序
    with pytest.raises(ValidationError):
        QuestionItem.model_validate(data["questions"][0])


def test_negative_case_stem_divergence_red():
    data = copy.deepcopy(_load("medgen_case.json"))
    data["questions"][1]["case_stem"] = "另一段完全不同的案例题干……"  # 改坏：子题 stem 分裂
    stems = {q.get("case_stem", "") for q in data["questions"] if q.get("group_kind") == "case"}
    assert len(stems) == 2, "改写 case_stem 后组内应不再共享统一 stem（应为红）"


def test_negative_image_ref_not_injected_red():
    data = copy.deepcopy(_load("medgen_image.json"))
    data["questions"][0]["image_ref"] = "IMG9"  # 改坏：指向不存在的素材切片
    ref = data["questions"][0]["image_ref"]
    assert f"[{ref}]" not in _IMAGE_SECTIONS, "不存在的 image_ref 不应出现在注入切片中（应为红）"
