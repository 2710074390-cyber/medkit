"""主页面 IA 收敛守卫（Playwright，零 LLM）。

2026-10-20（前端设计重构 S3）：一级页签 5→4（+「题库」随出题工坊开关出现），
原「学习中心」子导航（`#learnnav` pill）、Alt+数字直达、←/→ 循环整体拆除：

- 「概览」（复习总览 + 薄弱点诊断）并入「今天」——`#dash_loop` / `#learn_kp` /
  `#learn_reco` / `#learn_stats` 等 id 原样保留，指标不再两处各算一遍；
- 「大纲管理」迁入「设置 → 进阶功能」的 `details.adv`（见 test_syllabus_view.py）；
- 「错题」升为一级页签，进入即渲染错题助手（聊天为唯一主界面）。

本文件的历史前身守「六视图逐一点击切换」，判据（VIEWS 清单 / `#learnnav`）随功能
删除而失效，故按新 IA 重写：现在守的是**页面清单、直达初始化、窄屏不溢出、主题翻转、
Ctrl+1..5 顺序**。
"""

from __future__ import annotations

# 侧栏顺序即契约（顺序变了 = Ctrl+数字与 title 提示全错位）
TABS = ["start", "study", "learn", "bank", "mine"]

# 直达某页签后，用「占位文案被替换」证明该页的内容加载器真的执行过
_TAB_CONTENT = {
    "start": ("#start_body", "加载中"),
    "study": ("#bank_list", "加载中"),
    "learn": ("#vault_status", "读取中"),
    "bank": ("#proj_list", "加载中"),
    "mine": ("#prompt_list", "加载中"),
}


def test_sidebar_order_and_labels(page, server_url):
    """侧栏就是「今天 / 刷题 / 错题 / 题库 / 设置」，顺序与 title 里的 Ctrl+数字一致。"""
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    tabs = page.evaluate(
        """() => [...document.querySelectorAll('.tabs button[data-tab]')]
                   .map(b => [b.dataset.tab, b.innerText.trim()])"""
    )
    assert [t for t, _ in tabs] == TABS, f"页签顺序应为 {TABS}，实得 {tabs}"
    labels = dict(tabs)
    assert labels["learn"] == "错题", "「错题」应升为一级页签并可一眼认出"
    assert labels["mine"] == "设置", "原「我的」应更名为「设置」"
    # 每个页签 title 里的快捷键提示要与侧栏位置一致（Ctrl+1=今天 … Ctrl+5=设置）
    for i, tab in enumerate(TABS, start=1):
        title = page.locator(f'button[data-tab="{tab}"]').get_attribute("title")
        assert f"Ctrl+{i}" in (title or ""), f"{tab} 的 title 应提示 Ctrl+{i}，实得 {title!r}"


def test_today_tab_owns_merged_overview(page, server_url):
    """原「学习中心 → 概览」的指标块必须整块在「今天」页里（同一批指标不再两处各算）。"""
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    for sel in ("#dash_loop", "#dash_subject", "#learn_stats", "#learn_kp", "#learn_reco"):
        assert page.locator(sel).count() == 1, f"{sel} 应并入「今天」页"


def test_learn_tab_renders_mistakes_view(page, server_url):
    """「错题」页签进入即渲染错题视图（聊天为唯一主界面），无二级子导航。"""
    page.goto(server_url)
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    assert page.locator("#lv-mistakes").evaluate("el => el.classList.contains('show')"), \
        "「错题」页的错题视图应默认激活"
    page.wait_for_selector("#vault_chat", timeout=15000)
    assert page.locator("#learnnav").count() == 0, "子导航已随 IA 收敛删除，不应复活"


def test_learn_tab_narrow_no_overflow(page, server_url):
    """390×844 窄屏：错题页（两栏在窄屏塌成一栏）无横向溢出。"""
    page.goto(server_url)
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    overflow = 999
    for _ in range(8):
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow <= 2:
            break
    assert overflow <= 2, f"错题页横向溢出 {overflow}px（390px 视口）"


def test_ctrl_number_shortcuts_follow_sidebar_order(page, server_url):
    """Ctrl/⌘+1..5 的映射必须与侧栏顺序一致。

    2026-10-20 S3 收敛后侧栏顺序变了（错题升到第 3、题库挪到第 4），
    `TAB_KEYS` 若不跟着改，Ctrl+3 会跳到「题库」——这条就是那个错位的可证伪点。
    """
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    for key, tab in zip("12345", TABS, strict=True):
        page.keyboard.press(f"Control+{key}")
        page.wait_for_selector(f"#tab-{tab}.show", timeout=15000)
        assert page.locator(f"#tab-{tab}.show").count() == 1, f"Ctrl+{key} 应切到「{tab}」"


def test_command_palette_opens_filters_and_runs(page, server_url):
    """Ctrl+K 命令面板（v0.19 S4）：快捷键唤起 → 搜索过滤 → Enter 执行。

    它同时是「键盘优先」的入口，也是可见导航的兜底（侧栏 `#btn_palette` 能点开），
    故两个入口都断言。
    """
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)

    # ① 侧栏按钮是可见兜底：不记快捷键也能打开
    page.click("#btn_palette")
    page.wait_for_selector("#palette_mask", state="visible", timeout=15000)
    assert page.locator(".palette-item").count() >= 5, "面板里应有可执行命令"
    page.keyboard.press("Escape")
    page.wait_for_selector("#palette_mask", state="hidden", timeout=15000)

    # ② 快捷键唤起：Ctrl+K
    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette_mask", state="visible", timeout=15000)

    # ③ 输入即过滤（只留含关键词的命令；`length>0` 防「空集 every 恒真」的假绿）
    #    ⚠️ 只对 `data-kind="cmd"` 断言：面板里还会并排出现**题目**结果（Ctrl+K 搜题），
    #    而题目行显示的是**被截断的题干**——命中词可能落在选项/解析里、或落在截断之后，
    #    于是「每行都含关键词」对题目行不成立（这不是缺陷，是两类行显示的东西不同）。
    page.fill("#palette_q", "刷题")
    page.wait_for_function(
        "() => { const it = [...document.querySelectorAll('.palette-item[data-kind=\"cmd\"]')];"
        " return it.length > 0 && it.every(b => b.innerText.includes('刷题')); }",
        timeout=15000)

    # ④ Enter 执行当前高亮项 → 落到「刷题」页，且面板自动收起
    page.keyboard.press("Enter")
    page.wait_for_selector("#tab-study.show", timeout=15000)
    assert page.locator("#palette_mask").is_hidden(), "执行后面板应收起，不挡住首屏"


def test_palette_searches_questions_and_previews_them(page, server_url):
    """Ctrl+K 的**内容结果**：搜题 → 点开只读预览（题干 / 选项 / 答案 / 出处）。

    为什么单开一条：面板列表从「一维命令数组」变成「命令 + 内容混排」，
    `data-i` 索引、↑↓ 导航、Enter 分发共用同一份数组——这类重构只有真浏览器能验
    （`paletteRun` 里那处 `entry.kind` 分支写错，加载期闸门和 eslint 都看不见）。

    播种与清理都在本用例内：同一个后端会话被整层用例共享，不能留下题库。
    """
    name = "搜题面板用例题库"
    md = ("# 题库\n\n### q1 · A1 型 · 单选 · 理解\n**慢阻肺的诊断**\n"
          "慢阻肺最主要的病因是\n- A. 吸烟\n- B. 感染\n"
          "**✅ 答案：A**\n💡 吸烟是主要危险因素\n")
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    bid = ""
    try:
        bid = page.evaluate(
            """async ([md, name]) => {
          const p = await (await fetch('/api/bank/import/preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: md, fmt: 'md'})})).json();
          const c = await (await fetch('/api/bank/import/commit', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, subject: '内科学_搜题面板', items: p.items})})).json();
          return c.bank_id;
        }""", [md, name])
        page.keyboard.press("Control+k")
        page.wait_for_selector("#palette_mask", state="visible", timeout=15000)
        page.fill("#palette_q", "慢阻肺")
        page.wait_for_selector('.palette-item[data-kind="item"]', timeout=15000)

        # 分段标题必须在场：命令与内容混排时「Enter 是执行动作还是看题」要一眼分得清。
        # 不断言**只有**一段：错题/考点命中时会多出段（同一后端会话被整层共享，
        # 别的用例可能已播下含「慢阻肺」的错题）——这正是三段式该有的行为。
        secs = page.locator(".palette-sec")
        assert secs.count() >= 1, "内容结果必须带分段标题"
        texts = [secs.nth(i).inner_text() for i in range(secs.count())]
        assert any("题目" in t for t in texts), f"缺少「题目」段：{texts}"
        row = page.locator('.palette-item[data-kind="item"]').first
        assert "慢阻肺" in row.inner_text(), f"题目行没显示题干：{row.inner_text()!r}"

        row.click()
        page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
        body = page.locator("#md_body").inner_text()
        assert "慢阻肺最主要的病因是" in body, f"预览缺题干：{body[:120]}"
        assert "吸烟" in body, f"预览缺选项：{body[:120]}"
        assert "答案：A" in body, f"预览缺答案：{body[:120]}"
        # 只读预览不该有「取消」——点了什么也不会取消，反让人以为有未保存的改动
        assert page.locator("#md_cancel").is_hidden(), "只读预览不该出现「取消」按钮"
        page.click("#md_ok")
        page.wait_for_selector("#modal_mask", state="hidden", timeout=15000)
    finally:
        if bid:   # 播种失败时没有 id，不要把 '/undefined' 发给后端
            page.evaluate(
                """async ([bid]) => { await fetch('/api/bank/banks/' + bid, {method: 'DELETE'}); }""",
                [bid])


def test_palette_previews_a_mistake_with_my_own_words(page, server_url):
    """错题段：面板里搜到自己的错题 → 预览里能看到**我写的理解与疑问**。

    为什么值得单开一条：错题的价值不在题干（题干题库里也有），而在用户自己写下的
    那两段。聚合检索若只回题干，等于把"我上次错在哪"这件事又藏起来了。

    不清理播种的错题：本层用隔离的 home（`server_home`）起服务，用例结束后整个
    目录连同文件一起丢弃；而"错题本来就会越攒越多"也是真实形态，后面用例按
    `>= 1 段` 断言，不会被它带偏。
    """
    page.goto(server_url)
    page.wait_for_selector("#tab-start.show", timeout=15000)
    made = page.evaluate(
        """async () => {
          const r = await (await fetch('/api/vault/mistakes', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: '面板用例·慢阻肺的叩诊音',
              my_reasoning: '我原来以为是过清音才对。', doubts: '到底怎么区分？',
              subject: '内科学_面板错题'})})).json();
          return r.path || '';
        }""")
    assert made, "错题没建成（vault 未就绪？）"

    page.keyboard.press("Control+k")
    page.wait_for_selector("#palette_mask", state="visible", timeout=15000)
    page.fill("#palette_q", "叩诊音")
    page.wait_for_selector('.palette-item[data-kind="item"]', timeout=15000)
    secs = page.locator(".palette-sec")
    texts = [secs.nth(i).inner_text() for i in range(secs.count())]
    assert any("错题" in t for t in texts), f"缺少「错题」段：{texts}"
    page.locator('.palette-item[data-kind="item"]').first.click()
    page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
    body = page.locator("#md_body").inner_text()
    assert "我原来以为是过清音才对。" in body, f"预览缺「我的理解」：{body[:200]}"
    assert "到底怎么区分？" in body, f"预览缺「我的疑问」：{body[:200]}"
    page.click("#md_ok")
    page.wait_for_selector("#modal_mask", state="hidden", timeout=15000)


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

    五个页签逐个直达，断言各 tab 的内容加载器均执行（占位文案被替换）且无「脚本异常」toast。
    修复前 initTab 在脚本加载完成前调用 showTab → stopPoll/loadLibrary 未定义 →
    ReferenceError（内容空屏 + 错误 toast）。
    """
    for tab in TABS:
        container, placeholder = _TAB_CONTENT[tab]
        page.goto(f"{server_url}/#{tab}")
        page.wait_for_selector(f"#tab-{tab}.show", timeout=15000)
        page.wait_for_function(
            "args => !document.querySelector(args[0]).innerText.includes(args[1])",
            arg=(container, placeholder), timeout=15000,
        )
        assert page.locator("#toasts .toast.bad").count() == 0, \
            f"#{tab} 直达不应出现错误 toast（脚本异常）"
