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


def _queue_all(page, subject: str = "") -> int:
    return page.evaluate(
        """async (subject) => {
          const r = await fetch('/api/library/review/queue-all?subject='
            + encodeURIComponent(subject), {method: 'POST'});
          return (await r.json()).added;
        }""",
        subject,
    )


def _cleanup_learn(page, subject: str, tag: str) -> None:
    """清理本用例播种的错题与复习卡，避免污染同一会话的其它浏览器用例。"""
    page.evaluate(
        """async ([subject, tag]) => {
          const ms = await (await fetch('/api/library/mistakes')).json();
          const ids = (ms.mistakes || []).filter(m =>
            m.subject === subject && (m.question || '').includes(tag)).map(m => m.id);
          for (const id of ids) {
            await fetch('/api/library/mistakes/' + encodeURIComponent(id), {method: 'DELETE'});
          }
          const cs = await (await fetch('/api/library/review/cards?subject='
            + encodeURIComponent(subject))).json();
          for (const c of (cs.cards || [])) {
            await fetch('/api/library/review/' + encodeURIComponent(c.id), {method: 'DELETE'});
          }
          return ids.length;
        }""",
        [subject, tag],
    )


def test_badge_source_tooltip_and_learn_note(page, server_url):
    """有待办 → 侧栏红点 title 可解释；学习中心子导航徽章带说明。"""
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    try:
        assert _seed_mistake(page, "儿科学", "支气管肺炎") == 200
        assert _queue_all(page, "儿科学") >= 1
        page.reload()
        page.wait_for_selector("#tab-start.show", timeout=15000)

        badge = page.locator('button[data-tab="study"] .navbadge')
        badge.wait_for(state="visible", timeout=15000)
        title = badge.get_attribute("title") or ""
        assert "今日到期" in title, f"红点应解释来源，实得 {title!r}"
        assert badge.get_attribute("data-source") == "study"

        # 学习中心概览出现说明条（错题入库后 loop.mistakes>0）
        page.locator('button[data-tab="learn"]').click()
        page.wait_for_selector("#tab-learn.show", timeout=15000)
        page.wait_for_function(
            "() => document.querySelector('#dash_loop')?.innerText.includes('侧栏红点来源')",
            timeout=15000,
        )
        nb = page.locator("#nb_mistakes")
        nb.wait_for(state="visible", timeout=15000)
        assert "错题本" in (nb.get_attribute("title") or ""), "子导航徽章应带来源说明"
    finally:
        _cleanup_learn(page, "儿科学", "支气管肺炎")
