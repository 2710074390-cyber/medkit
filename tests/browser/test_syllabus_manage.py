"""WP-10：大纲管理改名与角色标签（浏览器用例，零 LLM）。"""

from __future__ import annotations


def test_syllabus_manage_rename_and_roles(page, server_url):
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('button[data-lv="syllabus"]')
    page.wait_for_selector("#lv-syllabus.show", timeout=15000)

    text = page.locator("#lv-syllabus").inner_text()
    assert "大纲管理" in text, "视图标题应为「大纲管理」"
    assert "大纲覆盖" not in text, "功能名不应再出现「大纲覆盖」"
    pills = page.locator("#syl_std").inner_text()
    assert "教师重点（主要依据）" in pills
    assert "官方 306（补充）" in pills
    assert "大纲管理" in page.locator('button[data-lv="syllabus"]').inner_text()
