"""U-13（R6-09 / 独立核查 H-5）产物页 XSS 回归守卫。

**方法**：恶意输入探针——把 XSS 载荷灌进**每一个**会进入产物的字段，断言产物原文里
不出现该载荷（即已被转义）。比人工逐点核对「拼接点是否转义」更可靠。

**背景**：R5-B-11 报「产物页拼接点转义覆盖不均」。本文件把「不均」变成可机械核验的断言，
覆盖 题库 HTML / 押题卷 HTML / Anki / Markdown 四条导出路径，以及 案例组 / B1 选项组 两种分组形态。
"""

import re

import pytest

from medkit.render.qbank_html import export_anki, export_html, export_md, export_paper_html

# 两种典型载荷：标签注入（文本上下文）与属性逃逸（属性上下文）
PAYLOADS = [
    "<img src=x onerror=alert(1)>",
    '" onmouseover="alert(1)" x="',
]

_STR_FIELDS = {
    "id": "", "type": "A1", "bloom": "理解", "subtopic": "", "question": "",
    "answer": "A", "analysis": "", "source_type": "真题", "source_year": "",
    "module": "", "sid": "", "case_id": "", "case_stem": "",
}
_LIST_FIELDS = ["options", "know_tags"]


def _evil(payload: str, kind: str) -> dict:
    q = dict(_STR_FIELDS)
    for k in list(_STR_FIELDS):
        q[k] = payload
    q["answer"] = "A"           # 答案需非空（渲染契约）
    q["options"] = [payload, "b", "c", "d", "e"]
    q["know_tags"] = [payload]
    if kind == "case":
        q["group_kind"] = "case"
    elif kind == "og":
        q["type"] = "B1"
        q["group_kind"] = "option_group"
        q["options"] = []
        q["group"] = {"options": [payload, "b", "c", "d", "e"]}
    return q


def _strip_data_island(html: str) -> str:
    """剥掉押题卷的内嵌 JSON 数据岛（`let QUESTIONS = (…);`）。

    数据岛内的原文出现**是预期且安全的**：`_questions_json_for_page` 用 `json.dumps` 生成并
    把 `</` 转义为 `<\\/` 防 `</script>` 逃逸；真正的渲染由前端 `esc()` 完成
    （见下方 `test_paper_js_escapes_every_rendered_field`）。本函数用于检查**数据岛之外**
    的静态 HTML 拼接是否也安全。
    """
    return re.sub(r"let QUESTIONS = \(.*?\);\n", "", html, flags=re.S)


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("kind", ["single", "case", "og"])
def test_export_html_escapes_all_fields(payload, kind):
    """题库 HTML：所有字段均须转义（含 data-* 属性上下文）。"""
    out = export_html([_evil(payload, kind)], "题库")
    assert payload not in out, f"题库 HTML 泄漏载荷（{kind}）：{payload!r}"


@pytest.mark.parametrize("payload", PAYLOADS)
@pytest.mark.parametrize("kind", ["single", "case", "og"])
def test_export_paper_html_static_part_is_escaped(payload, kind):
    """押题卷 HTML：数据岛之外的静态拼接不得原文出现载荷。"""
    out = _strip_data_island(export_paper_html([_evil(payload, kind)], "押题卷"))
    assert payload not in out, f"押题卷静态 HTML 泄漏载荷（{kind}）：{payload!r}"


def test_paper_js_escapes_every_rendered_field():
    """押题卷前端渲染必须对每个题目字段走 `esc()`（数据岛是 JSON，安全性取决于此）。"""
    # 数据岛转义验证需带 `</` 的载荷（空题目集不会产生转义后的 `<\\/`）
    probe = {"id": "Q001", "type": "A1", "bloom": "理解", "subtopic": "s",
             "question": "看这里</script><script>alert(1)</script>",
             "options": ["a", "b", "c", "d", "e"], "answer": "A", "analysis": "a"}
    src = export_paper_html([probe], "押题卷")
    for field in ("q.question", "q.bloom", "q.answer", "q.analysis",
                  "q.case_id", "q.case_label", "q.source_year"):
        assert f"esc({field}" in src, f"押题卷 JS 未对 {field} 做 esc()"
    assert "let QUESTIONS = (" in src
    assert "</script><script>" not in src, "数据岛未阻断 `</script>` 逃逸"
    assert "<\\/script>" in src, "数据岛应把 `</` 转义为 `<\\/`"


@pytest.mark.parametrize("payload", PAYLOADS)
def test_export_anki_escapes_id_and_fields(payload):
    """Anki 导出声明 `#html:true`，卡面字段必须转义（U-13 实测曾泄漏 `id`）。"""
    out = export_anki([_evil(payload, "single")])
    assert payload not in out, f"Anki 导出泄漏载荷：{payload!r}"


@pytest.mark.parametrize("payload", PAYLOADS)
def test_export_md_escapes_html_significant_chars(payload):
    """Markdown 导出：含 `<` 的载荷必须被实体化。

    注：Markdown **文本上下文**里 `"` 本身无害（不存在属性逃逸场景），故只对含标签的载荷
    断言「原文不再出现」；对纯引号载荷只要求无原始标签形态。
    """
    out = export_md([_evil(payload, "single")], "题库")
    assert "<img" not in out, "MD 导出泄漏原始标签"
    if "<" in payload:
        assert payload not in out, f"MD 导出泄漏载荷：{payload!r}"

