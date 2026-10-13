"""本地错题（vault）视图的浏览器守卫。

覆盖真实链路：旧错题 → 一键迁移成 md → 索引 → 薄弱项表 → 列表 → 详情 →
独立 UI 输入区写「我的理解/疑问」→ **刷新后仍在**（证明真的写进了文件，而不是只改了内存）。

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
    # 「一键迁移」带原生 confirm 确认框；Playwright 默认自动**关闭**对话框（confirm 返回 false），
    # 不接管它的话迁移会被静默取消、toast 永不出现（实测这里就是 20s 超时的真因）。
    page.on("dialog", lambda d: d.accept())
    page.goto(f"{server_url}/#learn")
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('#learnnav button[data-lv="vault"]')
    page.wait_for_selector("#lv-vault.show", timeout=15000)
    # 文件夹全域问的入口必须在场（不需要 API Key；未配 Key 时点它只会给出错误提示）
    page.wait_for_selector("#vault_ask_q", timeout=15000)


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


def _migrate(page) -> None:
    page.click("#btn_vault_migrate")
    page.wait_for_function(
        "() => { const t = document.getElementById('toasts');"
        " return t && t.innerText.includes('迁移完成'); }", timeout=20000)


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
    """迁移 → 薄弱项表 → 写「我的理解/疑问」→ 刷新后仍在。

    最后一步是全用例的**可证伪点**：如果 `vaultEdit()` 只改了内存（没落盘），
    刷新后两个 textarea 会是空的——这条断言就是用来抓那种"看起来成功了"的实现。
    """
    _goto_vault(page, server_url)
    _seed_library(page, _SUBJ_A)
    try:
        _migrate(page)

        # 薄弱项表：出现表格且含本用例科目
        page.wait_for_selector("#vault_stats table", timeout=15000)
        assert _SUBJ_A in page.locator("#vault_stats").inner_text(), \
            "薄弱项表应含迁移进来的科目"

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
        page.click('#learnnav button[data-lv="vault"]')
        page.wait_for_selector("#lv-vault.show", timeout=15000)
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
        _migrate(page)
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
