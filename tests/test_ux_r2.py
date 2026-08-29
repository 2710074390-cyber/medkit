"""R2 全链路 UX 审查修复的回归测试（2026-08-28）。

覆盖：D12（apkg B1 选项，见 test_s3_apkg.py）、B10（_answer_issue）、
ME-6（_sample_paper 案例组原子）、ME-7（select_paper_stable 防漂移）。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core.orchestrator import (  # noqa: E402
    _review_slice_digest,
    _sample_paper,
    select_paper_stable,
)
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


def test_select_paper_stable_topup_keeps_reused_order():
    """B26：复用不足时保留已复用题 + 从剩余池补足（抽样 N-len(reused) 追加）——
    剔除后重抽不得洗牌已复用题的成员与顺序（旧实现整卷重抽会漂移）。"""
    qs = [_mk(f"Q{i:03d}", "A1", sid=f"S{i:03d}", bloom="记忆") for i in range(1, 61)]
    first = _sample_paper(qs, 50)
    ids = [q["id"] for q in first]
    alive = [q for q in qs if q["id"] not in ids[:10]]   # 剔除前 10 题 → 复用 40，需补 10
    picked = select_paper_stable(ids, alive)
    assert len(picked) == 50, f"应补足到 50，实得 {len(picked)}"
    got = [q["id"] for q in picked]
    # 已复用的 40 题必须原样保留且顺序不变（补足只能追加，不能洗牌）
    assert got[:40] == ids[10:], f"已复用题被洗牌：{got[:40]}"
    # 补足部分来自剩余池且不重复
    assert len(set(got)) == 50, f"补足出现重复：{len(set(got))}"
    assert set(got[40:]) <= {q["id"] for q in alive} - set(ids[10:])


# ---------------------------------------------------------------- B32 手册切片预算轮转
def test_review_slice_digest_covers_all_chapters():
    """B32：6000 预算按切片轮转分配——切片多于预算/1200 时，后面章节也能进入手册。"""
    slices = [{"title": f"第{i + 1}章", "text": "甲乙丙丁戊己庚辛壬癸" * 400}  # 每章 4000 字
              for i in range(8)]
    out = _review_slice_digest(slices, per_slice=1200, budget=6000)
    for i in range(8):
        assert f"第{i + 1}章" in out, f"第{i + 1}章未进入手册（预算应轮转覆盖全部切片）"
    # 正文总量受预算约束（标题与分隔开销很小）
    assert len(out) < 6000 + 200, f"正文超出预算：{len(out)}"

    # 每切片单轮至多 1200：3 切片 × 3000 字、预算 6000 → 每片先 1200 再 800，全部覆盖
    slices3 = [{"title": f"第{i + 1}章", "text": "甲乙丙丁" * 750} for i in range(3)]
    out3 = _review_slice_digest(slices3, per_slice=1200, budget=6000)
    for i in range(3):
        assert f"第{i + 1}章" in out3
    assert len(out3) < 6000 + 200
