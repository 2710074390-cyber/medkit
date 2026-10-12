"""窄屏 / 平板适配（2026-10-12）：768×1024 平板 + 390×844 手机 均不得横向溢出。

背景：体验质检（`docs/体验质检_医学生视角_2026-10-10.md` §七）自述「只测 Chromium、
窄屏只测 390×844，未测 768 平板」。本文件把「窄屏不溢出」变成**可证伪的守卫**，
并把 768 这一档补上。

证伪：删掉 `base.css` 的 `@media (max-width:420px)` 里
`.bank-preview-table{display:block;overflow-x:auto}` → 390 档必红。

引擎由 `MEDKIT_TEST_BROWSER`（chromium / firefox / webkit）选择，见 `conftest.py`。
"""

from __future__ import annotations

import pytest

_MD = ("# 题库\n\n### q1 · A1 型 · 单选 · 理解\n**慢阻肺的诊断**\n"
       "慢阻肺最主要的病因是\n- A. 吸烟\n- B. 感染\n"
       "**✅ 答案：A**\n💡 吸烟是主要危险因素\n")
_BANK_NAME = "响应式用例库"
_SUBJECT = "内科学_响应式A"

_TABS = ["start", "drill", "study", "bank", "learn", "mine"]
_SIZES = [(768, 1024), (390, 844)]


def _new_page(browser, width, height):
    """独立 context（自带窄屏视口）+ 预置首启引导前置状态（与 conftest 的 page 同口径）。"""
    ctx = browser.new_context(viewport={"width": width, "height": height})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    pg.add_init_script(
        "localStorage.setItem('medkit-onboarded','1');"
        "sessionStorage.setItem('medkit-wz-seen','1');"
        "localStorage.setItem('medkit-mode-seen','1');"
    )
    return ctx, pg


def _sw(pg) -> tuple[int, int]:
    return tuple(pg.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]"))


@pytest.mark.parametrize("size", _SIZES)
def test_no_horizontal_overflow_on_key_tabs(browser, server_url, size):
    """六个主页面在 768 平板与 390 手机上均不得出现整页横向滚动。"""
    ctx, pg = _new_page(browser, *size)
    try:
        for tab in _TABS:
            pg.goto(f"{server_url}/#{tab}")
            pg.wait_for_selector(f"#tab-{tab}.show", timeout=15000)
            sw, iw = _sw(pg)
            assert sw <= iw + 1, f"{size} 下 #{tab} 横向溢出：scrollWidth={sw} > innerWidth={iw}"
    finally:
        ctx.close()


@pytest.mark.parametrize("size", _SIZES)
def test_bank_manage_table_does_not_overflow(browser, server_url, size):
    """体验质检实测的溢出元凶：题目管理表格（★ / 笔记两列加入后 390 上溢出 458 > 390）。

    判据是**整页不横向滚动**（表格自己滚不算溢出）——成熟做法是「表格自滚，不是整页滚」。
    """
    ctx, pg = _new_page(browser, *size)
    try:
        pg.goto(f"{server_url}/#drill")
        pg.wait_for_selector("#tab-drill.show", timeout=15000)
        pg.evaluate(
            """async ([md, name, subject]) => {
              const p = await (await fetch('/api/bank/import/preview', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: md, fmt: 'md'})})).json();
              await fetch('/api/bank/import/commit', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name: name, subject: subject, items: p.items})});
            }""", [_MD, _BANK_NAME, _SUBJECT])
        pg.reload()
        pg.wait_for_selector("#tab-drill.show", timeout=15000)
        pg.wait_for_selector(".bank-item", timeout=15000)
        pg.click('button:has-text("管理题目")')
        pg.wait_for_selector("#bank_item_list .bank-preview-table", timeout=15000)
        sw, iw = _sw(pg)
        assert sw <= iw + 1, f"{size} 下题目管理表格导致整页横向溢出：{sw} > {iw}"
    finally:
        pg.evaluate(
            """async ([name, subject]) => {
              const r = await (await fetch('/api/bank/banks')).json();
              for (const b of (r.banks || [])) {
                if (b.name === name) {
                  await fetch('/api/bank/banks/' + encodeURIComponent(b.id), {method: 'DELETE'});
                }
              }
              try {
                await fetch('/api/library/subjects/delete', {
                  method: 'POST', headers: {'Content-Type': 'application/json'},
                  body: JSON.stringify({subject: subject})});
              } catch (e) { /* 科目不存在时忽略 */ }
            }""", [_BANK_NAME, _SUBJECT])
        ctx.close()
