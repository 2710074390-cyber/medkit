"""WP-12：纯净安装包「载入示例」按钮降级（浏览器用例，拦截 /api/sample）。"""

from __future__ import annotations


def test_sample_button_degraded_when_unavailable(page, server_url):
    def handler(route):
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body='{"sample": false, "available": false, "error": "示例仅开发版可用"}')

    page.route("**/api/sample", handler)
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.wait_for_function(
        "() => document.getElementById('btn_sample')?.innerText.includes('示例仅开发版可用')",
        timeout=15000,
    )
    assert page.locator("#btn_sample").is_disabled()
