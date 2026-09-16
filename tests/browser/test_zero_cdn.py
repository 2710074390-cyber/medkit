"""V-14（运行时闸门）：**零 CDN** —— 应用全程不得发出任何外部网络请求。

与 `tests/test_v14_zero_cdn.py`（静态正则扫描源码）互补：静态扫描看不见**动态注入**的外部资源
（`document.createElement("script")` 后赋 CDN 地址、`import("https://…")` 拼接、第三方 SDK
运行时自己拉资源等）。本文件用 Playwright 拦截**真实请求**，只要出现非回环主机即失败。

为什么值得单独立闸：`docs/engineering/borrow-rules.md` §1/§2 与 R6 判据 P7 都写明「零 CDN、
零构建、产物可离线打开」，但此前**没有任何自动检查**——后人加一行 CDN 引用不会有任何反馈，
而它会让「断网可用」的承诺静默失效。
"""

from __future__ import annotations

import urllib.parse

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _watch(page) -> list[str]:
    """挂上请求监听，返回「外部请求」收集列表。"""
    external: list[str] = []

    def _on_request(req):
        host = urllib.parse.urlparse(req.url).hostname or ""
        if host and host not in LOCAL_HOSTS:
            external.append(f"{req.method} {req.url}")

    page.on("request", _on_request)
    return external


def _walk_app(page, server_url: str) -> None:
    """走一遍主链路：首屏 + 各主 tab + 学习中心各子视图（触发懒加载/按需渲染）。"""
    page.goto(server_url)
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    for tab in ("start", "learn", "study", "mine"):
        btn = page.locator(f'button[data-tab="{tab}"]')
        if btn.count():
            btn.first.click()
            page.wait_for_timeout(250)
    # 学习中心子视图（每个都可能触发新的渲染分支）
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    pills = page.locator("#learnnav button")
    for i in range(min(pills.count(), 6)):
        pills.nth(i).click()
        page.wait_for_timeout(250)


def test_app_makes_no_external_requests(page, server_url):
    """主链路全程：所有网络请求的主机都必须是回环地址。"""
    external = _watch(page)
    _walk_app(page, server_url)
    page.wait_for_timeout(600)
    assert not external, "应用发出了外部网络请求（违反「零 CDN / 可离线」）：\n  " + "\n  ".join(external)


def test_product_page_makes_no_external_requests(page, server_url):
    """产物页（题库/押题卷）也必须零外部请求——学生常断网打开它。

    这里用「渲染产物 HTML 的脚本模板是否只引用本地资源」间接覆盖：
    真正打开产物需要先跑出题管线（依赖 LLM），故改为断言产物渲染模块产出的 HTML
    不含外部资源引用（与静态闸门同口径，但针对产物路径再钉一次）。
    """
    from medkit.render import qbank_html, review_html

    q = [{"id": "q1", "type": "A1", "subject": "内科学", "question": "题干？",
          "options": ["A", "B", "C", "D", "E"], "answer": "A", "analysis": "解析",
          "bloom": "理解", "know_tags": ["kp"], "miss_count": 1}]
    pages = [
        qbank_html.export_html(q, "题库"),
        qbank_html.export_paper_html(q, "押题卷"),
        review_html.review_to_html("# 复习手册\n\n| 知识点 | 到期 |\n|---|---|\n| kp | 2026-01-01 |\n"),
    ]
    for html in pages:
        for token in ("http://", "https://", "//cdn", "@import"):
            assert token not in html, f"产物页含外部引用 {token!r}（断网打开会残缺）"
