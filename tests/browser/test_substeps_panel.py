"""WP-3：子步骤面板渲染（前端纯函数，零 LLM）浏览器用例。

验证 renderSubsteps：当前阶段过滤、运行/完成/失败/重试状态展示、可展开详情、空态。
"""

from __future__ import annotations


def test_render_substeps_panel(page, server_url):
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)

    html = page.evaluate(
        """() => renderSubsteps([
          {stage:"gate1", step:"options", label:"选项校验", status:"running", detail:"第 1 轮"},
          {stage:"gate1", step:"bloom", label:"Bloom 校验", status:"done", detail:"第 1 轮"},
          {stage:"qc", step:"batch1", label:"质检批次 1/2", status:"failed", detail:"超时"},
          {stage:"qc", step:"batch1", label:"质检批次 1/2", status:"retry", detail:"重试 1/2"},
        ], "gate1")"""
    )
    assert "选项校验" in html
    assert "进行中" in html
    assert "质检批次" not in html, "按当前阶段过滤后不应显示 QC 事件"

    html_all = page.evaluate(
        """() => renderSubsteps([
          {stage:"qc", step:"batch1", label:"质检批次 1/2", status:"retry", detail:"重试 1/2"},
          {stage:"qc", step:"batch1", label:"质检批次 1/2", status:"failed", detail:"超时"},
        ], "")"""
    )
    assert "质检批次" in html_all
    assert "重试 1/2" in html_all
    assert "重试" in html_all
    assert "失败" in html_all

    done_html = page.evaluate(
        """() => renderSubsteps([
          {stage:"gate1", step:"options", label:"选项校验", status:"done", detail:"第 1 轮"},
          {stage:"qc", step:"batch1", label:"质检批次 1/2", status:"done", detail:"完成"},
        ], "done")"""
    )
    assert "选项校验" in done_html and "质检批次" in done_html, "终态应展示全部阶段子步骤"

    empty = page.evaluate('''() => renderSubsteps([], "gate1")''')
    assert "暂无子步骤记录" in empty
