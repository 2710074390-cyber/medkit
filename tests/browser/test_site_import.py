"""WP-11：外部站点做题数据导入（浏览器用例，拦截 import-export，零 LLM）。"""

from __future__ import annotations

import json


def test_site_import_button_and_flow(page, server_url):
    def handler(route):
        route.fulfill(status=200, headers={"content-type": "application/json"},
                      body=json.dumps({"ok": True, "added": 1, "updated": 0,
                                       "skipped": 0, "errors": []}))

    page.route("**/api/library/mistakes/import-export", handler)
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('button[data-lv="mistakes"]')
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    assert page.locator("#btn_mk_site").is_visible()

    payload = json.dumps({"items": [{"subject": "儿科学", "chapter": "呼吸",
                                     "question": "测试题", "answer": "A"}]}).encode("utf-8")
    page.set_input_files("#mk_site_file", files=[{
        "name": "site.json", "mimeType": "application/json", "buffer": payload,
    }])
    page.wait_for_function(
        "() => document.querySelector('#toasts')?.innerText.includes('站点数据导入')",
        timeout=15000,
    )
