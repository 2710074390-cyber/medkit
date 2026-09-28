# -*- coding: utf-8 -*-
"""`pack/stage0-cases-check.py` 的守卫用例。

这个脚本的定位是「花钱之前的体检」——它漏判的代价是**真金白银**（跑一轮
模型才发现题样是坏的，得重跑），所以它自己必须有守卫，而且守卫要能证伪。

三类用例：
1. `check_case()` 的逐条判据（硬/软分开）。
2. `main()` 的退出码语义（0 通过 / 1 硬问题 / 2 用法错误）——**退出码是它
   唯一能被 CI 消费的接口**，测函数不测退出码等于没测防线。
3. 真实文件不回归（30 题全绿）。
"""
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "pack" / "stage0-cases-check.py"


def _load():
    spec = importlib.util.spec_from_file_location("stage0_cases_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CHK = _load()
TAGS = CHK._tags()


def _case(**kw):
    """一份完全合法的题样，逐项覆盖来触发判据。"""
    base = {
        "id": "T1",
        "subject": "生理学",
        "chapter": "第一章",
        "topic": "考点",
        "question": "下列关于 X 的叙述，正确的是",
        "options": ["A. 甲", "B. 乙", "C. 丙", "D. 丁", "E. 戊"],
        "answer": "C",
        "user_answer": "A",
        "confidence": 3,
        "my_reasoning": "我当时记成了乙，没注意到题干问的是正确项。",
        "human_tag": "知识盲区",
    }
    base.update(kw)
    return base


def _hard(c):
    return CHK.check_case(c, TAGS)[0]


def _soft(c):
    return CHK.check_case(c, TAGS)[1]


# ---------------------------------------------------------------- 基线

def test_valid_case_passes_clean():
    """合法题样必须硬软都为空，否则后面所有用例的基线都不成立。"""
    assert _hard(_case()) == []
    assert _soft(_case()) == []


def test_authoritative_tags_come_from_schema():
    """标签集必须来自 schema，不是脚本里手写的字面量（防漂移）。"""
    from medkit.core import schema as schema_mod
    assert TAGS == set(schema_mod.ANALYSIS_TAGS)
    assert len(TAGS) == 6


# ---------------------------------------------------------------- 硬判据

@pytest.mark.parametrize("missing", ["id", "question", "options", "answer",
                                     "user_answer", "human_tag"])
def test_missing_required_field_is_hard(missing):
    """缺任一必备字段都是硬问题，且报错要指名缺哪个。"""
    c = _case()
    c.pop(missing)
    msgs = _hard(c)
    assert any(missing in m for m in msgs), msgs


def test_missing_field_short_circuits():
    """字段都不全时不做后续检查（否则会喷一堆无意义的下游报错）。"""
    c = _case()
    c.pop("options")
    assert len(_hard(c)) == 1


def test_answer_equals_user_answer_is_hard():
    """这是错题集：答对的题混进来会污染分母，必须硬挡。"""
    msgs = _hard(_case(answer="A", user_answer="A"))
    assert any("分母" in m for m in msgs), msgs


def test_answer_out_of_option_range_is_hard():
    """只有 3 个选项却答 D → 答案映射不到选项文本。"""
    c = _case(options=["A. 甲", "B. 乙", "C. 丙"], answer="D", user_answer="A")
    assert any("不在选项字母范围" in m for m in _hard(c))


def test_user_answer_out_of_option_range_is_hard():
    c = _case(options=["A. 甲", "B. 乙", "C. 丙"], answer="C", user_answer="E")
    assert any("不在选项字母范围" in m for m in _hard(c))


def test_option_prefix_discontinuity_is_hard():
    """`A.` `B.` `D.` 断档 → 答案 C 会指到 `D.` 的文本上，必须挡。"""
    c = _case(options=["A. 甲", "B. 乙", "D. 丁", "E. 戊", "F. 己"],
              answer="D", user_answer="A")
    assert any("前缀不符" in m for m in _hard(c))


def test_option_without_text_is_hard():
    c = _case(options=["A. 甲", "B. ", "C. 丙", "D. 丁", "E. 戊"])
    assert any("无文本内容" in m for m in _hard(c))


def test_empty_options_is_hard():
    c = _case(options=[], answer="A", user_answer="B")
    assert any("options 为空" in m for m in _hard(c))


def test_unknown_human_tag_is_hard():
    """标签必须落在权威 6 类里，否则「一致率」分母算不出来。"""
    msgs = _hard(_case(human_tag="粗心"))
    assert any("不在权威 6 类" in m for m in msgs), msgs


def test_ai_tag_alone_does_not_satisfy_human_tag():
    """只有 ai_error_tag、没有人工 error_tag 时必须红——这正是导出通道
    刻意过滤的情形（无人工归因就无法判 AI 对错）。"""
    c = _case()
    c.pop("human_tag")
    c["ai_error_tag"] = "知识盲区"
    assert any("human_tag" in m for m in _hard(c))


# ---------------------------------------------------------------- 软判据

def test_empty_reasoning_is_soft_not_hard():
    """真实错题可能没写「当时的想法」，不该硬挡（但要有警告）。"""
    c = _case(my_reasoning="")
    assert _hard(c) == []
    assert any("my_reasoning 为空" in m for m in _soft(c))


def test_long_stem_is_soft():
    c = _case(question="题" * (CHK.PROMPT_LIMITS["STEM_MAX_CHARS"] + 1))
    assert _hard(c) == []
    assert any("会被截断" in m for m in _soft(c))


def test_long_reasoning_is_soft():
    c = _case(my_reasoning="想" * (CHK.PROMPT_LIMITS["REASONING_MAX_CHARS"] + 1))
    assert _hard(c) == []
    assert any("会被截断" in m for m in _soft(c))


@pytest.mark.parametrize("bad", [0, 6, -1, "高"])
def test_confidence_out_of_range_is_soft(bad):
    """置信度越界不该挡人（真实自评未必守 1~5），但要在跑之前看到。"""
    c = _case(confidence=bad)
    assert _hard(c) == []
    assert any("confidence" in m for m in _soft(c))


def test_confidence_none_is_acceptable():
    """未填置信度是合法状态（不是 0，也不是越界）。"""
    c = _case(confidence=None)
    assert _hard(c) == [] and _soft(c) == []


def test_norm_strips_whitespace_and_normalizes_width():
    """查重归一：全角/半角、空格差异不该让同一题漏检。"""
    assert CHK._norm("下列关于 X 的叙述") == CHK._norm("下列关于Ｘ的叙述")
    assert CHK._norm("甲  乙") == CHK._norm("甲乙")
    assert CHK._norm(None) == ""


# ---------------------------------------------------------------- 退出码

def _write(tmp_path, cases):
    p = tmp_path / "cases.json"
    p.write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_exit_0_on_clean_set(tmp_path):
    assert CHK.main(["--cases", _write(tmp_path, [_case()])]) == 0


def test_exit_1_on_hard_problem(tmp_path):
    assert CHK.main(["--cases", _write(tmp_path, [_case(human_tag="粗心")])]) == 1


def test_exit_0_on_soft_without_strict(tmp_path):
    assert CHK.main(["--cases", _write(tmp_path, [_case(my_reasoning="")])]) == 0


def test_exit_1_on_soft_with_strict(tmp_path):
    """`--strict` 是 CI 用的：软问题也要能变成红，否则 CI 只会看到绿。"""
    p = _write(tmp_path, [_case(my_reasoning="")])
    assert CHK.main(["--cases", p, "--strict"]) == 1


def test_exit_2_on_missing_file(tmp_path):
    assert CHK.main(["--cases", str(tmp_path / "nope.json")]) == 2


def test_exit_2_on_invalid_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert CHK.main(["--cases", str(p)]) == 2


def test_exit_2_on_non_list_top_level(tmp_path):
    p = tmp_path / "obj.json"
    p.write_text('{"a": 1}', encoding="utf-8")
    assert CHK.main(["--cases", str(p)]) == 2


def test_duplicate_stem_warns(tmp_path):
    """重复题干是软警告（不挡人），但必须真的报出来——S20/S27 那次就是这样
    发现的：题干一字不差，等于同一考点在结果表里被算了两遍。"""
    a = _case(id="A1")
    b = _case(id="A2")
    b["options"] = ["A. 甲", "B. 乙", "C. 丙", "D. 丁", "E. 戊"]
    b["answer"], b["user_answer"] = "C", "B"
    b["question"] = a["question"] + "  "   # 只差空白 → 归一后应判重
    assert CHK.main(["--cases", _write(tmp_path, [a, b]),
                     "--strict"]) == 1


def test_duplicate_id_is_hard(tmp_path):
    a, b = _case(), _case()
    b["question"] = "完全不同的题干内容，用来排除题干重复的干扰"
    assert CHK.main(["--cases", _write(tmp_path, [a, b])]) == 1


# ---------------------------------------------------------------- 真实文件

def test_repo_cases_file_is_clean(capsys):
    """仓库里那份真实题样必须零硬问题、零软警告。

    这条同时是**回滚守卫**：以后谁再往里插一道重复题/越界答案，这里先红。
    """
    rc = CHK.main(["--cases", str(CHK.DEFAULT_CASES), "--strict"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "硬问题 0 处" in out
    assert "软警告 0 处" in out


def test_repo_cases_file_reaches_target_count():
    """《总纲》§3.4 口径 30 题。"""
    cases = json.loads(CHK.DEFAULT_CASES.read_text(encoding="utf-8"))
    assert len(cases) >= CHK.TARGET_N
