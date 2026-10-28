"""题目契约升级守卫（v0.22 阶段 4 / G4）——逐选项解析 + 原文引句 + 难度。

## 这一组在守什么

与 UWorld / AMBOSS 的差距不在「题」，在**解析的教育密度**：它们让每个错选项都教一次
（为什么错、人通常会怎么想错）。本阶段把这三件事写进契约与门禁：

1. `QuestionItem` 接受并归一四个新字段（键大写、难度 1..5、引句截断）；
2. **旧产物不炸**：没有这些字段的题照样过契约（空 dict 是合法值，不是错误）；
3. 门禁**批次级 opt-in**：只要本批有一题带逐选项理由，就要求每题每选项都有（缺 ⇒ fail）；
   整批都没有 ⇒ 不判（否则重跑旧项目会全红）；
4. 提示词与契约**同源**：`medgen.md` 必须写到这四个字段名，否则模型根本不知道要输出。

## 为什么「缺理由」是 fail 而不是 warn

理由缺失不是「质量瑕疵」，是**这道题没有解析**——它退化成「对答案」。而 `warn` 只进人工复核，
用户看到的仍是「只有一行解析」的题。这是本阶段唯一一处硬性要求。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from medkit.core.schema import QuestionItem
from medkit.gates import options_check

CASE_DIR = Path(__file__).resolve().parent / "fixtures" / "llm_cases"
PROMPT = Path(__file__).resolve().parent.parent / "medkit" / "prompts" / "medgen.md"

BASE = {
    "question": "下列关于川崎病急性期治疗，首选药物是？",
    "type": "A1",
    "options": ["阿司匹林", "青霉素", "糖皮质激素", "丙种球蛋白", "抗凝药"],
    "answer": "A",
    "analysis": "川崎病急性期首选阿司匹林。【源:切片S001】",
}


def _all_rationales(letters: str = "ABCDE", text: str = "理由") -> dict[str, str]:
    return {L: f"{text}{L}" for L in letters}


# ---------------------------------------------------------------- 契约层
def test_new_fields_are_accepted_and_normalized():
    """四个新字段能过契约，且做形状归一（不是原样收下）。"""
    m = QuestionItem.model_validate({
        **BASE,
        "option_rationales": {"a": "为什么 A 对", "选项B": "为什么 B 错", "C": ""},
        "misconceptions": {"B": "把联合用药记成单用"},
        "source_quote": "  川崎病急性期首选阿司匹林。  ",
        "difficulty": "4",
    })
    assert m.option_rationales == {"A": "为什么 A 对", "B": "为什么 B 错"}
    assert m.misconceptions == {"B": "把联合用药记成单用"}
    assert m.source_quote == "川崎病急性期首选阿司匹林。"
    assert m.difficulty == 4


@pytest.mark.parametrize("bad,expect", [(0, 0), (9, 0), ("abc", 0), (None, 0), ("5", 5), (3.7, 3)])
def test_difficulty_is_clamped_not_guessed(bad, expect):
    """难度只接受 1..5；**越界一律 0（未标注）**，不猜、不截断到边界。

    为什么不像别的字段那样 clamp 到 1/5：难度是自评，把 9 截成 5 等于替模型编了个「最难」。
    宁可标 0（未知），也不要一个假的确定值。
    """
    m = QuestionItem.model_validate({**BASE, "difficulty": bad})
    assert m.difficulty == expect


def test_legacy_question_without_new_fields_still_validates():
    """**旧产物必须照样通过**：空 dict / 空串 / 0 都是合法值。

    这条防的是「升级把历史项目判死」——契约层加字段不能变成破坏性变更。
    """
    m = QuestionItem.model_validate(dict(BASE))
    assert m.option_rationales == {} and m.misconceptions == {}
    assert m.source_quote == "" and m.difficulty == 0


def test_extra_forbid_still_rejects_unknown_keys():
    """`extra="forbid"` 不能被新字段顺手放宽——未知键仍然报错（防止拼错的字段被静默吞掉）。"""
    with pytest.raises(ValidationError):
        QuestionItem.model_validate({**BASE, "option_rationale": {}})   # 少一个 s


def test_fixtures_carry_new_fields():
    """夹具必须带新字段——否则「模型能输出新字段」这件事没有任何样本证据。"""
    for name in ("medgen_a1.json", "medgen_x.json", "medgen_case.json", "medgen_image.json"):
        data = json.loads((CASE_DIR / name).read_text(encoding="utf-8"))
        for q in data["questions"]:
            assert q.get("option_rationales"), f"{name} 缺 option_rationales"
            assert q.get("source_quote"), f"{name} 缺 source_quote"
            assert 1 <= int(q.get("difficulty") or 0) <= 5, f"{name} 难度越界"


# ---------------------------------------------------------------- 提示词同源
def test_prompt_declares_all_new_fields():
    """**提示词是契约**：`medgen.md` 必须写到四个字段名。

    这是「schema 加了字段、提示词没要求」这类**静默失效**的唯一机器判据——
    那种情况下契约永远通过（字段都是可选的），而模型永远不会输出它们。
    """
    text = PROMPT.read_text(encoding="utf-8")
    for field in ("option_rationales", "misconceptions", "source_quote", "difficulty"):
        assert field in text, f"medgen.md 未要求输出 {field}"


# ---------------------------------------------------------------- 门禁：逐选项理由
def test_rationale_check_skips_legacy_batches():
    """整批都没有逐选项理由 ⇒ **不判**（旧产物不当成缺陷）。"""
    qs = [QuestionItem.model_validate(dict(BASE)).model_dump()]
    assert options_check.check_rationale_coverage(qs) == []


def test_rationale_check_requires_every_option():
    """批次里有一题带了理由 ⇒ 每题每选项都要有；缺一个即 **fail**。"""
    qs = [{"id": "q1", **BASE, "option_rationales": _all_rationales("ABCD")},   # 缺 E
          {"id": "q2", **BASE, "option_rationales": _all_rationales("ABCDE")}]
    issues = options_check.check_rationale_coverage(qs)
    assert issues, "缺 E 必须被抓住"
    assert [i["q_id"] for i in issues] == ["q1"], issues
    assert issues[0]["severity"] == "fail"
    assert "E" in issues[0]["reason"]


def test_rationale_check_flags_extra_keys_as_info():
    """理由里出现超出本题选项的键 ⇒ `info`（不阻断，只提示错位）。"""
    q = {"id": "q1", **BASE, "options": ["甲", "乙", "丙"], "answer": "A",
         "option_rationales": _all_rationales("ABCDE")}
    issues = options_check.check_rationale_coverage([q])
    assert any(i["code"] == "R20" and i["severity"] == "info" for i in issues), issues


def test_check_all_includes_batch_quality_checks():
    """`check_all` 必须把批次级质量项并进同一份 issues（否则编排层看不到）。"""
    qs = [{"id": f"q{i}", **BASE, "answer": "A",
           "option_rationales": _all_rationales("ABCD")} for i in range(12)]
    issues = options_check.check_all(qs)["issues"]
    codes = {i["code"] for i in issues}
    assert "R20" in codes, "缺 E 的理由没被抓到"
    assert "R22" in codes, "12 题全押 A，答案分布告警没触发"


def test_answer_distribution_ignores_small_batches():
    """样本 < 10 题不判分布——小样本必然偏，卡它只会制造噪声。"""
    qs = [{"id": f"q{i}", **BASE, "answer": "A"} for i in range(9)]
    assert options_check.check_answer_distribution(qs) == []


def test_option_length_clue_flags_long_correct_answer():
    """正确选项明显最长 ⇒ `warn`（长度泄露答案）。短选项不卡。"""
    long_q = {"id": "q1", "question": "?", "type": "A1", "answer": "A",
              "options": ["急性前壁心肌梗死伴心源性休克需要立即再灌注治疗",
                          "胃炎", "胆囊炎", "胰腺炎", "肠梗阻"]}
    issues = options_check.check_option_length_clue([long_q])
    assert issues and issues[0]["code"] == "R21"

    short_q = {"id": "q2", "question": "?", "type": "A1", "answer": "A",
               "options": ["肺炎", "胃炎", "胆囊炎", "胰腺炎", "肠梗阻"]}
    assert options_check.check_option_length_clue([short_q]) == []


# ---------------------------------------------------------------- 渲染与读取
def test_html_rationales_is_empty_for_legacy_question():
    """旧题（无逐选项解析）渲染必须是**空串**——重新导出老题库不能变版式。"""
    from medkit.render import qbank_html

    assert qbank_html._html_rationales({"question": "q", "analysis": "a"}) == ""


def test_export_html_renders_rationales_only_when_present():
    """有字段才渲染，且内容被转义（不许把模型输出当 HTML 插进产物）。"""
    from medkit.render import qbank_html

    q_new = {"id": "q1", **BASE,
             "option_rationales": {"A": "为什么对 <script>", "B": "为什么错"},
             "misconceptions": {"B": "常见误解"},
             "source_quote": "原文一句"}
    html = qbank_html.export_html([q_new])
    assert 'class="rat"' in html
    assert "为什么对 &lt;script&gt;" in html, "选项理由未转义"
    assert "原文一句" in html

    html_old = qbank_html.export_html([{"id": "q2", **BASE}])
    assert 'class="rat"' not in html_old


def test_reveal_endpoint_exposes_rationales():
    """背题模式（`/reveal`）要能看到逐选项理由——背题正是最需要它的场景。"""
    from fastapi.testclient import TestClient

    from medkit import main as m
    from medkit.core import bank as banklib

    banklib.create_bank("背题测试", "内科学")
    bid = str(banklib.list_banks()[0]["id"])
    banklib.add_items(bid, [{
        "question": "背题模式的题", "subtopic": "测试", "options": ["甲", "乙", "丙", "丁", "戊"],
        "answer": "A", "analysis": "解析。【源:切片S001】",
        "option_rationales": {"A": "对的理由", "B": "错的理由"},
        "misconceptions": {"B": "常见误解"},
        "source_quote": "原文依据", "difficulty": 4,
    }])
    iid = str(banklib.list_items(bid)[0]["id"])
    body = TestClient(m.app, base_url="http://127.0.0.1").get(
        f"/api/bank/items/{iid}/reveal").json()
    assert body["option_rationales"]["A"] == "对的理由"
    assert body["misconceptions"]["B"] == "常见误解"
    assert body["source_quote"] == "原文依据"
    assert body["difficulty"] == 4
