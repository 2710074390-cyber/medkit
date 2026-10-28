"""断言级溯源守卫（v0.22 阶段 4 / G5）。

## 这一组在守什么

旧溯源是**字符串对齐**：只要 subtopic 的**前 2 个字**出现在被引切片里就放行
（`trace_check._align_hit`）。于是「引用对了切片、内容说错了」照样 PASS——
门禁看起来在把关，实际上只挡住了「切片 ID 不存在」这一种错。

本阶段加 `check_entailment`：要求每题给出 `source_quote`（教材原文那一句），
并把它拿到**所引切片**里逐字核对。判据分两条：

1. `F3` 引文核不到原文（编造引句 / 引错切片）——默认 `warn`，`strict=True` 时 `fail`；
2. `F4` 正确选项在「引文 + 解析」里没有原样出现——只报 `info`（提示人工确认）。

## 为什么默认不 fail（这条取舍必须被记下来）

提示词要求「逐字照抄」，但模型的「逐字」常带轻微改写。若默认 fail，
一次 50 题的批量会被剔到空题库——**把可选功能做成致命功能**。
所以默认 warn（进 MedFix + 人工复核），`strict` 由配置 `entailment_strict` 打开。
但「编造引文」这件事**必须能被抓住**，否则本阶段的升级等于没做——所以两条路都测。
"""

from __future__ import annotations

import pytest

from medkit.gates import trace_check

SLICE = ("麻疹前驱期一般为3～4天，出现发热、结膜充血、流泪、流涕及口腔麻疹黏膜斑。"
         "皮疹自耳后发际开始，逐渐蔓延至面部、躯干及四肢。")
SLICES = {"S001": SLICE, "S002": "急性前壁心肌梗死心电图表现为V1～V4导联ST段弓背向上抬高。"}


def _q(**kw):
    base = {
        "id": "q1",
        "question": "该患儿最可能的诊断是？",
        "type": "A1",
        "subtopic": "麻疹的诊断",
        "options": ["川崎病", "麻疹", "猩红热", "幼儿急疹", "药疹"],
        "answer": "B",
        "analysis": "发热3天出疹伴结膜充血与麻疹黏膜斑，符合麻疹。【源:切片S001】",
        "source_quote": "麻疹前驱期一般为3～4天，出现发热、结膜充血、流泪、流涕及口腔麻疹黏膜斑。",
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------- 降级
def test_no_quote_means_check_is_unavailable():
    """整批没有 `source_quote`（旧产物）⇒ `available=False` 且**零 issue**。

    这是「降级不阻断」的核心：历史项目重跑不能因为这个新检查全红。
    """
    out = trace_check.check_entailment([_q(source_quote="")], SLICES)
    assert out["available"] is False
    assert out["issues"] == []
    assert out["fail_count"] == 0 and out["warn_count"] == 0


# ---------------------------------------------------------------- 引文核实
def test_verbatim_quote_is_grounded():
    out = trace_check.check_entailment([_q()], SLICES)
    assert out["available"] is True
    assert out["grounded"] == 1 and out["coverage"] == 1.0
    assert [i for i in out["issues"] if i["code"] == "F3"] == []


def test_punctuation_and_space_differences_are_tolerated():
    """排版差异不算编造：全角/半角标点、空格、句末句号的增删都要容忍。

    归一的是**格式**，判的是**内容**——否则模型把「，」写成「,」就毁一道题。
    """
    q = _q(source_quote="麻疹前驱期一般为 3～4 天, 出现发热, 结膜充血, 流泪, 流涕及口腔麻疹黏膜斑")
    out = trace_check.check_entailment([q], SLICES)
    assert out["grounded"] == 1, out["issues"]


def test_ellipsis_split_quote_checks_each_segment():
    """用省略号跳读的引文：**每一段**都要在切片里（防止「首尾真、中间编」）。"""
    ok = _q(source_quote="麻疹前驱期一般为3～4天…皮疹自耳后发际开始")
    assert trace_check.check_entailment([ok], SLICES)["grounded"] == 1

    bad = _q(source_quote="麻疹前驱期一般为3～4天…患儿应终身隔离并接种疫苗")
    out = trace_check.check_entailment([bad], SLICES)
    assert out["grounded"] == 0
    assert any(i["code"] == "F3" and i["severity"] == "warn" for i in out["issues"])


def test_fabricated_quote_warns_by_default_and_fails_when_strict():
    """**注入「引用对、内容错」的样本必须被拦**。

    引用的是真实切片 S001（ID 存在、subtopic 也在里面，旧判据会放行），
    但引文是编的 ⇒ 默认 warn（进人工复核），strict 下 fail（剔除）。
    """
    q = _q(source_quote="麻疹患儿应终身隔离，并对全部接触者注射丙种球蛋白。")
    soft = trace_check.check_entailment([q], SLICES)
    assert soft["grounded"] == 0
    assert any(i["code"] == "F3" and i["severity"] == "warn" for i in soft["issues"])

    hard = trace_check.check_entailment([q], SLICES, strict=True)
    assert hard["fail_count"] == 1
    assert any(i["code"] == "F3" and i["severity"] == "fail" for i in hard["issues"])


def test_quote_from_wrong_slice_is_caught():
    """引文只在**别的**切片里 ⇒ 引错切片，同样抓。"""
    q = _q(source_quote="急性前壁心肌梗死心电图表现为V1～V4导联ST段弓背向上抬高。")
    out = trace_check.check_entailment([q], SLICES)
    assert out["grounded"] == 0, "引用了 S002 的内容却标 S001，不该算核到"


def test_quote_without_slice_id_is_skipped_with_info():
    """有引文但没标切片 ID ⇒ `info`（引文无法核对），不报 fail/warn。

    为什么不一并报 warn：这是**溯源格式**问题，`check_trace` 的 F2 已经在管；
    同一件事报两遍只会让复核清单变长。
    """
    q = _q(analysis="发热3天出疹伴结膜充血，符合麻疹。")
    out = trace_check.check_entailment([q], SLICES)
    codes = [i["code"] for i in out["issues"]]
    assert codes == ["F3"]
    assert out["issues"][0]["severity"] == "info"


# ---------------------------------------------------------------- 答案锚定
def test_answer_anchor_flags_missing_option_text():
    """引文与解析里都没有正确选项的原样文本 ⇒ `info`（提示人工确认解析支撑答案）。"""
    q = _q(options=["川崎病", "猩红热", "麻疹", "幼儿急疹", "药疹"], answer="B",
           analysis="发热3天出疹，符合该病。【源:切片S001】",
           source_quote="麻疹前驱期一般为3～4天，出现发热、结膜充血、流泪、流涕及口腔麻疹黏膜斑。")
    out = trace_check.check_entailment([q], SLICES)
    assert any(i["code"] == "F4" and i["severity"] == "info" for i in out["issues"]), out["issues"]


def test_answer_anchor_passes_when_option_appears():
    """正确选项在引文/解析里出现 ⇒ 不报 F4。"""
    out = trace_check.check_entailment([_q()], SLICES)   # 解析里写了「符合麻疹」
    assert [i for i in out["issues"] if i["code"] == "F4"] == []


# ---------------------------------------------------------------- 覆盖度
def test_coverage_counts_only_questions_with_quotes():
    qs = [_q(), _q(id="q2", source_quote=""), _q(id="q3", source_quote="编出来的引文内容")]
    out = trace_check.check_entailment(qs, SLICES)
    assert out["checked"] == 2, out
    assert out["coverage"] == pytest.approx(0.5)
