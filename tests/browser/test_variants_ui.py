"""错题助手 UI 2.0 的浏览器守卫（2026-10-29）。

不调 LLM：只把「助手答复 / 变式卡」的消息形状注入 `_vaultChat`，验证渲染层真的
把引用做成可点 chip、把变式做成可作答的卡，而不是只改后端。
"""

from __future__ import annotations


def _goto_vault(page, server_url: str) -> None:
    page.goto(f"{server_url}/#learn")
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    page.wait_for_selector("#vault_chat", timeout=15000)


def test_citation_chip_opens_source_mistake(page, server_url):
    _goto_vault(page, server_url)
    # 真建一条错题，让引用路径真实存在于索引里
    page.evaluate(
        """async () => {
          await fetch('/api/vault/mistakes', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({question: 'UI守卫-引用跳转', subject: 'UI守卫',
              options: ['甲选项', '乙选项'], answer: 'A', analysis: '解析正文'})});
          await vaultLoad();
        }""")
    page.wait_for_function(
        "() => [...document.querySelectorAll('.vault-row')].some(r => r.innerText.includes('UI守卫-引用跳转'))",
        timeout=15000)
    rel = page.evaluate(
        """() => (_vaultItems || []).find(r => r.question === 'UI守卫-引用跳转').path""")
    assert rel
    page.evaluate(
        """(rel) => {
          _vaultChat.length = 0;
          _vaultChat.push({role: 'assistant', text: '这句有依据。', path: rel,
            contextLabel: 'UI守卫-引用跳转',
            cites: [{path: rel, section: '解析'}], grounded: true, toolNames: []});
          vaultChatRender();
        }""", rel)
    page.wait_for_selector(".chat-cite", timeout=15000)
    assert rel not in page.inner_text(".chat-cite")  # chip 只显示文件名，不塞整条路径
    page.click(".chat-cite")
    page.wait_for_selector("#vault_detail", state="visible", timeout=15000)
    assert "UI守卫-引用跳转" in page.inner_text("#vault_detail")


def test_variant_card_can_select_and_show_result(page, server_url):
    _goto_vault(page, server_url)
    page.evaluate(
        """() => {
          _vaultChat.length = 0;
          _vaultChat.push({role: 'assistant', text: '', variant: {
            setId: 'var-test', source_path: 'mistakes/x.md',
            items: [{index: 0, question: '变式题干', options: ['甲选项', '乙选项', '丙选项', '丁选项', '戊选项'],
                     type: 'A1', difficulty: 3, variant_type: 'stem'}],
            answers: {}, result: null}});
          vaultChatRender();
        }""")
    page.wait_for_selector(".variant-card", timeout=15000)
    page.click(".variant-opt[data-opt='B']")
    page.wait_for_selector(".variant-opt.sel", timeout=15000)
    # 注入判分结果（不调真实接口）：验证结果区与逐选项理由真的渲染
    page.evaluate(
        """() => {
          const m = _vaultChat[0];
          m.variant.result = {correct: 1, total: 1, passed: true, threshold: 1,
            items: [{index: 0, question: '变式题干', options: ['甲选项', '乙选项', '丙选项', '丁选项', '戊选项'],
                     user_answer: 'B', answer: 'B', correct: true, analysis: '解析正文',
                     option_rationales: {A: '甲错', B: '乙对', C: '丙错', D: '丁错', E: '戊错'},
                     misconceptions: {}, source_quote: '', difficulty: 3}],
            source: {ok: true}};
          vaultChatRender();
        }""")
    page.wait_for_selector(".variant-sum.ok", timeout=15000)
    assert "迁移通过" in page.inner_text(".variant-sum.ok")
    assert "逐选项理由" in page.inner_text(".variant-card")
    # 390px 窄屏：变式卡不能把整页撑出横向滚动
    page.set_viewport_size({"width": 390, "height": 800})
    page.wait_for_timeout(100)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
