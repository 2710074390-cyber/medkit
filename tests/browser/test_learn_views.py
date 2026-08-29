"""IMP-05：学习中心六视图 + 主题 + 窄屏浏览器用例（Playwright，零 LLM）。

覆盖：
- 六视图（概览 / 错题本 / 讲解产物 / 提问学习 / 复习计划 / 大纲覆盖）切换：点击子导航，
  断言对应视图被激活（aria-selected 翻转 + 视图获得 .show 激活态）；
- 刷新记忆上次视图（sessionStorage 保留 medkit-learn-view）；
- 390×844 窄屏：六个视图均无横向溢出（documentElement.scrollWidth 不超视口 + 容忍）；
- 明暗主题切换：data-theme 属性翻转（light<->dark）且页面内容仍渲染。

注：历史缺陷（lv-explain 缺失闭合致 tutor/review/syllabus 视图被嵌套隐藏）已在
commit 7555b77 修复；本文件同时断言 aria-selected 与可见性双重口径。
"""

from __future__ import annotations

VIEWS = ["overview", "mistakes", "explain", "tutor", "review", "syllabus"]


def _open_learn(page, server_url: str):
    page.goto(server_url)
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)


def _assert_active(page, name: str):
    """断言某子导航对应的视图被激活：对应按钮 aria-selected=true 且视图带 .show。"""
    btn = page.locator(f'#learnnav button[data-lv="{name}"]')
    btn.wait_for(state="visible", timeout=15000)
    assert btn.get_attribute("aria-selected") == "true", f"{name}: aria-selected 应为 true"
    view = page.locator(f"#lv-{name}")
    assert view.evaluate("el => el.classList.contains('show')"), f"{name}: 视图未激活(.show)"


def test_learn_center_six_views_switch(page, server_url):
    """六视图逐一点击后均被激活（aria-selected 翻转为 true + 视图 .show）。"""
    _open_learn(page, server_url)
    assert page.locator("#lv-overview").evaluate("el => el.classList.contains('show')"), \
        "默认应停留在概览"
    for name in VIEWS:
        page.click(f'#learnnav button[data-lv="{name}"]')
        _assert_active(page, name)


def test_learn_view_remembered_after_reload(page, server_url):
    """刷新后 sessionStorage 记住上次视图（错题本）。"""
    _open_learn(page, server_url)
    page.click('#learnnav button[data-lv="mistakes"]')
    _assert_active(page, "mistakes")
    page.reload()
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    # 刷新后 URL 仍带 #learn；initLearnView 按 sessionStorage 恢复 mistakes
    _assert_active(page, "mistakes")


def test_learn_views_narrow_no_overflow(page, server_url):
    """390×844 窄屏：六个视图激活后均无横向溢出。"""
    page.goto(server_url)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    for name in VIEWS:
        page.click(f'#learnnav button[data-lv="{name}"]')
        # 视图内异步加载会在渲染中途瞬时加宽，这里轮询到稳定后再测，避免时序假阳性。
        overflow = 999
        for _ in range(6):
            page.wait_for_timeout(300)
            overflow = page.evaluate(
                "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            if overflow <= 2:
                break
        assert overflow <= 2, f"视图 {name} 横向溢出 {overflow}px（390px 视口）"


def test_learn_views_breakpoints_no_overflow(page, server_url):
    """IMP-11：断点收敛（480/640/860 三档）——四视口下六视图均无横向溢出。"""
    page.goto(server_url)
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    for w, h in ((860, 900), (820, 900), (640, 900), (480, 900)):
        page.set_viewport_size({"width": w, "height": h})
        for name in VIEWS:
            page.click(f'#learnnav button[data-lv="{name}"]')
            page.locator(f"#lv-{name}").wait_for(state="visible", timeout=15000)
            overflow = 999
            for _ in range(6):
                page.wait_for_timeout(300)
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
                )
                if overflow <= 2:
                    break
            assert overflow <= 2, f"{w}px × 视图 {name} 溢出 {overflow}px"


def test_learn_views_shortcut_keys(page, server_url):
    """IMP-12①：Alt+1..6 直达子导航；IMP-08：←/→ 方向键循环切换（APG tab 模式）。"""
    _open_learn(page, server_url)
    page.keyboard.press("Alt+6")
    assert page.locator('#learnnav button[data-lv="syllabus"]').get_attribute("aria-selected") == "true"
    assert page.locator("#lv-syllabus").is_visible()
    page.keyboard.press("Alt+1")
    assert page.locator("#lv-overview").is_visible()
    # ←/→：聚焦当前 pill（overview）→ 右键 → mistakes
    page.locator('#learnnav button[data-lv="overview"]').focus()
    page.keyboard.press("ArrowRight")
    assert page.locator("#lv-mistakes").is_visible(), "→ 应切到错题本"
    page.keyboard.press("ArrowLeft")
    assert page.locator("#lv-overview").is_visible(), "← 应切回概览"


def test_theme_toggle_flips_data_theme(page, server_url):
    """点击主题按钮：data-theme 在 light/dark 间翻转，且内容仍渲染。"""
    page.goto(server_url)
    page.wait_for_selector("#btn_theme", timeout=15000)
    before = page.locator("html").get_attribute("data-theme")
    assert before in ("light", "dark"), f"初始主题异常: {before!r}"
    page.click("#btn_theme")
    after = page.locator("html").get_attribute("data-theme")
    assert after in ("light", "dark"), f"切换后主题异常: {after!r}"
    assert after != before, "data-theme 未翻转"
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    assert page.locator("#btn_theme").is_enabled()


def test_hash_direct_navigation_initializes_tab(page, server_url):
    """H-1 回归：带 hash 直达 URL 时 tab 内容必须初始化（initTab 推迟到 DOMContentLoaded）。

    直达 #learn：学习中心概览由 loadLibrary 渲染（learn.js 后于 app.js 加载）；
    直达 #mine：项目列表由 loadProjects 渲染（review-desk.js 最后加载）。
    修复前 initTab 在脚本加载完成前调用 showTab → stopPoll/loadLibrary 未定义 →
    ReferenceError（内容空屏 + 「脚本异常」toast）。
    """
    cases = (("learn", "#dash_loop", "汇总中"), ("mine", "#proj_list", "加载中"))
    for tab, container, placeholder in cases:
        page.goto(f"{server_url}/#{tab}")
        page.wait_for_selector(f"#tab-{tab}.show", timeout=15000)
        page.wait_for_function(
            "args => !document.querySelector(args[0]).innerText.includes(args[1])",
            arg=(container, placeholder), timeout=15000,
        )
        assert page.locator("#toasts .toast.bad").count() == 0, \
            f"#{tab} 直达不应出现错误 toast（脚本异常）"
