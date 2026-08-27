"""R2 全链路 UX 审查修复的回归测试（2026-08-28）。

覆盖：D12（apkg B1 选项，见 test_s3_apkg.py）、B10（_answer_issue）、
ME-6（_sample_paper 案例组原子）、ME-7（select_paper_stable 防漂移）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core.orchestrator import _sample_paper, select_paper_stable  # noqa: E402
from medkit.routers.review import _answer_issue  # noqa: E402


def _mk(i: str, qtype: str, sid: str = "S001", bloom: str = "记忆", **kw) -> dict:
    q = {"id": i, "type": qtype, "bloom": bloom, "subtopic": "考点", "module": "第一章",
         "sid": sid, "question": f"第{i}题题干", "options": ["甲", "乙", "丙", "丁", "戊"],
         "answer": "A", "analysis": "解析【源:切片" + sid + "】"}
    q.update(kw)
    return q


# ---------------------------------------------------------------- B10 答案键校验
def test_answer_issue_basic_rules():
    q = _mk("Q1", "A1", answer="B")
    assert _answer_issue(q) is None
    q = _mk("Q2", "A1", answer="BD")            # 单选多键
    assert _answer_issue(q) and "单字母" in _answer_issue(q)
    q = _mk("Q3", "X", answer="B")              # 多选单键
    assert _answer_issue(q) and "至少 2 个" in _answer_issue(q)
    q = _mk("Q4", "X", answer="BDE")
    assert _answer_issue(q) is None
    q = _mk("Q5", "A1", answer="F")             # 超出选项范围
    assert _answer_issue(q) and "范围外" in _answer_issue(q)
    q = _mk("Q6", "A1", answer="")              # 空答案
    assert _answer_issue(q) and "不能为空" in _answer_issue(q)
    q = _mk("Q7", "A1", answer=" B ")           # 带空格容忍
    assert _answer_issue(q) is None


def test_answer_issue_b1_uses_group_options():
    q = _mk("B1Q", "B1", options=[], group_kind="option_group",
            group={"options": ["支原体", "肺炎链球菌", "腺病毒", "呼吸道合胞病毒", "金黄色葡萄球菌"]},
            answer="B")
    assert _answer_issue(q) is None
    q["answer"] = "Z"
    assert _answer_issue(q) and "范围外" in _answer_issue(q)


# ---------------------------------------------------------------- ME-6 案例组原子抽样
def test_sample_paper_case_group_atomic():
    # 案例子题故意用不同 bloom/sid：旧的「逐题分桶」必然拆散；原子合并后同进同出
    qs = [
        _mk("C1_1", "A3", sid="S002", bloom="记忆", case_id="C001", case_order=1,
            group_kind="case"),
        _mk("C1_2", "A4", sid="S002", bloom="理解", case_id="C001", case_order=2,
            group_kind="case"),
        _mk("C1_3", "A4", sid="S002", bloom="应用", case_id="C001", case_order=3,
            group_kind="case"),
        _mk("C2_1", "A3", sid="S003", bloom="应用", case_id="C002", case_order=1,
            group_kind="case"),
        _mk("C2_2", "A4", sid="S009", bloom="应用", case_id="C002", case_order=2,
            group_kind="case"),
        _mk("S1", "A1", sid="S004", bloom="记忆"),
        _mk("S2", "A1", sid="S005", bloom="记忆"),
        _mk("S3", "X", sid="S006", bloom="理解"),
        _mk("S4", "X", sid="S007", bloom="理解"),
        _mk("S5", "B1", sid="S008", bloom="记忆", options=[],
            group_kind="option_group", group={"options": ["a", "b", "c", "d", "e"]}),
        _mk("S6", "B1", sid="S008", bloom="记忆", options=[],
            group_kind="option_group", group={"options": ["a", "b", "c", "d", "e"]}),
    ]
    case_sizes: dict[str, int] = {}
    for q in qs:
        c = q.get("case_id")
        if c:
            case_sizes[c] = case_sizes.get(c, 0) + 1
    for _ in range(30):                          # 多种子重复验证
        picked = _sample_paper(qs, 5)
        seen: dict[str, set[str]] = {}
        for q in picked:
            cid = q.get("case_id")
            if cid:
                seen.setdefault(cid, set()).add(q["id"])
        for cid, ids in seen.items():            # 命中的案例组必须子题齐全
            assert len(ids) == case_sizes[cid], f"案例组 {cid} 被拆散：{ids}"


# ---------------------------------------------------------------- ME-7 防漂移复用
def test_select_paper_stable_reuses_saved_ids():
    qs = [_mk(f"Q{i:03d}", "A1", sid=f"S{i:03d}", bloom="记忆") for i in range(1, 61)]
    first = _sample_paper(qs, 50)
    ids = [q["id"] for q in first]
    # 题库不变 → 复用原抽样（即便随机状态变化也不换批）
    rounds = [q["id"] for q in select_paper_stable(ids, qs)]
    assert rounds == ids
    # 剔除 10 题后仍复用剩余 40，再补足 50
    alive = [q for q in qs if q["id"] not in ids[:10]]
    picked = select_paper_stable(ids, alive)
    assert len(picked) == 50
    assert set(ids[10:]) <= {q["id"] for q in picked}
    # saved_ids 有幽灵 id → 忽略
    picked2 = select_paper_stable(["GHOST"], qs)
    assert len(picked2) == 50
