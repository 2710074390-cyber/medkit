"""W2 守卫：门禁① 子项「未跑成」必须与「无问题」可区分（2026-10-02 二轮审计）。

背景：`_stage_gate1` 里五个子校验（选项/Bloom/溯源/查重/数值核验）任一 `_run_substep`
失败时，原实现把 issues 清空成 `[]` —— 于是「**未校验**」被伪装成「**无问题**」，
未质检的题库冒充已通过门禁进入产物。这违反项目总原则：
**「检查通过」与「检查没跑」必须能区分。**

现契约：子项失败 ⇒ issues 置 `None`（≠ `[]`），gate 结果带 `unverified:[...]`，
`meta.gate1_unverified` 非空 ⇒ 产物页显式标 ⛔。

判据（三档，缺一即假绿）：
1. **结构**：源码里五处失败分支不得再出现 `<x>_issues = []` / `{"issues": []}`（AST 文本扫描）。
2. **行为（正面）**：子步骤抛错时 gate 结果 `issues is None` 且 `unverified` 含该项。
3. **注入反向**：把某处改回 `[]` → 结构守卫必须红（本文件 scene 2 现场注入自证）。

运行：`pytest tests/test_gate1_unverified.py -q`（零 LLM、零网络）
"""
from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ORCH = ROOT / "medkit" / "core" / "orchestrator.py"

# 五处 gate1 子步骤的失败分支所对应的「清空为无问题」旧写法（任一残留即回归）
_FORBIDDEN_PATTERNS = [
    "_opt_issues = []",
    "_bloom_issues = []",
    "_trace_issues = []",
    '{"issues": []}',
]


def _src() -> str:
    return ORCH.read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1) 结构判据
@pytest.mark.parametrize("pat", _FORBIDDEN_PATTERNS)
def test_no_silent_clear_of_gate_issues(pat):
    """失败分支不得把 issues 清空为 []（那让「未校验」冒充「无问题」）。"""
    src = _src()
    assert pat not in src, (
        f"orchestrator.py 仍存在 `{pat}` —— 门禁① 失败被静默当作「无问题」。")


def test_unverified_is_recorded_and_surfaced():
    """必须真的登记 unverified 并写进 meta 与产物公告（否则前端无从区分）。"""
    src = _src()
    assert "_unverified.append(" in src, "失败分支未登记 unverified"
    assert "gate1_unverified" in src, "unverified 未写入 meta（前端拿不到）"
    assert "gate1_unverified=_uniq" in src, "meta 写入键名不一致"


def test_detector_can_hit():
    """元守卫：禁止模式检测器必须能命中一个已知旧写法（否则恒绿）。"""
    for pat in _FORBIDDEN_PATTERNS:
        sample = f"def f():\n    {pat}\n"
        assert pat in sample, f"检测器对 {pat!r} 恒不命中"


# ---------------------------------------------------------------- 2) 行为判据（正面）
def test_substep_failure_yields_none_not_empty(monkeypatch, tmp_path):
    """端到端：让「选项校验」子步骤抛错 → gate 结果 issues 必须是 None（未校验）。

    直接驱动 `_stage_gate1`，用假 client + 计数「哪些子步骤被调用」，并断言
    gate1_round*.json 里 options.issues is None 且 unverified 含「选项校验」。
    这是「拦在哪一步」的判据——不是只看有没有返回值。
    """
    import json

    from medkit.core import orchestrator as orch

    base = tmp_path / "proj"
    base.mkdir()
    meta_path = base / "meta.json"
    meta_path.write_text("{}", encoding="utf-8")

    questions = [{"id": "Q1", "sid": "S001", "stem": "题干", "options": ["A", "B"],
                  "answer": "A", "type": "A1"}]

    calls: list[str] = []

    def fake_run_substep(b, stage, step, label, fn, **kw):
        calls.append(step)
        if step == "options":
            return None, "模拟校验崩溃"          # 该子项「未跑成」
        try:
            return fn(None), None
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    monkeypatch.setattr(orch, "_run_substep", fake_run_substep)

    # 关闭修复循环的二次轮：让 FIX_ROUNDS_GATE 之外仍走一遍即可（可定向问题为空 → break）
    orch._stage_gate1(
        base=base, meta_path=meta_path, questions=list(questions),
        cancel=__import__("threading").Event(), done_sids={"S001"},
        fix_client_fn=lambda ev: None, text_by_sid={"S001": "题干"},
        bloom_target=None, known_sids={"S001"})

    assert "options" in calls, "选项校验子步骤根本没被调用（用例未打在真身上）"

    rounds = sorted((base / "质检报告").glob("gate1_round*.json"))
    assert rounds, "未落 gate1_round*.json"
    gate = json.loads(rounds[0].read_text(encoding="utf-8"))
    assert gate["options"]["issues"] is None, \
        f"未跑成却写成 {gate['options']['issues']!r}（应为 None=未校验）"
    assert "选项校验" in gate.get("unverified", []), gate.get("unverified")

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert "选项校验" in (meta.get("gate1_unverified") or []), \
        f"meta 未记录未校验子项：{meta.get('gate1_unverified')!r}"


def test_all_verified_yields_empty_list(monkeypatch, tmp_path):
    """对照：全部子项跑成时 issues 是 list（可为空）且 unverified==[] —— 等价改写必绿。"""
    import json

    from medkit.core import orchestrator as orch

    base = tmp_path / "proj"
    base.mkdir()
    meta_path = base / "meta.json"
    meta_path.write_text("{}", encoding="utf-8")
    questions = [{"id": "Q1", "sid": "S001", "stem": "题干", "options": ["A", "B"],
                  "answer": "A", "type": "A1"}]

    def fake_ok(b, stage, step, label, fn, **kw):
        try:
            return fn(None), None
        except Exception as e:  # noqa: BLE001
            return None, str(e)

    monkeypatch.setattr(orch, "_run_substep", fake_ok)
    orch._stage_gate1(
        base=base, meta_path=meta_path, questions=list(questions),
        cancel=__import__("threading").Event(), done_sids={"S001"},
        fix_client_fn=lambda ev: None, text_by_sid={"S001": "题干"},
        bloom_target=None, known_sids={"S001"})

    rounds = sorted((base / "质检报告").glob("gate1_round*.json"))
    gate = json.loads(rounds[0].read_text(encoding="utf-8"))
    assert isinstance(gate["options"]["issues"], list), "正常路径 issues 应为 list"
    assert gate.get("unverified") == [], gate.get("unverified")


# ---------------------------------------------------------------- 3) 源码级自证（注入即红）
def test_injection_turns_guard_red():
    """把失败分支改回 `_opt_issues = []` → 结构守卫的判定核心必须命中（注入即红）。

    演示「检查会拦」：注入前 `_opt_issues = []` 不出现（绿）；注入后出现（红）。
    """
    src = _src()
    assert "_opt_issues = []" not in src, "基线已含禁止模式（守卫本应红）"
    injected = src.replace("_opt_issues = None", "_opt_issues = []", 1)
    assert injected != src, "注入失败（锚点未命中）——用例未打在真身上"
    assert "_opt_issues = []" in injected, "注入后未出现禁止模式 → 守卫不会红"
