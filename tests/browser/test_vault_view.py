"""本地错题（vault）视图的浏览器守卫。

覆盖真实链路：判错后自动流入 vault（后端 `_mirror_to_vault`）→ 索引 → 薄弱项表 →
列表 → 详情 → 独立 UI 输入区写「我的理解/疑问」→ **刷新后仍在**
（证明真的写进了文件，而不是只改了内存）。

2026-10-16（奥卡姆精简第三轮）：「错题本」不再有 `#mv_tabs` 二级切换与导入导出入口
（旧库错题在判错时已自动补写进 vault，无需「一键迁移」）；本视图以聊天为唯一主界面。

Agent 问答本身不在这里测——它需要真 API Key，其行为由 `tests/test_vault.py`
的桩客户端覆盖（那才能断言「没有依据必须说不知道」）。

隔离口径：vault 真身目录由 `server_launcher.py` 重定向到本 session 的隔离 home
（`<home>/vault`），**绝不写真实 ~/文档/MedKit错题本/**。

跨用例污染的对策：session 级 server 是共享的，vault 文件会累积。因此本文件
**不断言全局行数**，一律先用「本科目唯一题干」把列表筛到本用例的 2 条再断言——
否则用例结果会随执行顺序变化（这本身就是假绿的温床）。
"""

from __future__ import annotations


def _goto_vault(page, server_url: str) -> None:
    # v0.19 IA 收敛：错题升为一级页签，进入即渲染错题视图（无二级子导航可点）
    page.goto(f"{server_url}/#learn")
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    # 文件夹全域问的入口必须在场（不需要 API Key；未配 Key 时点它只会给出错误提示）
    page.wait_for_selector("#vault_ask_q", timeout=15000)
    # 今日复习卡片（SM-2）也必须在场
    page.wait_for_selector("#vault_due", timeout=15000)


def _seed_library(page, subject: str, n: int = 2) -> None:
    """往旧错题本塞 n 条带唯一科目标记的错题（题干 = `{subject}-题干{i}`）。"""
    page.evaluate(
        """async ([subject, n]) => {
             for (let i = 0; i < n; i++) {
               await fetch('/api/library/mistakes', {
                 method: 'POST', headers: {'Content-Type': 'application/json'},
                 body: JSON.stringify({source: 'manual', subject,
                   chapter: '呼吸系统', question: subject + '-题干' + i,
                   options: ['甲', '乙'], answer: 'A',
                   analysis: subject + '-解析' + i})});
             }
           }""", [subject, n])


def _reload_vault(page, subject: str) -> None:
    """播种后让前端重新拉 vault 数据（点「重建索引」= 重扫文件夹 + 刷新统计/列表）。

    判错回流会在后端把旧库错题自动补写进 vault 并重建索引；这里等价于用户刷新
    一次页面。等「薄弱项」表里出现本科目，确保统计真的拉到了，再往下断言。
    """
    page.click("#btn_vault_rebuild")
    page.wait_for_function(
        "(s) => document.getElementById('vault_stats').innerText.includes(s)",
        arg=subject, timeout=20000)


def _search(page, text: str) -> None:
    """在 vault 搜索框里输入并触发检索，等列表稳定后再返回。"""
    page.fill("#vault_q", text)
    # 必须用 id：`button:has-text("搜索")` 会命中 6 个元素（服务商标题里也含"搜索"），
    # 首个匹配是隐藏的单选钮 ⇒ 永远点不到（实测 15s 超时的真因）。
    page.click("#btn_vault_q")
    # 注意：`arg` 必须是关键字实参（`wait_for_function(script, *, arg, timeout)`）——
    # 位置传参在 Playwright Python 里直接 TypeError，整条用例会在真正断言前就崩掉。
    page.wait_for_function(
        """(t) => {
             const rows = document.querySelectorAll('.vault-row');
             if (rows.length === 0) {
               // 允许"无命中"的空态，但不能还停在上一批
               const box = document.getElementById('vault_list');
               return !!box && box.innerText.includes('没有命中');
             }
             return rows.length > 0 &&
                    [...rows].every(r => r.innerText.includes(t));
           }""", arg=text, timeout=15000)


def _cleanup(page, subject: str) -> None:
    """删掉本用例塞进旧错题本的记录（vault 文件留在隔离 home，随 session 销毁）。"""
    page.evaluate(
        """async (subject) => {
             const ms = await (await fetch('/api/library/mistakes')).json();
             const bad = (ms.mistakes || []).filter(m => m.subject === subject).map(m => m.id);
             if (bad.length) {
               await fetch('/api/library/mistakes/batch-delete', {
                 method: 'POST', headers: {'Content-Type': 'application/json'},
                 body: JSON.stringify({ids: bad})});
             }
           }""", subject)


_SUBJ_A = "内科学_vault持久"
_SUBJ_B = "外科学_vault检索"


def test_vault_view_persists_user_notes_to_file(page, server_url):
    """判错流入 vault → 薄弱项表 → 写「我的理解/疑问」→ 刷新后仍在。

    最后一步是全用例的**可证伪点**：如果 `vaultEdit()` 只改了内存（没落盘），
    刷新后两个 textarea 会是空的——这条断言就是用来抓那种"看起来成功了"的实现。
    """
    _goto_vault(page, server_url)
    _seed_library(page, _SUBJ_A)
    try:
        _reload_vault(page, _SUBJ_A)
        # 薄弱项表：出现表格且含本用例科目
        page.wait_for_selector("#vault_stats table", timeout=15000)
        assert _SUBJ_A in page.locator("#vault_stats").inner_text(), \
            "薄弱项表应含流入 vault 的科目"

        # 列表：按本科目唯一标记筛到正好 2 条
        _search(page, _SUBJ_A)
        assert page.locator(".vault-row").count() == 2

        # 打开第一条 → 详情里有独立输入区 → 保存
        page.locator(".vault-row").first.click()
        page.wait_for_selector("#vault_detail #vlt_und", timeout=15000)
        page.fill("#vlt_und", "我以为感染是病因。")
        page.fill("#vlt_doubts", "稳定期会用抗菌药吗？")
        page.click('button:has-text("保存理解与疑问")')
        page.wait_for_function(
            "() => { const t = document.getElementById('toasts');"
            " return t && t.innerText.includes('已保存到文件'); }", timeout=15000)

        # 刷新后重新检索：行上应出现"有我的理解"标记，详情里两栏内容原样回来
        page.reload()
        page.wait_for_selector("#tab-learn.show", timeout=15000)
        page.wait_for_selector("#lv-mistakes.show", timeout=15000)
        _search(page, _SUBJ_A)
        first = page.locator(".vault-row").first
        assert "有我的理解" in first.inner_text(), "保存后行上应有「有我的理解」标记"
        first.click()
        page.wait_for_selector("#vlt_und", timeout=15000)
        assert page.input_value("#vlt_und") == "我以为感染是病因。"
        assert page.input_value("#vlt_doubts") == "稳定期会用抗菌药吗？"
    finally:
        _cleanup(page, _SUBJ_A)


def test_vault_search_narrows_and_clears(page, server_url):
    """搜索把列表收窄到命中项，清除后恢复（搜索是真的在过滤，不是摆设）。"""
    _goto_vault(page, server_url)
    _seed_library(page, _SUBJ_B)
    try:
        _reload_vault(page, _SUBJ_B)
        _search(page, _SUBJ_B)
        assert page.locator(".vault-row").count() == 2

        # 更具体的查询 → 只剩 1 条
        _search(page, f"{_SUBJ_B}-题干1")
        assert page.locator(".vault-row").count() == 1

        # 清除 → 搜索框清空、清除按钮收起，恢复成本科目 2 条
        page.click("#btn_vault_q_clear")
        page.wait_for_selector("#btn_vault_q_clear", state="hidden", timeout=15000)
        assert page.input_value("#vault_q") == ""
        _search(page, _SUBJ_B)
        assert page.locator(".vault-row").count() == 2
    finally:
        _cleanup(page, _SUBJ_B)


_SUBJ_C = "儿科学_vault筛选"


def test_vault_stat_row_filters_list(page, server_url):
    """薄弱项表点一行 → 错题列表收窄到该组（把「先学什么」接到「看哪些题」）。"""
    _goto_vault(page, server_url)
    _seed_library(page, _SUBJ_C)
    try:
        _reload_vault(page, _SUBJ_C)
        _search(page, _SUBJ_C)
        assert page.locator(".vault-row").count() == 2

        # 点薄弱项表里本科目那一行 → 列表按该组重筛（先等「筛选」条出现，
        # 不能只等行数——行数在点之前就已满足，会读到未刷新的旧 DOM）
        page.locator("#vault_stats .vault-statrow", has_text=_SUBJ_C).first.click()
        page.wait_for_function(
            "() => document.getElementById('vault_filter_bar').innerText.includes('筛选')",
            timeout=15000)
        page.wait_for_function(
            """(t) => {
                 const rows = document.querySelectorAll('.vault-row');
                 return rows.length === 2 && [...rows].every(r => r.innerText.includes(t));
               }""", arg=_SUBJ_C, timeout=15000)
        assert "筛选" in page.locator("#vault_filter_bar").inner_text()

        # 清除筛选 → 筛选条收起
        page.click("#btn_vault_filter_clear")
        page.wait_for_function(
            "() => document.getElementById('vault_filter_row').style.display === 'none'",
            timeout=15000)
        assert page.locator("#vault_filter_bar").inner_text() == ""
    finally:
        _cleanup(page, _SUBJ_C)


_SUBJ_D = "皮肤科_vault新建"


def test_vault_pane_loads_without_any_extra_action(page, server_url):
    """**进入「错题本」就必须把本地文件面板的数据拉起来**（不许停在「加载中…」）。

    2026-10-16 修的真缺陷：把「本地错题」并成错题本的唯一主界面之后，
    切换路径一度**没人调 `vaultView()`**，于是面板永远停在占位文案，
    必须刷新浏览器才出数据——正是用户反馈的「点了没反应，要刷新才有反应」。
    判据：进入后**不做任何动作**，薄弱项表/列表/今日复习都必须自己出现。
    """
    _goto_vault(page, server_url)
    # 不做任何点击：等三块数据自己渲染出来
    page.wait_for_selector("#vault_status", timeout=15000)
    page.wait_for_function(
        "() => { const t = document.getElementById('vault_status').innerText;"
        " return t && !t.includes('读取中'); }", timeout=15000)
    page.wait_for_function(
        "() => { const t = document.getElementById('vault_list').innerText;"
        " return t && !t.includes('加载中'); }", timeout=15000)
    page.wait_for_function(
        "() => { const t = document.getElementById('vault_due');"
        " return t && !t.innerText.includes('加载中'); }", timeout=15000)
    # 真的到了「已加载」的终态之一，而不是仍停在占位文案
    status = page.locator("#vault_status").inner_text()
    assert ("文件夹" in status) or ("还没有创建" in status), status
    assert "读取中" not in status


def test_vault_pane_loads_on_direct_entry(page, server_url):
    """**直接进入**（hash=#learn）时也必须自己加载，不依赖任何点击。

    这条覆盖上一支用例漏掉的路径：`learn.js` 的进入钩子在**脚本加载期**可能被提前调用，
    那时 `learn-vault.js` 还没求值 ⇒ `window.vaultView` 未定义、加载被静默跳过。
    """
    page.goto(f"{server_url}/#learn")
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.reload()
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    page.wait_for_function(
        "() => { const t = document.getElementById('vault_list').innerText;"
        " return t && !t.includes('加载中'); }", timeout=15000)
    # 真的到了「已加载」的两个终态之一（而不是仍停在占位文案）
    status = page.locator("#vault_status").inner_text()
    assert ("文件夹" in status) or ("还没有创建" in status), status
    assert "加载中" not in page.locator("#vault_due").inner_text()


def test_vault_chat_ui_present_and_clears(page, server_url):
    """错题助手 = 聊天框式：对话区 / 快捷提问 / 输入行 / 手册入口都在场，且能清空对话。"""
    _goto_vault(page, server_url)
    assert page.locator("#vault_chat").count() == 1, "必须有对话区"
    assert page.locator("#vault_chat .chat-empty").count() == 1, "空对话要有引导文案"
    assert page.locator("#vault_chat_quick .chat-chip").count() >= 3, "快捷提问至少 3 条"
    assert page.locator("#btn_vault_handbook").count() == 1, "必须有复习手册入口"
    assert page.locator("#vault_hb_subject").count() == 1, "手册要能按科目生成"
    # 未配 API Key 时提问必须给出**可见的错误**，而不是静默无反应
    page.fill("#vault_ask_q", "我哪章最弱？")
    page.click("#btn_vault_ask_global")
    page.wait_for_function(
        "() => { const b = document.querySelector('#vault_chat .chat-msg.bot');"
        " return !!b && b.innerText.trim().length > 0; }", timeout=20000)
    assert page.locator("#vault_chat .chat-msg.user").count() == 1, "提问要落成用户气泡"
    page.click("#btn_vault_chat_clear")
    page.wait_for_function(
        "() => { const t = document.getElementById('toasts');"
        " return t && t.innerText.includes('已清空对话'); }", timeout=15000)
    assert page.locator("#vault_chat .chat-empty").count() == 1


def test_vault_create_and_review_roundtrip(page, server_url):
    """新建错题直达 vault → 进列表 → 复习评分推进 due（错题→复习→掌握闭环，零 API Key）。"""
    _goto_vault(page, server_url)
    page.click("#btn_vault_new_toggle")
    page.wait_for_selector("#vault_new_form", state="visible", timeout=15000)
    page.fill("#nt_q", "浏览器新建题_vault")
    page.fill("#nt_sub", _SUBJ_D)
    page.fill("#nt_opts", "A. 甲\nB. 乙")
    page.fill("#nt_ans", "A")
    page.click("button:has-text('创建错题文件')")
    page.wait_for_function(
        "() => { const t = document.getElementById('toasts');"
        " return t && t.innerText.includes('已写入错题文件夹'); }", timeout=15000)

    # 落了文件、进了列表
    page.fill("#vault_q", "浏览器新建题_vault")
    page.click("#btn_vault_q")
    page.wait_for_function(
        """(t) => {
             const rows = document.querySelectorAll('.vault-row');
             return rows.length >= 1 && [...rows].some(r => r.innerText.includes(t));
           }""", arg="浏览器新建题_vault", timeout=15000)

    # 复习评分：推进 due（直接打端点，等价于点「记得」）
    res = page.evaluate(
        """async () => {
             const ms = await (await fetch('/api/vault/mistakes?q=' +
               encodeURIComponent('浏览器新建题_vault'))).json();
             const it = (ms.items || [])[0];
             if (!it) return { err: 'not found' };
             const before = it.due || '';
             const g = await (await fetch('/api/vault/review/' + encodeURI(it.path) + '/grade',
               { method: 'POST', headers: { 'Content-Type': 'application/json' },
                 body: JSON.stringify({ quality: 5 }) })).json();
             return { before, after: g.due, status: g.status };
           }""")
    assert not res.get("err"), res
    assert res["after"] and res["after"] > res["before"], \
        f"「记得」后 due 应推到未来：{res}"
