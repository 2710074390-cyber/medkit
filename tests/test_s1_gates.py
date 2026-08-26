"""S1-D 门禁与出题健壮性回归测试：

Bloom 配比小数兼容 + 合计≠100 归一 / options=null 防御 + 超发截断 / 模板一次性替换（防二次注入）/
溯源全角冒号 / 查重保留数字判别 / Bloom 小题量放宽（fail→warn）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.agents import medgen  # noqa: E402
from medkit.gates.bloom_check import check_bloom  # noqa: E402
from medkit.gates.dedup_check import check_dup  # noqa: E402
from medkit.gates.trace_check import check_trace  # noqa: E402


def test_bloom_ratio_str_float_and_legacy():
    # int(0.3)=0 的旧实现静默回退 → 现在按小数归一
    assert medgen._bloom_ratio_str({"记忆": 0.3, "理解": 0.4, "应用": 0.25, "创造": 0.05}) \
        == "30% / 40% / 25% / 5%"
    # 百分比形式（合计 100）保持不变
    assert medgen._bloom_ratio_str({"记忆": 40, "理解": 40, "应用": 15, "创造": 5}) \
        == "40% / 40% / 15% / 5%"
    # 合计 ≠100（60）→ 归一化为 100
    out = medgen._bloom_ratio_str({"记忆": 20, "理解": 20, "应用": 10, "创造": 10})
    assert "33.3%" in out and "16.7%" in out, out
    # 空/None → 默认 30/40/25/5
    assert medgen._bloom_ratio_str(None) == "30% / 40% / 25% / 5%"


def test_parse_questions_null_options_defense():
    slice_ = {"sid": "S001", "title": "第一章"}
    # 显式 null options（setdefault 不会覆盖）→ 空列表，不崩
    qs = medgen._parse_questions(
        {"questions": [{"question": "题", "options": None, "type": None, "answer": None}]}, slice_)
    assert qs[0]["options"] == []
    assert qs[0]["type"] == "A1" and qs[0]["answer"] == ""
    # 非列表 options → 空列表；混合类型 → 保留字符串/数字
    qs2 = medgen._parse_questions(
        {"questions": [{"question": "题2", "options": [{"x": 1}, "甲", 5]}]}, slice_)
    assert qs2[0]["options"] == ["甲", "5"]
    # 非 dict 项被过滤
    assert medgen._parse_questions({"questions": [{"question": None}, "x"]}, slice_) == []


def test_generate_slice_truncates_overflow():
    calls = []

    class FakeOver:
        def chat_json(self, messages, **kwargs):
            calls.append(messages[-1].get("content", ""))
            return {"questions": [
                {"type": "A1", "bloom": "记忆", "question": f"题{i}？",
                 "options": ["a", "b", "c", "d", "e"], "answer": "A",
                 "analysis": f"解析{i}。【源:切片S001】"} for i in range(10)]}

    qs, _ = medgen.generate_slice(FakeOver(), "儿科", "期末", {"sid": "S001", "title": "章", "text": "文"},
                                  5, {"A1": 100}, "教师重点")
    assert len(qs) == 5, f"LLM 超发 10 题应按配额截断为 5（实际 {len(qs)}）"
    assert len(calls) == 1, "截断后不应再触发补充调用"


def test_template_single_pass_no_second_injection():
    captured = []

    class FakeSpy:
        def chat_json(self, messages, **kwargs):
            captured.append(messages[0]["content"])
            return {"questions": [{"question": "题", "options": ["a", "b", "c", "d", "e"],
                                   "answer": "A", "analysis": "解"}]}

    # 教材文本含 {teacher_text} 字面量 → 旧实现会被二次替换注入教师文本
    slice_ = {"sid": "S001", "title": "章", "text": "正文含 {teacher_text} 字面量"}
    medgen.generate_slice(FakeSpy(), "儿科", "期末", slice_, 1, {"A1": 100}, "教师秘密文本")
    system = captured[0]
    assert system.count("教师秘密文本") == 1, "教师文本只应出现一次（占位符替换）"
    assert "{teacher_text}" in system, "教材中的字面量 {teacher_text} 不应被二次替换"


def test_reference_block_injection():
    """v0.5.2：自备真题/补充资料注入——块存在、防照抄硬约束在、不传则整块消失。"""
    captured = []

    class FakeSpy:
        def chat_json(self, messages, **kwargs):
            captured.append(messages[0]["content"])
            return {"questions": [{"question": "题", "options": ["a", "b", "c", "d", "e"],
                                   "answer": "A", "analysis": "解"}]}

    slice_ = {"sid": "S001", "title": "章", "text": "教材正文"}
    medgen.generate_slice(FakeSpy(), "儿科", "期末", slice_, 1, {"A1": 100}, "教师重点",
                           exam_text="2024真题原文：佝偻病初期表现……",
                           extra_text="课件笔记：维生素D来源……")
    system = captured[0]
    assert "用户自备真题参考" in system and "严禁照抄" in system
    assert "2024真题原文：佝偻病初期表现……" in system
    assert "用户自备补充资料" in system and "维生素D来源" in system
    assert "[源:切片SXXX]" in system, "真题块应声明溯源仍指向教材切片"
    # 超长截断：>4000 字的真题只保留前 4000（模板自身零星含「题」，用连续长串断言）
    captured.clear()
    medgen.generate_slice(FakeSpy(), "儿科", "期末", slice_, 1, {"A1": 100}, "教师重点",
                           exam_text="题" * 5000)
    assert "题" * (medgen.EXAM_CHAR_LIMIT + 1) not in captured[0], "真题注入应截断到上限"
    # 不传 → 整块不出现（不污染默认流程）
    captured.clear()
    medgen.generate_slice(FakeSpy(), "儿科", "期末", slice_, 1, {"A1": 100}, "教师重点")
    assert "用户自备真题参考" not in captured[0] and "用户自备补充资料" not in captured[0]


def test_trace_fullwidth_colon_ok():
    # 全角冒号「源：」应被识别（旧实现只认半角 → 误报 F2）
    r = check_trace([{"id": "X", "analysis": "……【源：切片S001】"}], {"S001"})
    assert r["fail_count"] == 0, r["issues"]
    assert r["ok_count"] == 1
    # 半角仍正常
    r2 = check_trace([{"id": "X", "analysis": "……【源:切片S001】"}], {"S001"})
    assert r2["fail_count"] == 0
    # 全角 + 不存在切片 → F2
    r3 = check_trace([{"id": "X", "analysis": "……【源：切片S999】"}], {"S001"})
    assert r3["fail_count"] == 1


def test_dedup_keeps_digits():
    # 「血钾 5.5」vs「血钾 7.0」：仅数值不同 → 不应误报近似重复
    r = check_dup([
        {"id": "Q001", "question": "血钾 5.5mmol/L 的处理，下列正确的是？"},
        {"id": "Q002", "question": "血钾 7.0mmol/L 的处理，下列正确的是？"},
    ])
    assert r["pairs"] == 0, r["issues"]
    # 完全相同 → 仍查重
    r2 = check_dup([
        {"id": "Q001", "question": "关于儿童生长发育规律，下列哪项描述是正确的？"},
        {"id": "Q002", "question": "关于儿童生长发育规律，下列哪项描述是正确的？"},
    ])
    assert r2["pairs"] >= 1


def test_bloom_small_bank_relaxed():
    # n=5（<10）：单题 20% 占比 → 偏差超限也应降级为 warn（不再触发 MedFix 空转）
    small = check_bloom([{"bloom": "记忆"}] * 4 + [{"bloom": "应用"}])
    assert small["fail_count"] == 0, small["issues"]
    assert any(x["severity"] == "warn" for x in small["issues"])
    # n=50：同样的极端分布 → 仍 fail
    big = check_bloom([{"bloom": "记忆"}] * 40 + [{"bloom": "应用"}] * 10)
    assert big["fail_count"] >= 1, big["issues"]
