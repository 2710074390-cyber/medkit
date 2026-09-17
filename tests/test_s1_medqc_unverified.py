"""S1-1 回归（R8+W 修复批次 B1）：质检故障不得静默降级为「通过」。

对应收口报告 `docs/reviews/审查报告_2026-09-17_W轮补审.md` §3.1 S1-1：

- 修复前：单批质检 LLM 异常 → `_qc_batch_once` 返回 `decision="PASS_WITH_FIXES"` + `severity="warn"`
  的 `QC_ERR`，聚合层只看 severity → 整批 20 题**跳过事实校验**却仍算「通过（需修复）」。
- 修复后：异常批 → `QC_UNVERIFIED` / `severity="fail"` / `score=-1` / `decision="BLOCKED"`；
  聚合层显式承认批次级 BLOCKED；`QC_UNVERIFIED` 进人工复核清单；且**不触发** MedFix 的 LLM 调用。

反向验证（先红后绿，**两道守卫各自独立可证伪**，2026-09-17 实测）：

| 注入的回归 | 变红的用例 | 结论 |
|---|---|---|
| 异常分支 `severity` 由 `fail` 改回 `warn` | `test_qc_batch_exception_blocks` | 第 1 道守卫（issue 级别）有效 |
| 聚合层去掉 `or "BLOCKED" in decisions` | `test_qc_aggregation_honors_batch_level_blocked` | 第 2 道守卫（批次决策级别）有效 |

> 注意：单独注入第 1 条时，`..._not_counted_in_score` / `..._partial_failure_blocks` 仍为绿——
> 因为第 2 道守卫独立兜住了，属**纵深防御正常表现**，非断言失效。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from medkit.agents import medfix, medqc  # noqa: E402


def _q(i: int = 1) -> dict:
    return {"id": f"Q{i:03d}", "type": "A1", "bloom": "理解", "question": f"题{i}",
            "options": ["a", "b", "c", "d", "e"], "answer": "A",
            "analysis": "解析", "sid": "S001"}


def _qbank(n: int) -> list[dict]:
    return [_q(i) for i in range(1, n + 1)]


class _Boom:
    """chat_json 恒抛异常：模拟质检 LLM 故障（超时/网络/鉴权失败）。"""

    def __init__(self) -> None:
        self.calls = 0

    def chat_json(self, messages, **kwargs):
        self.calls += 1
        raise RuntimeError("模拟质检 LLM 故障")


def test_qc_batch_exception_blocks():
    """单批异常 → QC_UNVERIFIED(fail) + score=-1 + decision=BLOCKED（不再 PASS_WITH_FIXES）。"""
    r = medqc._qc_batch_once(_Boom(), _qbank(2), {"S001": "教材"})
    assert r["decision"] == "BLOCKED", "质检异常批必须判 BLOCKED，不得静默降级"
    assert r["score"] == -1, "异常批不得计分（原为 50 分计入平均）"
    unv = [x for x in r["issues"] if x.get("code") == "QC_UNVERIFIED"]
    assert unv, "必须留下 QC_UNVERIFIED 问题条目供人工复核"
    assert unv[0]["severity"] == "fail", "severity 必须是 fail，否则聚合层仍会放过"
    assert "未经事实校验" in unv[0]["reason"], "文案须明写本批未经事实校验"


def test_qc_batch_exception_not_counted_in_score():
    """1 批正常(90) + 1 批异常 → 平均分只取 90（异常批 -1 被排除，不再拉低为 70）。"""
    class Mixed:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return {"score": 90, "gate_decision": "PASS", "issues": [], "summary": "ok"}
            raise RuntimeError("模拟第二批判分故障")

    r = medqc.qc_batch(Mixed(), _qbank(medqc.BATCH_SIZE + 1),
                       {"S001": "教材"}, concurrency=1)
    assert r["score"] == 90.0, "异常批（-1）不得计入平均分"
    assert r["gate_decision"] == "BLOCKED"


def test_qc_batch_partial_failure_blocks():
    """部分批异常不得被其他批的 PASS 掩盖——整体仍为 BLOCKED。"""
    class OneBad:
        def __init__(self) -> None:
            self.calls = 0

        def chat_json(self, messages, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("模拟第二批判分故障")
            return {"score": 95, "gate_decision": "PASS", "issues": [], "summary": "ok"}

    r = medqc.qc_batch(OneBad(), _qbank(medqc.BATCH_SIZE + 1),
                       {"S001": "教材"}, concurrency=1)
    assert r["gate_decision"] == "BLOCKED", "有批未校验即不得整体判通过"
    assert any(x.get("code") == "QC_UNVERIFIED" for x in r["issues"])


def test_qc_aggregation_honors_batch_level_blocked(monkeypatch):
    """聚合层必须显式承认批次级 BLOCKED——即便该批 issues 里没有 fail 级条目。

    这是脆弱点的根：原聚合只看 `severity == "fail"`，`decisions` 集合收了却从未参与判定。
    这里注入一个「decision=BLOCKED 但 issues 全为 warn」的批次，断言整体仍 BLOCKED。
    """
    def fake_batch(client, batch, slice_by_sid):
        return {"issues": [{"q_id": "Q001", "code": "X", "severity": "warn", "reason": "仅告警"}],
                "score": 88, "decision": "BLOCKED", "summary": "批次级阻断"}

    monkeypatch.setattr(medqc, "_qc_batch_once", fake_batch)
    r = medqc.qc_batch(_Boom(), _qbank(2), {"S001": "教材"}, concurrency=1)
    assert r["gate_decision"] == "BLOCKED", "批次级 BLOCKED 不得因 issues 无 fail 而丢失"


def test_medfix_skips_unverified_issue_without_llm_call():
    """QC_UNVERIFIED 的 q_id 不是真题目 → MedFix 必须零 LLM 调用直接返回（零额外成本）。"""
    class Spy:
        def __init__(self) -> None:
            self.called = False

        def chat_json(self, messages, **kwargs):
            self.called = True
            return {"questions": []}

    spy = Spy()
    out = medfix.fix_questions(
        spy, _qbank(3),
        [{"q_id": "QC_UNVERIFIED", "code": "QC_UNVERIFIED", "severity": "fail",
          "reason": "质检批次异常：本批未经事实校验"}],
        {"S001": "教材"})
    assert out == {"fixed": [], "trace": []}
    assert spy.called is False, "未命中的 issue 不得触发 MedFix 的 LLM 调用"
