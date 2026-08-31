"""WP-2：进度 stepper 渲染（准备中 / 子步骤 / 百分比）浏览器用例（零 LLM）。

前端纯函数验证：不需要真实管线数据，直接调用全局 renderStepper 断言 HTML 输出。
"""

from __future__ import annotations


def test_render_stepper_preparing_and_substep(page, server_url):
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)

    html = page.evaluate(
        """() => renderStepper("gate1", {stage:"gate1", pct:0, done:0, total:3,
            detail:"", sub:"选项校验", sub_done:0, sub_total:4})"""
    )
    assert "准备中" in html, "pct=0 时不应空白，应显示准备中"
    assert "选项校验 0/4" in html, "应显示子步骤计数"

    html2 = page.evaluate(
        """() => renderStepper("generating", {stage:"generating", pct:50, done:1, total:2,
            detail:"切片 1/2", sub:"切片出题", sub_done:1, sub_total:2})"""
    )
    assert "切片 1/2" in html2, "detail 应展示"
    assert "切片出题 1/2" in html2, "sub 应展示"
    assert "50%" in html2, "百分比应展示"

    html3 = page.evaluate("""() => renderStepper("done", {stage:"done", pct:100, done:1, total:1})""")
    assert "已完成" in html3, "done 态应展示已完成"
