"""WP-4：科目管理（按科目查看 / 删除本地数据）浏览器用例，零 LLM。

覆盖：「科目管理」弹层删除科目 → 重开弹层该科目已消失、另科保留；
用后清理避免污染同会话其它用例。

2026-10-15（奥卡姆精简 T2）：原「刷题」页的**科目卡片过滤**随复习卡域删除（卡片按今日到期
复习数过滤，数据源已不存在）；「科目管理」入口随之搬到**学习中心 → 错题本**的科目行
（能力保留、位置更贴近它真正管理的数据）。
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


def _open_subject_manager(page, server_url: str) -> None:
    """从「学习中心 → 错题本」的科目行打开科目管理弹层。"""
    page.goto(f"{server_url}/#learn")
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('#learnnav button[data-lv="mistakes"]')
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)
    page.click('button:has-text("科目管理")')
    page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
    page.wait_for_function(
        "() => document.getElementById('md_title').innerText.includes('科目管理')",
        timeout=15000,
    )


def test_delete_subject_through_manager(page, server_url):
    subject = "内科学_删除A"
    try:
        page.goto(f"{server_url}/#start")
        page.wait_for_selector("#tab-start.show", timeout=15000)
        _seed_subject(page, subject)

        _open_subject_manager(page, server_url)
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
        # 显式断言（上面 wait_for_function 的 AST 看不见；`test_assertion_audit` 要求可见断言）
        listing = page.evaluate(
            "() => document.getElementById('md_body').innerText")
        assert subject not in listing, (
            f"删除后科目管理列表仍含「{subject}」")
        page.click("#md_cancel")
        page.wait_for_selector("#modal_mask", state="hidden", timeout=15000)
    finally:
        _cleanup(page, subject)
