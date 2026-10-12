"""错题 / 生成 双模式门（2026-10-10）：进入时先选模式 + 页签按模式过滤（浏览器用例，零 LLM）。

覆盖：
- 首次进入（从未见过模式门、无 hash）→ 模式门出现；
- 选「错题」→ 门关闭、落「练习」、**生成侧页签（题库）被隐藏**、中立页签（开始/我的）保留；
- 选「生成」→ 落「题库」、错题侧页签（练习/刷题/学习中心）被隐藏；
- 模式门**不遮挡侧栏**：门开着也能直接点页签（=「我自己探索」，关掉门且不过滤）；
- 侧栏模式按钮可重新开门切模式。

⚠️ `tests/browser/conftest.py` 的 `page` fixture 预置了 `medkit-mode-seen=1`（既有用例不出现模式门）。
本文件显式清掉它——**这正是「模式门真的会拦/会出现」的证伪点**：
若模式门逻辑被删掉，本文件会红，而其它用例全绿。
"""

from __future__ import annotations

_SIDEBAR_TABS = ["start", "drill", "study", "bank", "learn", "mine"]


def _open_gate(page, server_url: str):
    """清掉「已见过模式门」标记后无 hash 进入 → 门应出现。"""
    page.add_init_script(
        "localStorage.removeItem('medkit-mode-seen');"
        "localStorage.removeItem('medkit-mode');"
    )
    page.goto(server_url)
    page.wait_for_selector("#modegate", state="visible", timeout=15000)


def _visible_tabs(page) -> list[str]:
    return page.evaluate(
        """(tabs) => tabs.filter(t => {
             const b = document.querySelector('button[data-tab="' + t + '"]');
             return !!b && b.style.display !== 'none';
           })""", _SIDEBAR_TABS)


def test_gate_shows_on_first_entry_and_picks_wrong_mode(page, server_url):
    _open_gate(page, server_url)
    page.click('.modecard:has-text("错题")')
    page.wait_for_selector("#modegate", state="hidden", timeout=15000)
    page.wait_for_selector("#tab-drill.show", timeout=15000)

    assert page.evaluate("() => localStorage.getItem('medkit-mode')") == "wrong"
    assert page.locator("#mode_name").inner_text() == "错题"
    tabs = _visible_tabs(page)
    assert "bank" not in tabs, f"错题模式下「题库」（生成侧）应隐藏，实得 {tabs}"
    assert {"drill", "study", "learn", "start", "mine"} <= set(tabs)


def test_gate_picks_generate_mode_and_hides_wrong_side(page, server_url):
    _open_gate(page, server_url)
    page.click('.modecard:has-text("生成")')
    page.wait_for_selector("#modegate", state="hidden", timeout=15000)
    page.wait_for_selector("#tab-bank.show", timeout=15000)

    assert page.evaluate("() => localStorage.getItem('medkit-mode')") == "gen"
    tabs = _visible_tabs(page)
    for t in ("drill", "study", "learn"):
        assert t not in tabs, f"生成模式下「{t}」应隐藏，实得 {tabs}"
    assert {"bank", "start", "mine"} <= set(tabs)


def test_gate_does_not_block_sidebar_and_closes_on_tab_click(page, server_url):
    """门开着也能点侧栏页签（遮罩 pointer-events:none）——点了即「我自己探索」。"""
    _open_gate(page, server_url)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    assert page.locator("#modegate").is_hidden(), "点页签后模式门必须关闭"
    assert _visible_tabs(page) == _SIDEBAR_TABS, "「我自己探索」不得触发页签过滤"


def test_sidebar_mode_button_reopens_gate(page, server_url):
    _open_gate(page, server_url)
    page.click('.modecard:has-text("错题")')
    page.wait_for_selector("#modegate", state="hidden", timeout=15000)
    page.click("#btn_mode")
    page.wait_for_selector("#modegate", state="visible", timeout=15000)
    # 改选「生成」→ 落题库
    page.click('.modecard:has-text("生成")')
    page.wait_for_selector("#tab-bank.show", timeout=15000)
    assert page.evaluate("() => localStorage.getItem('medkit-mode')") == "gen"


def test_wizard_and_gate_are_not_shown_together(page, server_url):
    """P0-1 守卫：真首启时向导与模式门必须串行——向导关闭后才显示模式门，绝不叠加。"""
    page.add_init_script(
        "localStorage.removeItem('medkit-onboarded');"
        "sessionStorage.removeItem('medkit-wz-seen');"
        "localStorage.removeItem('medkit-mode-seen');"
        "localStorage.removeItem('medkit-mode');"
    )
    page.goto(server_url)
    # 真首启：向导先出现
    page.wait_for_selector("#wizard_mask", state="visible", timeout=15000)
    # 向导在场时模式门绝不可见（P0-1 的核心判据）
    gate_visible_while_wizard = page.evaluate(
        "() => { const g = document.getElementById('modegate');"
        " return !!g && g.style.display !== 'none'; }")
    assert not gate_visible_while_wizard, "向导显示期间模式门不得同时可见（P0-1）"
    # 跳过向导后模式门应出现
    page.click("#wz_skip")
    page.wait_for_selector("#modegate", state="visible", timeout=15000)
    page.wait_for_selector("#wizard_mask", state="hidden", timeout=15000)
    assert page.evaluate("() => localStorage.getItem('medkit-onboarded')") == "1"
