"""WP-5：错题本分组折叠 + 多选 + 批量删除/标记已掌握（浏览器用例，零 LLM）。

覆盖：分组 <details> 出现且默认展开；勾选 2 道 → 批量删除 → 列表减少；
批量标记已掌握后「只看未掌握」即时隐藏。用后清理避免污染同会话其它用例。
"""

from __future__ import annotations


def _seed(page, subject: str, n: int) -> None:
    page.evaluate(
        """async ([subject, n]) => {
          for (let i = 0; i < n; i++) {
            const r = await fetch('/api/library/mistakes', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({source: 'manual', subject, chapter: '呼吸系统',
                question: subject + '题干' + i, know_tags: [subject + '考点'],
                options: ['甲', '乙', '丙', '丁', '戊'], answer: 'A',
                analysis: '解析。', miss_count: 1})
            });
            if (!r.ok) throw new Error('seed ' + r.status);
          }
          return n;
        }""",
        [subject, n],
    )


def _cleanup(page, subject: str) -> None:
    page.evaluate(
        """async (subject) => {
          const r = await fetch('/api/library/subjects/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({subject})
          });
          return r.status;
        }""",
        subject,
    )


def _goto_mistakes(page, server_url: str) -> None:
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('button[data-lv="mistakes"]')
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    page.wait_for_selector("#learn_mk .mk-row", timeout=15000)


def test_mistakes_group_and_batch_delete(page, server_url):
    subject = "内科学_批测A"
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    try:
        _seed(page, subject, 3)
        _goto_mistakes(page, server_url)

        # R4-21：全选按钮明示选中范围（数据 <100 → 「全选全部」）
        assert page.locator("#btn_mk_all").is_visible()
        assert page.locator("#btn_mk_all").inner_text() == "全选全部"

        # 分组渲染：<details> 默认展开，头部含科目与计数
        assert page.locator("#learn_mk .mk-group").count() >= 1
        first_group = page.locator("#learn_mk .mk-group").first
        assert "内科学_批测A" in first_group.inner_text()
        assert "3 道" in first_group.inner_text()

        # 勾选前 2 行
        boxes = page.locator("#learn_mk .mkck")
        boxes.nth(0).check()
        boxes.nth(1).check()
        assert "已选 2" in page.locator("#mk_sel_count").inner_text()

        # 批量导出 JSON：应触发下载
        with page.expect_download() as dl:
            page.click('button:has-text("导出 JSON")')
        assert dl.value.suggested_filename.endswith(".json")

        page.click("#btn_mk_batch_del")
        page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.getElementById('md_ok').innerText.includes('导出并删除')",
            timeout=15000,
        )
        page.click("#md_ok")
        page.wait_for_function(
            "() => document.querySelectorAll('#learn_mk .mk-row').length === 1",
            timeout=15000,
        )
    finally:
        _cleanup(page, subject)


def test_mistakes_batch_learn_hides_from_filter(page, server_url):
    subject = "外科学_批测B"
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    try:
        _seed(page, subject, 2)
        _goto_mistakes(page, server_url)

        boxes = page.locator("#learn_mk .mkck")
        boxes.nth(0).check()
        page.click('button:has-text("标记已掌握")')
        page.wait_for_function(
            "() => document.getElementById('mk_sel_count')?.innerText.includes('已选 0')",
            timeout=15000,
        )
        # 默认不过滤仍可见 2 行（标记只是归档标记）
        page.wait_for_selector("#learn_mk .mk-row", timeout=15000)
        page.check("#mk_filter_unlearned")
        page.wait_for_function(
            "() => document.querySelectorAll('#learn_mk .mk-row').length === 1",
            timeout=15000,
        )
    finally:
        _cleanup(page, subject)
