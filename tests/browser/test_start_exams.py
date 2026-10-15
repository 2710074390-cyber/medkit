"""v0.10.0 PR-1：多场考试计划 + 红点来源可感知（浏览器用例，零 LLM）。

覆盖：
- 开始页可添加多场考试（名称/日期/标签/提醒），刷新保留、按日期排序；
- 删除考试不影响其它场次；
- 旧单场键 medkit-exam-date 自动迁移为一场考试；
- 有待办（今日到期）时侧栏红点带来源 title，学习中心子导航徽章带说明。
"""

from __future__ import annotations


def _goto_start_clear(page, server_url: str) -> None:
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.evaluate(
        "() => { localStorage.removeItem('medkit-exams'); localStorage.removeItem('medkit-exam-date'); }"
    )
    page.reload()
    page.wait_for_selector("#tab-start.show", timeout=15000)


def _add_exam(page, title: str, date: str, tag: str = "") -> None:
    page.locator(".exam-add").click()
    page.fill("#exam_f_title", title)
    page.fill("#exam_f_date", date)
    page.fill("#exam_f_tag", tag)
    page.locator("#exam_f_ok").click()
    page.wait_for_selector(".exam-card", timeout=15000)


def test_start_multi_exam_add_sort_delete(page, server_url):
    _goto_start_clear(page, server_url)
    page.wait_for_selector(".exam-add", timeout=15000)

    _add_exam(page, "考研初试", "2099-12-25", "考研")
    assert page.locator(".exam-card").count() == 1
    assert "考研初试" in page.locator(".exam-card").first.inner_text()

    _add_exam(page, "期末", "2098-06-30", "期末")
    assert page.locator(".exam-card").count() == 2
    # 按日期升序：2098 期末应排在 2099 考研前
    assert "期末" in page.locator(".exam-card").first.inner_text()
    assert "考研初试" in page.locator(".exam-card").nth(1).inner_text()

    # 刷新保留
    page.reload()
    page.wait_for_selector(".exam-card", timeout=15000)
    assert page.locator(".exam-card").count() == 2

    # 删除期末 → 只剩考研
    first = page.locator(".exam-card").first
    first.locator('button:has-text("删除")').click()
    page.wait_for_selector("#modal_mask", timeout=15000)
    page.locator("#md_ok").click()
    page.wait_for_function("() => document.querySelectorAll('.exam-card').length === 1", timeout=15000)
    assert "考研初试" in page.locator(".exam-card").first.inner_text()


def test_start_exam_reminder_window_active(page, server_url):
    """R4-20：进入「考前 N 天」提醒窗口 → 卡片醒目提示（死数据变真触达）。"""
    _goto_start_clear(page, server_url)
    page.wait_for_selector(".exam-add", timeout=15000)
    date = page.evaluate(
        """() => { const d = new Date(Date.now() + 86400000);
                   const y = d.getFullYear(), m = String(d.getMonth() + 1).padStart(2, '0'),
                         dd = String(d.getDate()).padStart(2, '0');
                   return y + '-' + m + '-' + dd; }"""
    )
    _add_exam(page, "冲刺测试", date)
    txt = page.locator(".exam-card").first.inner_text()
    assert "备考冲刺提醒" in txt, f"进入提醒窗口应显示冲刺提示：{txt}"


def test_start_exam_legacy_migration(page, server_url):
    _goto_start_clear(page, server_url)
    page.evaluate("() => localStorage.setItem('medkit-exam-date', '2099-06-01')")
    page.reload()
    page.wait_for_selector(".exam-card", timeout=15000)
    assert page.locator(".exam-card").count() == 1
    assert "考试" in page.locator(".exam-card").first.inner_text()
    assert "2099-06-01" in page.locator(".exam-card").first.inner_text()
    left = page.evaluate("() => localStorage.getItem('medkit-exam-date')")
    assert left is None, "旧单场键应被迁移并清除"


def _seed_mistake(page, subject: str, tag: str) -> int:
    return page.evaluate(
        """async ([subject, tag]) => {
          const r = await fetch('/api/library/mistakes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source: 'manual', subject,
              chapter: '测试章', question: '测试题干——' + tag,
              know_tags: [tag], options: ['甲', '乙', '丙', '丁', '戊'],
              answer: 'A', analysis: '首选药为青霉素，确诊金标准为培养。'})
          });
          return r.status;
        }""",
        [subject, tag],
    )
