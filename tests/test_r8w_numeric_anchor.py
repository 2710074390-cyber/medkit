"""B4 回归（R8+W 修复批次）：医学事实的程序化锚点。

覆盖：
- **S1-2**：数值核验——**正确选项**里的临床数值必须有源文本出处，否则 `fail`（不经过 LLM）。
- **S1-3**：查源——题干整段照抄源切片 → `SRC_COPY` warn 进人工复核。
- **接线级**：门禁① 真的挂了这两条通道（只测 helper 会漏掉「调用点没接上」这类回归）。

⚠️ 本文件里 `test_distractor_numbers_are_not_flagged` 是**防回归的关键对照**：
数值核验的首版实现把 `options` 整体纳入核对，而选择题的**干扰项本来就是错误数值**
（把 110 改成 150 才叫干扰项）→ 几乎所有数值型选择题都会被判 fail。手工试跑立刻暴露。
"""

import json
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.core import config as cfgmod  # noqa: E402
from medkit.core import orchestrator as orch  # noqa: E402
from medkit.gates import dedup_check, numeric_check  # noqa: E402

SRC = "能量需求110kcal/kg，母乳含SIgA。出生体重3.25kg，1岁10kg。"
OPTIONS = ["110kcal/kg", "150kcal/kg", "80kcal/kg", "200kcal/kg", "90kcal/kg"]


def _q(qid="Q001", answer="A", stem="婴儿能量需求为多少？", sid="S001", options=None):
    return {"id": qid, "sid": sid, "type": "A1", "bloom": "记忆", "question": stem,
            "answer": answer, "options": options or list(OPTIONS),
            "analysis": f"解析【源:切片{sid}】"}


# ---------------------------------------------------------------- S1-2

def test_wrong_dose_in_correct_option_fails_without_llm():
    """报告要求的核心断言：错误剂量（正确选项）→ 数值门禁 fail，**无任何 LLM 介入**。"""
    r = numeric_check.check_numbers([_q(answer="B")], source_texts={"S001": SRC})
    fails = [x for x in r["issues"] if x["severity"] == "fail"]
    assert fails, "正确选项是 150kcal/kg（源文本为 110）必须判 fail"
    assert fails[0]["code"] == "NUM_UNSOURCED"
    assert "找不到出处" in fails[0]["reason"]
    assert r["passed"] is False


def test_distractor_numbers_are_not_flagged():
    """干扰项本来就是错误数值——不得因此判 fail（首版实现正是在这里翻车）。"""
    r = numeric_check.check_numbers([_q(answer="A")], source_texts={"S001": SRC})
    assert r["issues"] == [], f"正确选项 110 有出处即应干净，实际：{r['issues']}"
    assert r["passed"] is True


def test_case_stem_numbers_only_warn():
    """病例题干可自行构造数值（如「出生体重4.2kg」）→ 只 warn，绝不 fail。"""
    q = _q(stem="出生体重4.2kg的男婴，其能量需求为？", answer="A")
    r = numeric_check.check_numbers([q], source_texts={"S001": SRC})
    assert r["fail_count"] == 0
    assert [x["severity"] for x in r["issues"]] == ["warn"]


def test_x_type_multi_answer_all_options_checked():
    """X 型多答案：任一正确选项的临床数值无出处即 fail。"""
    q = _q(answer="AB", options=OPTIONS)
    r = numeric_check.check_numbers([q], source_texts={"S001": SRC})
    assert r["fail_count"] == 1, "A 有源、B(150) 无源 → 应判 fail"


def test_no_source_text_skips_check():
    """取不到源文本 → 跳过，不妄判（纯自命题场景）。"""
    r = numeric_check.check_numbers([_q(sid="S999", answer="B")], source_texts={"S001": SRC})
    assert r["issues"] == [] and r["checked"] == 0


# ---------------------------------------------------------------- S1-3

def test_verbatim_copied_stem_flagged():
    """逐字复制切片文本当题干 → SRC_COPY warn。"""
    q = _q(stem="能量需求110kcal/kg，母乳含SIgA", answer="A")
    r = dedup_check.check_dup([q], source_texts={"S001": SRC})
    assert [x["code"] for x in r["issues"]] == ["SRC_COPY"]
    assert r["issues"][0]["severity"] == "warn"
    assert r["src_checked"] == 1


def test_normal_stem_not_flagged_as_copy():
    """正常命题（复用术语但重新组织）不得被误报。"""
    q = _q(stem="关于母乳喂养的优点，下列哪项说法正确？", answer="A")
    r = dedup_check.check_dup([q], source_texts={"S001": SRC})
    assert r["issues"] == []


def test_backward_compatible_without_source_texts():
    """不传 source_texts → 只做题间查重（向后兼容），不产生 SRC_COPY。"""
    a = _q(qid="Q001", stem="关于母乳喂养的优点，下列哪项说法正确？")
    b = _q(qid="Q002", stem="关于母乳喂养的优点，下列哪项说法正确？")
    r = dedup_check.check_dup([a, b])
    assert {x["code"] for x in r["issues"]} == {"DUP"}
    assert r["src_checked"] == 0


# ---------------------------------------------------------------- 接线级

@pytest.fixture()
def iso_cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(cfgmod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfgmod, "CONFIG_FILE", tmp_path / "config.json")
    orig_load = cfgmod.load

    def _load():
        c = orig_load()
        c["projects_dir"] = str(tmp_path / "projects")
        return c

    monkeypatch.setattr(cfgmod, "load", _load)
    return tmp_path


class _NoopFix:
    """MedFix 客户端：返回空修复（模拟「改不动」→ 走到剔除分支）。"""

    def chat_json(self, messages, **kwargs):
        return {"questions": []}


def test_gate1_wires_numeric_and_source_check(iso_cfg):
    """门禁① 必须真的调用数值核验与查源（接线级：只测 helper 会漏掉「没接上」）。"""
    base = iso_cfg / "projects" / "p_gate"
    base.mkdir(parents=True)
    meta_path = base / "meta.json"
    meta_path.write_text(json.dumps({"pid": "p_gate", "stage": "gate1"}), encoding="utf-8")

    bad = _q(qid="Q001", answer="B")                       # 正确选项 150 → 数值 fail
    good = _q(qid="Q002", answer="A")                      # 干净
    questions, cancel_out = orch._stage_gate1(
        base=base, meta_path=meta_path, questions=[bad, good],
        cancel=threading.Event(), done_sids=set(),
        fix_client_fn=lambda ev: _NoopFix(), text_by_sid={"S001": SRC},
        bloom_target=None, known_sids={"S001"})

    assert cancel_out is None
    # ① 门禁轮次文件里必须有 numeric 段（证明接线成功）
    round1 = json.loads((base / "质检报告" / "gate1_round1.json").read_text(encoding="utf-8"))
    assert "numeric" in round1, "门禁① 未接入数值核验（S1-2 接线回归）"
    assert round1["numeric"]["checked"] >= 1, "数值核验必须真的跑了（不是空壳）"
    assert "src_checked" in round1["dup"], "门禁① 未接入查源通道（S1-3 接线回归）"
    # ② 修复轮用尽后，数值未达标的题被剔除
    ids = {q["id"] for q in questions}
    assert "Q002" in ids, "干净题不得被误剔"
    assert "Q001" not in ids, "正确选项数值无出处的题应在修复轮用尽后剔除"


def test_gate1_keeps_clean_questions_untouched(iso_cfg):
    """对照组：全部干净时门禁① 一轮即过，不误伤。"""
    base = iso_cfg / "projects" / "p_clean"
    base.mkdir(parents=True)
    meta_path = base / "meta.json"
    meta_path.write_text(json.dumps({"pid": "p_clean", "stage": "gate1"}), encoding="utf-8")
    qs = [_q(qid="Q001", answer="A"), _q(qid="Q002", answer="A")]
    questions, cancel_out = orch._stage_gate1(
        base=base, meta_path=meta_path, questions=qs, cancel=threading.Event(),
        done_sids=set(), fix_client_fn=lambda ev: _NoopFix(),
        text_by_sid={"S001": SRC}, bloom_target=None, known_sids={"S001"})
    assert cancel_out is None
    assert {q["id"] for q in questions} == {"Q001", "Q002"}
