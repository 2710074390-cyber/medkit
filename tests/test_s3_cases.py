"""S3-2 案例题（A3/A4）+ B1 组题 · 期望数据结构与行为测试。

D1（2026-08-25 方案已确认）：**扁平 + case_id**——不引入嵌套，兼容现有审核台/编辑器/Anki；
case_stem 在组内每道子题冗余一份（子题独立编辑/剔除/修复时不丢题干）；B1 共享选项存 group 字段。
本文件定义期望契约，实现（prompts/门禁/QC/渲染）须使其全绿。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.agents import medfix  # noqa: E402
from medkit.core.quota import allocate  # noqa: E402
from medkit.gates.dedup_check import check_dup  # noqa: E402
from medkit.gates.options_check import check_all  # noqa: E402
from medkit.render.qbank_html import (  # noqa: E402
    export_anki,
    export_html,
    export_md,
    export_paper_html,
)

CASE_MEMBER = {"id": "Q001", "type": "A3", "bloom": "理解", "subtopic": "生长发育",
               "module": "第一章 生长发育", "sid": "S001",
               "question": "该患儿首先应做的检查是？",
               "options": ["血常规", "骨髓穿刺", "脑脊液检查", "尿常规", "腹部超声"],
               "answer": "C", "analysis": "案例解析。【源:切片S001】",
               "case_id": "C001", "case_order": 1, "group_kind": "case",
               "case_stem": "患儿男，3岁，发热3天，皮疹1天，精神差…"}

B1_MEMBER = {"id": "Q010", "type": "B1", "bloom": "记忆", "subtopic": "病原体",
             "module": "第一章 生长发育", "sid": "S001",
             "question": "上呼吸道感染最常见病原？",
             "options": [], "answer": "B", "analysis": "B1 解析。【源:切片S001】",
             "group_kind": "option_group", "group": {"options": ["支原体", "肺炎链球菌", "腺病毒", "呼吸道合胞病毒", "金黄色葡萄球菌"]}}


def test_d1_flat_structure_shape():
    """D1 契约：扁平字段 + 冗余 case_stem；子题维度的独立字段齐全。"""
    for k in ("case_id", "case_order", "case_stem", "group_kind"):
        assert k in CASE_MEMBER, f"案例子题应携带 {k}"
    assert CASE_MEMBER["group_kind"] == "case"
    assert isinstance(CASE_MEMBER["case_order"], int) and CASE_MEMBER["case_order"] >= 1
    assert "case_stem" in CASE_MEMBER and CASE_MEMBER["case_stem"]  # 冗余存一份（不嵌套）
    assert "group" in B1_MEMBER and B1_MEMBER["group"]["options"]
    assert B1_MEMBER["group_kind"] == "option_group"
    # 不做嵌套：不存在 children/sub_questions
    assert "children" not in CASE_MEMBER and "sub_questions" not in CASE_MEMBER


def test_options_check_a3_and_b1_group():
    # A3 子题：常规 5 选项校验通过
    r = check_all([CASE_MEMBER])
    assert r["fail_count"] == 0, r["issues"]
    # B1 子题：自身无选项，但 group.options 提供 5 项 → 通过（R1 不误报）
    r2 = check_all([B1_MEMBER])
    assert r2["fail_count"] == 0, r2["issues"]
    # B1 无 group（或 group 无选项）→ R1 fail
    bad = {**B1_MEMBER, "group": {}}
    assert any(x["code"] == "R1" for x in check_all([bad])["issues"])


def test_dedup_skips_same_case_and_option_group():
    sib = {**CASE_MEMBER, "id": "Q002", "case_order": 2,
           "question": "该患儿最可能的诊断是？", "answer": "D"}
    # 同案例子题共用题干 → 不应误报近似重复
    r = check_dup([CASE_MEMBER, sib])
    assert r["pairs"] == 0, r["issues"]
    # 不同案例、题干相同 → 仍应查重
    other = {**sib, "id": "Q003", "case_id": "C002"}
    r2 = check_dup([sib, other])
    assert r2["pairs"] >= 1, r2["issues"]
    # B1 同组子题同样跳过
    b1b = {**B1_MEMBER, "id": "Q011", "question": "肺炎链球菌肺炎的首选治疗？"}
    r3 = check_dup([B1_MEMBER, b1b])
    assert r3["pairs"] == 0, r3["issues"]


def test_medfix_preserves_case_group_fields():
    keys = set(medfix.PROVENANCE_KEYS)
    assert {"case_id", "case_order", "case_stem", "group_kind", "group", "sid", "module", "subtopic", "type"} <= keys


def test_export_md_groups_case_and_b1():
    qs = [CASE_MEMBER, {**CASE_MEMBER, "id": "Q002", "case_order": 2,
                        "question": "最可能的诊断是？", "answer": "D"},
          B1_MEMBER]
    md = export_md(qs, "题库")
    assert md.count(CASE_MEMBER["case_stem"]) == 1, "案例题干在 MD 中应只出现一次（组标题）"
    assert "C001" in md and "【案例】" not in md.replace("**【案例】", "")
    assert B1_MEMBER["group"]["options"][0] in md and "选项组" in md


def test_export_html_case_fold_and_b1_shared_options():
    qs = [CASE_MEMBER, {**CASE_MEMBER, "id": "Q002", "case_order": 2,
                        "question": "最可能的诊断是？", "answer": "D"},
          B1_MEMBER]
    html = export_html(qs, "题库")
    assert 'class="case"' in html or "案例" in html, "题库 HTML 应按组折叠呈现"
    assert html.count("患儿男，3岁") == 1, "案例题干只出现一次"
    assert "支原体" in html and "金黄色葡萄球菌" in html, "B1 共享选项应渲染"
    # Anki 子题卡带案例前缀（扁平卡不丢题干）
    txt = export_anki(qs)
    assert "【案例】患儿男" in txt, "Anki 子题应带案例题干前缀"


def test_paper_case_group_layout_and_group_score():
    qs = [CASE_MEMBER, {**CASE_MEMBER, "id": "Q002", "case_order": 2,
                        "question": "最可能的诊断是？", "answer": "D", "case_stem": CASE_MEMBER["case_stem"]}]
    html = export_paper_html(qs, "押题卷")
    assert "casebar" in html or "案例" in html, "押题卷按案例分组呈现"
    assert "groupScore" in html or "caseScore" in html, "分组判分（组内得分汇总）"


def test_quota_count_is_sub_question_total():
    """案例题按子题计数：配额总计 == target（子题维度的计数语义不变）。"""
    slices = [{"sid": "S001", "text": "生长发育三个高峰，婴儿期青春期，出生体重3.25kg。" * 30},
              {"sid": "S002", "text": "儿童营养能量需求，母乳SIgA。" * 20}]
    q = allocate(slices, "生长发育 3.25kg 婴儿期", 20)
    assert sum(x["count"] for x in q) == 20
