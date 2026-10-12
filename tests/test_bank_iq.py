# -*- coding: utf-8 -*-
"""题库导入解析（`core/bank_iq`）测试：往返一致性 / 列表符 / 题型推断 / 去重键。

## 为什么「往返一致性」是本文件的核心判据

`export_md()` → `parse_bank_md()` → 逐字段相等 —— 这是**零成本**拿到的正确性判据：
**自己导出的格式必须能原样读回来**。

此前读不回来（实测，见 `docs/题库刷题功能_调研与实现方案_2026-10-02.md` §5.7）：
① 切块器只认 `\\d+` 题号，MedKit 导出的 `### q1 · …` 里的 `q1` 不匹配 ⇒ 整篇变成一道题；
② `_OPT_RE` 要求行首即字母，`- A. 吸烟` 不命中 ⇒ 选项全丢、被当成题干、**不报错**。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import bank_iq  # noqa: E402
from medkit.render.qbank_html import export_md  # noqa: E402

# 覆盖单题 / X 型 / 案例组 / B1 选项组 / 图题（image_ref 在 md 里只留占位）
_CASES = [
    [{"id": "q1", "type": "A1", "bloom": "理解", "subtopic": "慢阻肺的诊断",
      "question": "慢阻肺最主要的病因是", "options": ["吸烟", "感染"],
      "answer": "A", "analysis": "吸烟是 COPD 最重要的环境致病因素"}],
    [{"id": "q1", "type": "A1", "bloom": "记忆", "subtopic": "A",
      "question": "题干一", "options": ["甲", "乙"], "answer": "A", "analysis": "解析一"},
     {"id": "q2", "type": "X", "bloom": "应用", "subtopic": "B",
      "question": "题干二", "options": ["丙", "丁", "戊"], "answer": "AC", "analysis": "解析二"}],
]


@pytest.mark.parametrize("questions", _CASES)
def test_export_md_roundtrip_fields_equal(questions):
    """**往返一致性**：`export_md()` → `parse_bank_md()` → 题干/选项/答案/解析/subtopic/题型全等。

    证伪：把 `library._LIST_MARK_RE` 那一行删掉 → 选项变空 → 本用例必红。
    """
    rows = bank_iq.parse_bank_md(export_md(questions, "题库"))
    assert len(rows) == len(questions), f"块数不符：{len(rows)} != {len(questions)}"
    for want, got in zip(questions, rows, strict=True):
        assert got["question"] == want["question"], f"题干不符：{got['question']!r}"
        assert [_strip(o) for o in got["options"]] == want["options"], "选项不符"
        assert got["answer"] == want["answer"], "答案不符"
        assert got["analysis"] == want["analysis"], "解析不符"
        assert got["subtopic"] == want["subtopic"], "subtopic 不符"
        assert got["type"] == want["type"], "题型不符"


def _strip(o: str) -> str:
    """导出时选项被渲染成 `- A. {文本}`，解析回来仍带字母前缀（归一在 normalize_item 里做）。"""
    import re
    return re.sub(r"^[A-Ha-h][)）、.．\s]+", "", str(o)).strip()


@pytest.mark.parametrize("marker", ["", "- ", "* ", "+ "])
def test_list_marker_forms_are_equivalent(marker):
    """列表符三形态（无 / `- ` / `* ` / `+ `）解析出的 options 必须相同。

    证伪：只支持一种形态 → 另外三种必红。
    """
    text = f"慢阻肺最主要的病因是\n{marker}A. 吸烟\n{marker}B. 感染\n答案：A"
    rows = bank_iq.parse_bank_md(text)
    assert len(rows) == 1
    assert rows[0]["options"] == ["A. 吸烟", "B. 感染"], rows[0]["options"]
    assert rows[0]["question"] == "慢阻肺最主要的病因是"


@pytest.mark.parametrize("head", ["1. ", "1、", "1．", "1)"])
def test_numbered_question_line_is_not_swallowed(head):
    """序号开头的**题干行**不得被剥列表符后误判为选项（误伤面回归）。"""
    rows = bank_iq.parse_bank_md(f"{head}患者男 3 岁发热咳嗽\nA. 甲\nB. 乙\n答案：B")
    assert len(rows) == 1
    assert rows[0]["question"] == "患者男 3 岁发热咳嗽", rows[0]["question"]
    assert rows[0]["options"] == ["A. 甲", "B. 乙"]


def test_h1_document_title_is_not_a_question():
    """`# 题库`（一级标题）是文档标题，不得变成一道「题干=文档标题、无答案」的题。

    实测踩过：`export_md()` 的首行就是 `# {title}`。
    """
    rows = bank_iq.parse_bank_md(export_md(_CASES[0], "呼吸系统题库"))
    assert len(rows) == 1
    assert rows[0]["question"] == "慢阻肺最主要的病因是"
    assert "呼吸系统题库" not in rows[0]["question"]


def test_case_group_header_is_not_a_question():
    """案例组头 `## 📋 案例 c1` 自身不是题目；子题各自成题。"""
    qs = [{"id": "q1", "type": "A3", "bloom": "应用", "subtopic": "案例",
           "question": "子题一", "options": ["甲", "乙"], "answer": "A", "analysis": "解一",
           "case_id": "c1", "case_order": 1, "case_stem": "案例题干"},
          {"id": "q2", "type": "A3", "bloom": "应用", "subtopic": "案例",
           "question": "子题二", "options": ["丙", "丁"], "answer": "B", "analysis": "解二",
           "case_id": "c1", "case_order": 2, "case_stem": "案例题干"}]
    rows = bank_iq.parse_bank_md(export_md(qs, "题库"))
    assert [r["question"] for r in rows] == ["子题一", "子题二"]


def test_option_group_shared_options_are_carried_to_subitems():
    """B1 选项组的**共享选项**必须顺延给后续子题（否则子题没有选项）。"""
    qs = [{"id": "q1", "type": "B1", "bloom": "记忆", "subtopic": "共享",
           "question": "共用选项题一", "options": [], "answer": "A", "analysis": "解一",
           "group_kind": "option_group", "group": {"options": ["甲", "乙", "丙"]}},
          {"id": "q2", "type": "B1", "bloom": "记忆", "subtopic": "共享",
           "question": "共用选项题二", "options": [], "answer": "B", "analysis": "解二",
           "group_kind": "option_group", "group": {"options": ["甲", "乙", "丙"]}}]
    rows = bank_iq.parse_bank_md(export_md(qs, "题库"))
    assert len(rows) == 2
    for r in rows:
        assert r["options"] == ["A. 甲", "B. 乙", "C. 丙"], r["options"]

@pytest.mark.parametrize("answer,want", [("A", "A1"), ("BD", "X"), ("ac", "X"), ("", "A1")])
def test_infer_type(answer, want):
    assert bank_iq.infer_type(answer) == want


def test_strict_and_loose_keys_differ_by_question_length():
    """严格键（题干全文）与宽松键（前 80 字）在长题干上必须不同，短题干上相同。"""
    a = {"question": "甲" * 100}
    b = {"question": "甲" * 80 + "乙" * 20}
    assert bank_iq.strict_key(a) != bank_iq.strict_key(b)
    assert bank_iq.loose_key(a) == bank_iq.loose_key(b)


def test_contains_hit_detects_prefix_variant():
    """包含检测：题干被加前缀（或截断）时命中已有题（只提示，不自动处理）。"""
    existing = [{"id": "x1", "question": "慢阻肺最主要的病因是吸烟"}]
    assert bank_iq.contains_hit("慢阻肺最主要的病因是", existing) == "x1"
    assert bank_iq.contains_hit("心力衰竭治疗要点", existing) is None
    # 太短的题干不参与（避免误报）
    assert bank_iq.contains_hit("甲", existing) is None


def test_normalize_strips_letter_prefix_and_sets_origin():
    rec = bank_iq.normalize_item(
        {"question": "题", "options": ["A. 甲", "B. 乙"], "answer": "b", "type": "A1"},
        bid="bk1", origin="md_import", now="2026-10-10T00:00:00")
    assert rec is not None
    assert rec["options"] == ["甲", "乙"], "选项字母前缀必须剥掉（否则渲染层再补一次 = A. A. 甲）"
    assert rec["answer"] == "B"
    assert rec["bank_id"] == "bk1" and rec["origin"] == "md_import"
    assert "_warn" not in rec


def test_normalize_rejects_empty_question():
    assert bank_iq.normalize_item({"question": "   "}, bid="b", origin="x", now="t") is None


def test_normalize_keeps_item_with_warning_on_contract_violation():
    """契约不合（X 型答案 5 个字母）→ **收下并标注**，不静默丢题。

    第三方题库写法千奇百怪；判分口径是集合比较（与题型无关），丢题比带告警收下更糟。
    """
    rec = bank_iq.normalize_item(
        {"question": "题", "options": ["甲", "乙", "丙", "丁", "戊"],
         "answer": "ABCDE", "type": "X"},
        bid="b", origin="x", now="t")
    assert rec is not None and rec["answer"] == "ABCDE"
    assert "_warn" in rec and "契约" in rec["_warn"]


def test_normalize_carries_case_group_and_media_fields():
    """案例 / 选项组 / 图 / 表 / 溯源字段必须**原样带过来**。

    证伪：把这些字段写死成空串（本次修复前的实现）→ 本用例必红。
    """
    rec = bank_iq.normalize_item({
        "question": "子题", "options": [], "answer": "B", "type": "A3",
        "case_id": "c1", "case_order": 2, "case_stem": "案例题干",
        "group_kind": "option_group", "group": {"options": ["甲", "乙"]},
        "image_ref": "fig_1", "data_table": "|a|b|",
        "source_type": "真题", "source_year": "2024",
    }, bid="b", origin="project", now="t")
    assert rec is not None
    assert (rec["case_id"], rec["case_order"], rec["case_stem"]) == ("c1", 2, "案例题干")
    assert rec["group_kind"] == "option_group" and rec["group"]["options"] == ["甲", "乙"]
    assert rec["image_ref"] == "fig_1" and rec["data_table"] == "|a|b|"
    assert (rec["source_type"], rec["source_year"]) == ("真题", "2024")
    # 非法 case_order / 非 dict group → 容错，不抛
    rec2 = bank_iq.normalize_item({"question": "题", "answer": "A",
                                   "case_order": "x", "group": ["甲"]},
                                  bid="b", origin="x", now="t")
    assert rec2 is not None and rec2["case_order"] == 0 and rec2["group"] is None


def test_json_and_csv_paths_reuse_library_parser():
    """json/csv 走 `library.parse_import_text`（不重复实现）。"""
    rows = bank_iq.parse_bank_text(
        '[{"question": "题", "options": ["甲"], "answer": "A"}]', "json")
    assert len(rows) == 1 and rows[0]["question"] == "题"
    csv = "题干,选项,答案\n题干一,甲|乙,A\n"
    rows2 = bank_iq.parse_bank_text(csv, "csv")
    assert len(rows2) == 1 and rows2[0]["options"] == ["甲", "乙"]


# ------------------------------------------------------------------ P0-2：第三方写法（选项在前、题干在后）

@pytest.mark.parametrize("text,expected_q,expected_opts", [
    # 基本：选项在前、题干在后（带列表符）
    ("- A. 甲\n- B. 乙\n这道题的题干是什么\n答案：A",
     "这道题的题干是什么", ["A. 甲", "B. 乙"]),
    # 无列表符的选项在前
    ("A. 甲\nB. 乙\n这道题的题干是什么\n答案：A",
     "这道题的题干是什么", ["A. 甲", "B. 乙"]),
])
def test_third_party_options_before_question(text, expected_q, expected_opts):
    """P0-2 守卫：「选项在前、题干在后」的第三方写法必须正确解析。

    证伪：把 library.py 的 `option_begun = False` 赋值删掉 → 题干被拼进选项 → 本用例必红。
    """
    rows = bank_iq.parse_bank_md(text)
    assert len(rows) == 1, f"应解析出 1 题，实得 {len(rows)}"
    assert rows[0]["question"] == expected_q, f"题干不符：{rows[0]['question']!r}"
    assert rows[0]["options"] == expected_opts, f"选项不符：{rows[0]['options']!r}"


def test_question_after_options_is_not_swallowed_by_last_option():
    """P0-2 根因回归：`option_begun` 为真后，若题干尚未收集到内容，
    后续非选项行必须归题干（而不是拼到最后一个选项上）。"""
    from medkit.core import library as lib
    parsed = lib.parse_question_text("- A. 甲\n- B. 乙\n这道题的题干是什么\n答案：A")
    assert parsed["question"] == "这道题的题干是什么", parsed["question"]
    assert parsed["options"] == ["A. 甲", "B. 乙"], parsed["options"]
    assert "答案" not in parsed["question"], "答案标记不得混进题干"


def test_bold_analysis_marker_is_stripped():
    """P1-4：`**解析：**` 的加粗标记必须被剥掉，解析内容不含 `**`。"""
    text = "题干\n- A. 甲\n- B. 乙\n答案：A\n**解析：**吸烟是 COPD 最重要的环境致病因素"
    rows = bank_iq.parse_bank_md(text)
    assert len(rows) == 1
    assert rows[0]["analysis"] == "吸烟是 COPD 最重要的环境致病因素", f"解析不符：{rows[0]['analysis']!r}"
    assert "**" not in rows[0]["analysis"]
