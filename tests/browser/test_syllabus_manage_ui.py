"""WP-10：大纲管理改名与角色标签（浏览器用例，零 LLM）。"""

from __future__ import annotations


def test_syllabus_manage_rename_and_roles(page, server_url):
    """v0.19 IA 迁址：大纲管理从「学习中心」迁入「设置 → 进阶功能」，标题与角色标签不变。"""
    page.goto(f"{server_url}/#mine")
    page.wait_for_selector("#tab-mine.show", timeout=15000)
    page.evaluate(
        "() => { const d = document.querySelector('#lv-syllabus')"
        "?.closest('details.adv'); if (d) d.open = true; }")
    page.wait_for_selector("#lv-syllabus", state="visible", timeout=15000)

    text = page.locator("#lv-syllabus").inner_text()
    assert "大纲管理" in text, "视图标题应为「大纲管理」"
    assert "大纲覆盖" not in text, "功能名不应再出现「大纲覆盖」"
    pills = page.locator("#syl_std").inner_text()
    assert "教师重点（主要依据）" in pills
    assert "官方 306（补充）" in pills
    # 它是「设置」页里可发现的入口：折叠区标题必须点名「大纲管理」
    assert "大纲管理" in page.locator("details.adv summary").first.inner_text()
