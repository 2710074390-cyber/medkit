"""首启体验（奥卡姆精简 P1/P4，2026-10-13）：进入即用，无任何拦截性弹层；生成链默认不可见。

## 背景

- **P1**：2026-10-10 引入的「错题 / 生成」双模式门与更早的 3 步新手向导**整体移除**。
  体验质检（`docs/体验质检_医学生视角_2026-10-10.md` §二 P0-1）实测：真首启时向导盖住模式门、
  模式门点不动 —— 新用户第一屏就卡住（onboarding anti-pattern）。
- **P4**：出题工坊（生成链）**降级为可选插件、默认关闭**——侧栏不出现「题库」页签，
  学习中心不出现「大纲管理」，「我的」不出现「网络检索」。开关在「我的 → 出题工坊」。

## 本文件是两条性质的证伪点

1. 若有人重新加回 `#modegate` / `#wizard_mask` / `#modebar` 等拦截层，必红；
2. 若有人把出题工坊改成**默认开**（或删掉开关），必红。

> 文件名历史：本文件前身是 `tests/browser/test_mode_gate.py`（2026-10-10 起守「模式门会出现」）。
> v0.15 精简后**判据反向**——守「模式门/向导不再出现」，故随职责改名。
"""

from __future__ import annotations

_SIDEBAR_TABS = ["start", "study", "learn", "mine"]
_GEN_TAB = "bank"
_REMOVED_OVERLAYS = ["#modegate", "#wizard_mask", "#modebar"]


def _gen_off(page) -> None:
    """让本页处于「出题工坊关闭」状态。

    conftest 的 `page` fixture 先 `add_init_script` 把 `medkit-gen-enabled` 设为 `1`
    （生成链自己的用例需要）；Playwright **按添加顺序执行 init script**，
    故此处后加的「删键」脚本会在每次导航时**后**执行 ⇒ 覆盖前一个。
    """
    page.add_init_script("localStorage.removeItem('medkit-gen-enabled');")


def test_first_run_lands_on_start_with_no_overlay(page, server_url):
    """无 hash 首次进入：落在「开始」页，且不存在任何首启遮罩/模式条。"""
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    for sel in _REMOVED_OVERLAYS:
        assert page.locator(sel).count() == 0, (
            f"首启不应存在 {sel} —— 奥卡姆精简 P1 已移除首启门与模式条")


def test_all_sidebar_tabs_visible_and_clickable_on_first_run(page, server_url):
    """首启时默认页签全部可见；逐个点击都能切换（无遮罩吃点击）。"""
    _gen_off(page)
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    visible = page.evaluate(
        """(tabs) => tabs.filter(t => {
             const b = document.querySelector('button[data-tab="' + t + '"]');
             return !!b && b.style.display !== 'none';
           })""", [*_SIDEBAR_TABS, _GEN_TAB])
    assert set(visible) == set(_SIDEBAR_TABS), (
        f"首启应显示 {_SIDEBAR_TABS}（不含出题工坊的「题库」），实得 {visible}")
    for tab in _SIDEBAR_TABS:
        page.click(f'button[data-tab="{tab}"]')
        page.wait_for_selector(f"#tab-{tab}.show", timeout=15000)


def test_generation_workshop_is_hidden_by_default(page, server_url):
    """P4 可证伪点：出题工坊默认关闭；打开开关后「题库」页签与相关入口出现。

    - 关闭时：侧栏无「题库」；`#bank` hash 直达被拒并落回「开始」；「大纲管理」/「网络检索」隐藏。
    - 打开后：「题库」页签可见且可进入。
    """
    _gen_off(page)
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    assert page.locator(f'button[data-tab="{_GEN_TAB}"]').is_hidden(), "默认不应显示「题库」页签"
    assert page.locator("#lv-syllabus").is_hidden(), "默认不应显示「大纲管理」"
    assert page.locator("#ws_card").is_hidden(), "默认不应显示「网络检索」卡片"

    # hash 直达 #bank 必须被拒（否则「默认关」只是视觉隐藏）
    page.goto(f"{server_url}/#bank")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    assert page.locator("#tab-bank.show").count() == 0, "工坊关闭时 #bank 不应可进入"

    # 打开开关 → 「题库」出现（不再导航，避免 init script 把开关又清掉）
    page.goto(f"{server_url}/#mine")
    page.wait_for_selector("#tab-mine.show", timeout=15000)
    # v0.19：「启用出题工坊」收进「设置 → 进阶功能」折叠区（工坊关闭时默认收起）
    page.evaluate("() => { const d = document.querySelector('details.adv'); if (d) d.open = true; }")
    page.locator("#t_gen").wait_for(state="visible", timeout=15000)
    page.check("#t_gen")
    page.wait_for_selector(f'button[data-tab="{_GEN_TAB}"]', state="visible", timeout=15000)
    assert page.locator("#ws_card").is_visible(), "开启后「网络检索」应出现"
    page.click(f'button[data-tab="{_GEN_TAB}"]')
    page.wait_for_selector("#tab-bank.show", timeout=15000)


def test_first_run_does_not_depend_on_legacy_onboarding_state(page, server_url):
    """首启不依赖任何 localStorage 前置状态（旧 medkit-onboarded / medkit-mode-seen 已失效）。"""
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.evaluate("() => { try { localStorage.clear(); sessionStorage.clear(); } catch (e) {} }")
    page.reload()
    page.wait_for_selector("#tab-start.show", timeout=15000)
    for sel in _REMOVED_OVERLAYS:
        assert page.locator(sel).count() == 0, f"清空 localStorage 后仍不应出现 {sel}"
