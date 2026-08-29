"""R3S-04：押题卷作答状态模型浏览器用例（Playwright，零 LLM）。

覆盖（对应补充轮验收清单）：
- C-02  X 型取消全部勾选 → 旧答案删除（已答计数回落）；
- C-03/C-04  案例组两子题同错 → 同步两道（按 question_id 去重，不误并/不漏存）；
- C-07  审核改题（题集指纹变化）→ 重开提示「卷面已更新」并清档（不静默套旧答案）；
- C-18  判分错题池在「重新作答」后清空（重做全对不再回流第一轮错题）。
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path


def _mk_project(server_home: Path, pid: str, questions: list[dict],
                subject: str = "儿科学") -> None:
    """向隔离服务器的 projects 目录播种一个项目（切片 + meta + 最终题库）。

    测试用独立 subject：避免错题同步污染学习库，串扰同服务器的其它浏览器用例。
    """
    base = Path(server_home) / "projects" / pid
    (base / "最终产物").mkdir(parents=True)
    (base / "slices.json").write_text(json.dumps(
        [{"sid": "S001", "title": "第一章 生长发育", "text": "教材内容。", "role": "textbook"}],
        ensure_ascii=False), encoding="utf-8")
    meta = {"pid": pid, "subject": subject, "exam": "期末", "target": len(questions),
            "toggles": {"qbank": True, "paper": True, "review": False},
            "stage": "done", "created": "2026-08-29T00:00:00"}
    (base / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    (base / "最终产物" / "questions_final.json").write_text(
        json.dumps(questions, ensure_ascii=False), encoding="utf-8")


def _render_paper(server_url: str, pid: str) -> None:
    req = urllib.request.Request(
        f"{server_url}/api/projects/{pid}/rerender",
        data=json.dumps({"what": "paper"}).encode(),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        assert r.status == 200, r.read().decode()


def _paper_url(server_url: str, pid: str) -> str:
    return f"{server_url}/api/projects/{pid}/files/{urllib.parse.quote('押题卷.html')}"


def _state_key(pid: str) -> str:
    return f"medkit-paper-{pid}"


def _mine(page, pid: str) -> list[dict]:
    return page.evaluate(
        """async (pid) => {
          const r = await fetch('/api/library/mistakes');
          const list = (await r.json()).mistakes || [];
          return list.filter(m => m.source_ref && m.source_ref.pid === pid);
        }""",
        pid,
    )


def test_paper_x_uncheck_removes_answer(page, server_url, server_home):
    """C-02：X 型勾选再全部取消 → 旧答案删除、已答计数回落。"""
    pid = "paper_x_test"
    _mk_project(server_home, pid, [
        {"id": "Q001", "sid": "S001", "type": "X", "bloom": "理解", "subtopic": "章",
         "question": "多选演示？", "options": ["A", "B", "C", "D"],
         "answer": "AC", "analysis": "解析。"}], subject="儿科X测试")
    _render_paper(server_url, pid)
    page.goto(_paper_url(server_url, pid))
    page.wait_for_selector("#quiz .q", timeout=15000)

    page.locator('input[name="q0"][value="B"]').check()
    page.wait_for_timeout(350)
    st1 = page.evaluate("k => JSON.parse(localStorage.getItem(k))", _state_key(pid))
    assert st1["answers"].get("Q001") == "B", f"勾选后应按 id 记录答案：{st1}"

    page.locator('input[name="q0"][value="B"]').uncheck()
    page.wait_for_timeout(350)
    st2 = page.evaluate("k => JSON.parse(localStorage.getItem(k))", _state_key(pid))
    assert "Q001" not in st2["answers"], f"取消全部勾选后旧答案应删除：{st2}"
    assert "0 / 1" in page.locator("#asw").inner_text(), "已答计数应回落为 0 / 1"


def test_paper_case_subquestions_sync_individually(page, server_url, server_home):
    """C-03/C-04：案例组两子题同错 → 同步两道（question_id 去重 + case_stem 入库）。"""
    pid = "paper_case_test"
    stem = "患儿男，5 岁，发热咳嗽三天。"
    qs = [
        {"id": "Q001", "sid": "S001", "type": "A3", "bloom": "应用", "subtopic": "病例",
         "question": "首先考虑的诊断是？", "options": ["A", "B", "C", "D"],
         "answer": "A", "analysis": "解析 1。", "case_id": "C1", "case_stem": stem,
         "case_order": 1, "group_kind": "case"},
        {"id": "Q002", "sid": "S001", "type": "A3", "bloom": "应用", "subtopic": "病例",
         "question": "首先考虑的诊断是？", "options": ["A", "B", "C", "D"],
         "answer": "B", "analysis": "解析 2。", "case_id": "C1", "case_stem": stem,
         "case_order": 2, "group_kind": "case"},
    ]
    _mk_project(server_home, pid, qs, subject="儿科案例测试")
    _render_paper(server_url, pid)
    page.goto(_paper_url(server_url, pid))
    page.wait_for_selector("#quiz .q", timeout=15000)

    # 押题卷抽样可能重排题序 → 按 id 动态定位（不假设 q0=Q001）
    i0, i1 = page.evaluate(
        "() => { const a = QUESTIONS.map(q => String(q.id || '')); return [a.indexOf('Q001'), a.indexOf('Q002')]; }")
    assert i0 >= 0 and i1 >= 0
    # 两子题都答 C → 都错 → 自动回流两道
    page.locator(f'input[name="q{i0}"][value="C"]').check()
    page.locator(f'input[name="q{i1}"][value="C"]').check()
    # 等作答落盘后再判分（避免输入事件与判分读状态的竞态）
    page.wait_for_function(
        "k => { const s = JSON.parse(localStorage.getItem(k) || 'null'); return s && s.answers.Q001 && s.answers.Q002; }",
        arg=_state_key(pid),
    )
    page.locator("#quiz .act").click()
    page.wait_for_function(
        """async (pid) => {
          const r = await fetch('/api/library/mistakes');
          const list = (await r.json()).mistakes || [];
          return list.filter(m => m.source_ref && m.source_ref.pid === pid).length >= 2;
        }""",
        arg=pid, timeout=15000,
    )
    mine = _mine(page, pid)
    qids = {m["source_ref"]["question_id"] for m in mine}
    assert qids == {"Q001", "Q002"}, f"两子题应各同步一条（按 id 去重不误并）：{qids}"
    assert all(m.get("case_stem") == stem for m in mine), "案例共享题干应入库（详情可读）"


def test_paper_stale_state_invalidated(page, server_url, server_home):
    """C-07：审核改题（指纹变化）→ 重开清档 + 提示，不静默套旧答案。"""
    pid = "paper_fp_test"
    qs = [{"id": "Q001", "sid": "S001", "type": "A1", "bloom": "记忆", "subtopic": "章",
           "question": "单选题干？", "options": ["A", "B", "C", "D"],
           "answer": "A", "analysis": "解析。"}]
    _mk_project(server_home, pid, qs, subject="儿科指纹测试")
    _render_paper(server_url, pid)
    page.goto(_paper_url(server_url, pid))
    page.wait_for_selector("#quiz .q", timeout=15000)

    page.locator('input[name="q0"][value="B"]').check()
    page.wait_for_timeout(350)
    st1 = page.evaluate("k => JSON.parse(localStorage.getItem(k))", _state_key(pid))
    assert st1["answers"].get("Q001") == "B"

    # 模拟审核台改题：题目 id 变化 → 重新渲染押题卷 → 指纹失效
    qs2 = [dict(qs[0], id="Q999", question="改后题干？")]
    (Path(server_home) / "projects" / pid / "最终产物" / "questions_final.json").write_text(
        json.dumps(qs2, ensure_ascii=False), encoding="utf-8")
    _render_paper(server_url, pid)
    page.reload()
    page.wait_for_selector("#quiz .q", timeout=15000)
    page.wait_for_selector(".banner.bad", timeout=15000)
    assert "卷面已更新" in page.locator(".banner.bad").inner_text(), "应提示旧作答失效"
    assert page.evaluate("k => localStorage.getItem(k)", _state_key(pid)) is None, \
        "指纹不匹配应清档（不静默映射旧答案）"


def test_paper_reset_clears_wrong_pool(page, server_url, server_home):
    """C-18：判分后「重新作答」清空错题池（重做全对不再回流第一轮错题）。"""
    pid = "paper_reset_test"
    _mk_project(server_home, pid, [
        {"id": "Q001", "sid": "S001", "type": "A1", "bloom": "记忆", "subtopic": "章",
         "question": "题 1？", "options": ["A", "B", "C", "D"], "answer": "A",
         "analysis": "解析 1。"},
        {"id": "Q002", "sid": "S001", "type": "A1", "bloom": "记忆", "subtopic": "章",
         "question": "题 2？", "options": ["A", "B", "C", "D"], "answer": "B",
         "analysis": "解析 2。"},
    ], subject="儿科重置测试")
    _render_paper(server_url, pid)
    page.goto(_paper_url(server_url, pid))
    page.wait_for_selector("#quiz .q", timeout=15000)

    # 押题卷抽样可能重排题序 → 按 id 动态定位（不假设 q0=Q001）
    i0, i1 = page.evaluate(
        "() => { const a = QUESTIONS.map(q => String(q.id || '')); return [a.indexOf('Q001'), a.indexOf('Q002')]; }")
    assert i0 >= 0 and i1 >= 0
    page.locator(f'input[name="q{i0}"][value="C"]').check()
    page.locator(f'input[name="q{i1}"][value="B"]').check()
    page.wait_for_function(
        "k => { const s = JSON.parse(localStorage.getItem(k) || 'null'); return s && s.answers.Q001 === 'C' && s.answers.Q002 === 'B'; }",
        arg=_state_key(pid),
    )
    page.locator("#quiz .act").click()
    page.wait_for_function(
        "() => { const sc = document.querySelector('#res .score'); return sc && sc.innerText.includes('1/2') && Object.keys(WRONG_POOL).length === 1; }",
    )

    page.locator("#res button", has_text="重新作答").click()
    page.wait_for_function(
        "() => document.querySelectorAll('#quiz .q').length === 2 && Object.keys(WRONG_POOL).length === 0",
    )
