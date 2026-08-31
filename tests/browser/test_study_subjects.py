"""WP-4：刷题科目卡片点击过滤 + 科目管理删除（浏览器用例，零 LLM）。

覆盖：点击科目卡片立即过滤复习计划；「科目管理」弹层删除科目 →
刷新后卡片消失、另科保留；用后清理避免污染同会话其它用例。
"""

from __future__ import annotations


def _seed_subject(page, subject: str) -> None:
    page.evaluate(
        """async (subject) => {
          const r = await fetch('/api/library/mistakes', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source: 'manual', subject, chapter: '测试章',
              question: subject + '题干', know_tags: [subject],
              options: ['甲', '乙', '丙', '丁', '戊'], answer: 'A',
              analysis: '解析。'})
          });
          if (!r.ok) throw new Error('seed mistake ' + r.status);
          const q = await fetch('/api/library/review/queue-all?subject='
            + encodeURIComponent(subject), {method: 'POST'});
          if (!q.ok) throw new Error('seed queue ' + q.status);
          return await q.json();
        }""",
        subject,
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


def test_study_subject_card_filter(page, server_url):
    subject = "内科学_过滤A"
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    try:
        _seed_subject(page, subject)
        page.reload()
        page.wait_for_selector("#tab-start.show", timeout=15000)
        page.click('button[data-tab="study"]')
        page.wait_for_selector("#tab-study.show", timeout=15000)
        page.wait_for_selector(".subj-card", timeout=15000)

        card = page.locator(".subj-card", has_text=subject)
        card.wait_for(state="visible", timeout=15000)
        card.click()
        page.wait_for_function(
            "() => document.getElementById('rv_subject')?.value === '" + subject + "'",
            timeout=15000,
        )
    finally:
        _cleanup(page, subject)


def test_delete_subject_through_manager(page, server_url):
    subject = "内科学_删除A"
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    try:
        _seed_subject(page, subject)
        page.reload()
        page.wait_for_selector("#tab-start.show", timeout=15000)
        page.click('button[data-tab="study"]')
        page.wait_for_selector("#tab-study.show", timeout=15000)
        page.wait_for_selector(".subj-card", timeout=15000)

        page.click('button:has-text("科目管理")')
        page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.getElementById('md_body').innerText.includes('" + subject + "')",
            timeout=15000,
        )
        page.click('button[data-subj="' + subject + '"]')
        page.wait_for_function(
            "() => document.getElementById('md_ok').innerText.includes('导出并删除')",
            timeout=15000,
        )
        page.click("#md_ok")

        # onOk 后重开科目管理弹层：列表应已无该科目
        page.wait_for_function(
            "() => document.getElementById('md_title').innerText.includes('科目管理')",
            timeout=15000,
        )
        page.wait_for_function(
            "() => !document.getElementById('md_body').innerText.includes('" + subject + "')",
            timeout=15000,
        )
        page.click("#md_cancel")
        page.wait_for_selector("#modal_mask", state="hidden", timeout=15000)
        assert page.locator(".subj-card", has_text=subject).count() == 0
    finally:
        _cleanup(page, subject)
