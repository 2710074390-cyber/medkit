"""WP-6：检索设置页（可信来源开关/自定义域名）浏览器用例 + manual 测试文案（零网络）。"""

from __future__ import annotations


def _goto_mine(page, server_url: str) -> None:
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="mine"]')
    page.wait_for_selector("#tab-mine.show", timeout=15000)
    page.wait_for_selector("#ws_backend", timeout=15000)


def _save_ws(page, trusted: bool, domains: str) -> None:
    if trusted:
        page.check("#t_web_trusted")
    else:
        page.uncheck("#t_web_trusted")
    page.fill("#ws_trusted_domains", domains)
    page.click("#btn_ws_save")
    page.wait_for_function(
        "() => document.querySelector('#toasts')?.innerText.includes('网络检索设置已保存')",
        timeout=15000,
    )


def test_search_settings_trusted_roundtrip(page, server_url):
    _goto_mine(page, server_url)
    try:
        _save_ws(page, True, "who.int, nhc.gov.cn")
        page.reload()
        page.wait_for_selector("#tab-mine.show", timeout=15000)
        # 设置表单经 GET /api/config 异步回填（未返回前是默认态）——等开关翻转为已保存值再断言，防时序竞态
        page.wait_for_function(
            "() => document.getElementById('t_web_trusted')?.checked === true",
            timeout=15000,
        )
        val = page.input_value("#ws_trusted_domains")
        assert "who.int" in val and "nhc.gov.cn" in val
    finally:
        _save_ws(page, False, "")


def test_search_settings_manual_test_message(page, server_url):
    """manual 后端点「测试」→ 给出「手动粘贴」的用法提示。

    ⚠️ 原版只有一个 `wait_for_function`（超时即失败）——那是**隐式断言**：
    读代码看不出在断言什么，也区分不出「提示出现了」与「等待超时了」。
    现补一条显式断言把期望结果写出来（2026-10-01 R30）。
    """
    _goto_mine(page, server_url)
    page.select_option("#ws_backend", "manual")
    page.click("#btn_ws_test")
    page.wait_for_function(
        "() => document.getElementById('ws_test_result')?.innerText.includes('手动粘贴')",
        timeout=15000,
    )
    assert "手动粘贴" in page.inner_text("#ws_test_result"), \
        "manual 后端应提示「手动粘贴」的用法（而非空结果或报错）"
