"""WP-9：富文本渲染（浏览器侧直接调用 mdRender/expMd，零 CDN）。"""

from __future__ import annotations


def test_md_render_rich_and_xss(page, server_url):
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    html = page.evaluate("() => mdRender('# 标题\\n\\n| A | B |\\n|---|---|\\n| 1 | 2 |\\n\\n**bold**')")
    assert "<table>" in html and "<h2>" in html and "<b>bold</b>" in html
    xss = page.evaluate(
        "() => mdRender('<img src=x onerror=alert(1)><script>alert(1)</script>**ok**')"
    )
    assert "<script>" not in xss and "<img" not in xss and "<b>ok</b>" in xss
    # 2026-10-15（奥卡姆精简 T2）：`expMd`（讲解产物专用渲染）随讲解域删除，
    # 全仓零引用——mdRender 已是唯一渲染入口，探针改为断言「它没被删掉」。
    assert page.evaluate("() => typeof mdRender") == "function"
