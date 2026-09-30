"""选项渲染的不变量（`render/qbank_html.py` + `render/apkg.py`）。

## 守的是一个真实的用户可见缺陷

库里的选项**可能带字母前缀**——文本/图像导入走 `library.parse_question_text`，
它保留原行（`"A. 支气管炎"`）；而从零生成的题库是纯文本（`"支气管炎"`）。
两种形态都会进渲染层，而渲染层**统一补字母**（`LETTERS[i]`）⇒
`["A. 甲"]` 曾被渲染成 **`A. A. 甲`**（apkg / MD / HTML / 页面 JSON 四处都中）。

修法：把「剥已有前缀」收在唯一的取用点 `_effective_options()` 上
（字母是**渲染层**的呈现职责，不是数据的一部分），而不是在 4 个调用点各写一遍。

⚠️ 注意 `gates/options_check.py` 与 `gates/numeric_check.py` 各自也剥过一次——
那是**纵深防御**（幂等，不冲突）。本文件的判据只针对**渲染出口**。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.render.apkg import _fields  # noqa: E402
from medkit.render.qbank_html import (  # noqa: E402
    _effective_options,
    _questions_json_for_page,
    export_html,
    export_md,
)

Q_PREFIXED = {"id": "q1", "question": "题干", "options": ["A. 甲", "B. 乙"], "answer": "A"}
Q_PLAIN = {"id": "q2", "question": "题干", "options": ["甲", "乙"], "answer": "A"}


def test_effective_options_strips_existing_letter():
    assert _effective_options(Q_PREFIXED) == ["甲", "乙"]
    # 纯文本形态行为不变
    assert _effective_options(Q_PLAIN) == ["甲", "乙"]


def test_effective_options_handles_all_separator_styles():
    """`（A）甲` / `A、甲` / `C．甲` 都要剥——只认一种写法就等于没修。"""
    q = dict(Q_PREFIXED, options=["（A）甲", "B、乙", "C．丙", "D: 丁"])
    assert _effective_options(q) == ["甲", "乙", "丙", "丁"]


def test_effective_options_is_idempotent():
    """幂等：已经剥过的再进一次不变（纵深防御的前提）。"""
    once = _effective_options(Q_PREFIXED)
    assert _effective_options(dict(Q_PREFIXED, options=once)) == once


def test_effective_options_does_not_strip_plain_text_letters():
    """**不误剥**：正文以字母开头但没有分隔符（「A型题说明」）时保持原样。

    这条防的是"把判据写宽"——剥错了会静默改掉选项内容，比多一个字母更难发现。
    """
    assert _effective_options(dict(Q_PREFIXED, options=["A型题说明", "B超检查"])) == \
        ["A型题说明", "B超检查"]


def test_apkg_field_has_exactly_one_letter():
    """apkg 的「选项」字段：每个选项前**恰好一个**字母。"""
    assert _fields(Q_PREFIXED)["选项"] == "A. 甲<br>B. 乙"
    assert _fields(Q_PLAIN)["选项"] == "A. 甲<br>B. 乙"


def test_md_export_has_exactly_one_letter():
    md = export_md([Q_PREFIXED])
    assert "- A. 甲" in md and "- B. 乙" in md
    assert "A. A." not in md


def test_html_page_payload_options_are_stripped():
    """页面内嵌 JSON（押题卷/题库页的客户端渲染）也走同一取用点 ⇒ 同样只补一次字母。"""
    payload = json.loads(_questions_json_for_page([Q_PREFIXED]))
    assert payload[0]["options"] == ["甲", "乙"], payload[0]["options"]


def test_html_export_has_exactly_one_letter():
    html = export_html([Q_PREFIXED])
    assert "A. A." not in html
    assert "<b>A</b> · 甲" in html
