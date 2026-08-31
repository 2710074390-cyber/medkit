"""WP-8：讲解 SSE 流式前端消费（浏览器用例，拦截流式响应，零 LLM）。"""

from __future__ import annotations

import json


def _sse(ev: str, data: dict) -> str:
    return f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def test_explain_stream_live_display(page, server_url):
    route_body = (
        _sse("meta", {"kp_name": "测试点", "subject": "儿科学", "grounded": True, "via_web": False})
        + _sse("delta", {"text": "第一段…"})
        + _sse("delta", {"text": "第二段…"})
        + _sse("done", {"explain": {
            "id": "ex_test", "subject": "儿科学", "kp_name": "测试点",
            "created_at": "2026-01-01T00:00:00", "content": "第一段…第二段…",
            "sources": [], "via_web": False, "grounded": True, "web_materials": [],
        }, "title": "测试点"})
    )

    def handler(route):
        route.fulfill(status=200,
                      headers={"content-type": "text/event-stream"},
                      body=route_body.encode("utf-8"))

    page.route("**/api/library/explain/stream", handler)

    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('button[data-lv="explain"]')
    page.wait_for_selector("#lv-explain.show", timeout=15000)
    page.wait_for_selector("#exp_kp", timeout=15000)

    page.evaluate(
        """() => {
          const sel = document.getElementById('exp_kp');
          const o = document.createElement('option');
          o.value = o.textContent = '测试点';
          sel.appendChild(o); sel.value = '测试点';
        }"""
    )
    page.click("#btn_exp_gen")
    page.wait_for_function(
        "() => document.getElementById('exp_cost')?.innerText.includes('已生成')",
        timeout=15000,
    )
    page.wait_for_function(
        "() => document.getElementById('exp_live')?.innerText.includes('第一段')",
        timeout=15000,
    )
    # 流式帧已被消费：live 区保留全部增量（完成后隐藏但内容可读）
    assert "第二段" in page.locator("#exp_live").inner_text()
    assert page.evaluate("() => typeof consumeSSE") == "function"
