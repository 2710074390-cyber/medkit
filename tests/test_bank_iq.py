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


# ================================================================== R13（2026-10-13）
# 第三方题库写法：**分节标题 / 题号标签 / 溯源引用 / 加粗选项**。
# 样本取自实测文件 `ALL_questions_FIXED.md`（急诊与灾难医学 batch035）——用户报「刷题时
# 标题被当成题目、题目内容识别错误」即由此格式触发：231 题里 11 题是章节空壳，
# 其余题干混进 `[X型题｜理解] id` / `> 模块：M1｜页码：教材P1` / 选项 / 答案。
_THIRD_PARTY_MD = """# 急诊与灾难医学 batch035 题库（220题） 题库（220题）

> 来源：最终产物\\batch035\\ALL_questions_FIXED.json | 导出：2026-09-07 10:09
> 题型分布：A1×100、A2×46、X×30、A3×24、B1×20

---

## M1（8题）

### 1. [A1型题｜理解] batch035-M1-A1-001

> 模块：M1｜页码：教材P1

**急诊医学的核心内涵是**

- A. 院前急救、院内急诊和危重症监护一体化模式
- **B. 早期判断,迅速救治,保护脏器功能生命安全** ✅
- C. 急救人员、车辆、通信的调动准备和现场急救

**答案：B**

> 解析：急诊医学是……教材P1。

---

### 8. [X型题｜理解] batch035-M1-X-001

> 模块：M1｜页码：教材P1-2

**我国急救医疗服务体系（EMSS）的组成部分包括**

- **A. 院前急救** ✅
- **B. 医院急诊** ✅
- **C. 危重症监护** ✅
- D. 社区卫生服务中心
- E. 康复医疗机构

**答案：ABC**

> 解析：我国目前的急救医疗服务体系……教材P1-2。

---

## M2（30题）

本章介绍常见急症的识别与处理。

### 1. [A1型题｜记忆] batch035-M2-A1-001

> 模块：M2｜页码：教材P10

**心跳骤停最可靠且最早的判断依据是**

- **A. 意识丧失伴大动脉搏动消失** ✅
- B. 瞳孔散大
- C. 心电图呈直线

**答案：A**

> 解析：意识丧失伴大动脉搏动消失是最可靠且最早的判断依据。教材P10。

---
"""


def test_section_headings_are_not_questions():
    """分节标题（`## M1（8题）`）不是题目——文档标题 + 2 个分节标题不得产出空壳题。

    实测：231 题里 11 题是「题干=章节名、无选项无答案」的空壳（源文件声明 220 题）。
    """
    rows = bank_iq.parse_bank_md(_THIRD_PARTY_MD)
    assert len(rows) == 3, f"应 3 道题（2 个分节标题都不成题），实得 {len(rows)}"
    qs = [r["question"] for r in rows]
    assert not any("（8题）" in q or "（30题）" in q for q in qs), f"分节标题变成了题目：{qs}"
    assert not any(q.startswith("#") for q in qs), f"文档标题变成了题目：{qs}"
    # 带正文的分节标题（M2 下有一句说明）也不得成题
    assert not any("本章介绍" in q for q in qs), f"分节标题正文混进了题干：{qs}"


def test_third_party_heading_metadata_goes_to_fields_not_stem():
    """`### 1. [A1型题｜理解] batch035-M1-A1-001` 的题型/id 进字段，**不进题干**。"""
    r0 = bank_iq.parse_bank_md(_THIRD_PARTY_MD)[0]
    assert r0["question"] == "急诊医学的核心内涵是", f"题干不符：{r0['question']!r}"
    assert r0["type"] == "A1", f"题型未从标题标签识别：{r0.get('type')!r}"
    assert r0["bloom"] == "理解", f"bloom 未从标题标签识别：{r0.get('bloom')!r}"
    assert r0["origin_ref"] == {"source_id": "batch035-M1-A1-001",
                               "module": "M1", "page": "教材P1"}, r0.get("origin_ref")
    # 题号标签与溯源引用都不得残留在题干里
    for bad in ("batch035", "A1型题", "模块：", "页码：", "**", "---"):
        assert bad not in r0["question"], f"题干残留 {bad!r}：{r0['question']!r}"


def test_bold_options_are_recognized_with_checkmark():
    """加粗 + 对勾的选项（`- **B. 早期判断…** ✅`）必须被识别，且剥掉 `**` 与 ✅。

    实测缺陷：旧实现只认行首即字母 ⇒ 只抓到未加粗的 D/E 两行，
    X 题 5 个选项变 2 个（**选项错位**），其余选项被并进题干。
    """
    rows = bank_iq.parse_bank_md(_THIRD_PARTY_MD)
    a1, x = rows[0], rows[1]
    assert a1["options"] == [
        "A. 院前急救、院内急诊和危重症监护一体化模式",
        "B. 早期判断,迅速救治,保护脏器功能生命安全",
        "C. 急救人员、车辆、通信的调动准备和现场急救"], a1["options"]
    assert x["options"] == ["A. 院前急救", "B. 医院急诊", "C. 危重症监护",
                            "D. 社区卫生服务中心", "E. 康复医疗机构"], x["options"]
    for opt in x["options"]:
        assert "**" not in opt and "✅" not in opt, f"选项残留呈现标记：{opt!r}"


def test_x_type_label_is_recognized():
    """`[X型题｜理解]` 必须识别为 X 型（`[A-H]` 覆盖不到 X，实测漏判 30 道多选）。"""
    x = bank_iq.parse_bank_md(_THIRD_PARTY_MD)[1]
    assert x["type"] == "X", f"X 型未识别：{x.get('type')!r}"
    assert x["answer"] == "ABC"
    assert x["question"] == "我国急救医疗服务体系（EMSS）的组成部分包括", x["question"]


def test_bold_stem_is_stem_not_subtopic():
    """标题下第一行整行加粗：**其后是选项** ⇒ 它就是题干（不是 subtopic）。

    对照（MedKit 导出）：**其后还有题干行** ⇒ 它才是 subtopic。两种写法都必须对。
    """
    rows = bank_iq.parse_bank_md(_THIRD_PARTY_MD)
    assert rows[0]["subtopic"] == "", f"加粗题干被误当 subtopic：{rows[0]['subtopic']!r}"
    # MedKit 自己的格式（加粗行后还有题干行）仍应把加粗行当 subtopic
    medkit_md = export_md(_CASES[0], "题库")
    r = bank_iq.parse_bank_md(medkit_md)[0]
    assert r["subtopic"] == "慢阻肺的诊断" and r["question"] == "慢阻肺最主要的病因是"


def test_inline_emphasis_markers_are_stripped_from_question():
    """题干里的行内强调（`**错误的**`）是呈现标记，必须剥掉（渲染层不会解释它）。"""
    rows = bank_iq.parse_bank_md(
        "### 1. 关于急诊医学专业特点的描述，**错误的**是\n"
        "- A. 甲\n- B. 乙\n**答案：A**\n💡 解析")
    assert rows[0]["question"] == "关于急诊医学专业特点的描述，错误的是", rows[0]["question"]


def test_horizontal_rule_does_not_leak_into_analysis():
    """题目之间的 `---` 不得残留在解析尾部（实测 `…教材P1-2。\\n\\n---`）。"""
    r = bank_iq.parse_bank_md(_THIRD_PARTY_MD)[0]
    assert r["analysis"] == "急诊医学是……教材P1。", f"解析不符：{r['analysis']!r}"
    assert "---" not in r["analysis"]


def test_mistakes_md_path_strips_md_scaffold():
    """「错题导入」的 md 分支（`library.parse_import_text`）同样不得把脚手架当题目。

    实测：同一份文件在该路径下多出 1 道「文档标题 + 分节标题」空壳题（221 vs 220），
    且每道题干残留 `[A1型题｜理解] id` 与 `> 模块：M1｜页码：教材P1`。
    """
    from medkit.core import library as lib
    rows = lib.parse_import_text(_THIRD_PARTY_MD, "md")
    assert len(rows) == 3, f"应 3 道题，实得 {len(rows)}"
    for r in rows:
        q = r["question"]
        for bad in ("batch035", "型题", "模块：", "页码：", "#"):
            assert bad not in q, f"题干残留脚手架 {bad!r}：{q!r}"
    assert rows[0]["options"] == [
        "A. 院前急救、院内急诊和危重症监护一体化模式",
        "B. 早期判断,迅速救治,保护脏器功能生命安全",
        "C. 急救人员、车辆、通信的调动准备和现场急救"], rows[0]["options"]
    # `> 解析：…` 是解析，**不得**被当成元数据剥掉
    assert rows[0]["analysis"].startswith("急诊医学是"), rows[0]["analysis"]
