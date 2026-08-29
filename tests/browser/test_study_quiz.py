"""v0.8.1：刷题 tab 卡片化（PRD 6.4）浏览器用例（Playwright，零 LLM）。

覆盖：
- 铺卡 → 今日到期知识点以翻转卡渲染（.qcard，正面 = 知识点名）；
- 点击卡面翻面（.flipped），背面出现红黄绿三按钮（忘了/模糊/记住）；
- 点「记住」→ 出卡动效后到期列表空态，今日进度 X/Y 更新为 1/1；
- 快捷键 3 对当前卡自评（未翻面时自动翻面）。
"""

from __future__ import annotations


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


def _open_study(page, server_url: str):
    page.goto(f"{server_url}/#study")
    page.wait_for_selector("#tab-study.show", timeout=15000)


def test_study_card_flip_and_three_buttons(page, server_url):
    """铺卡 → 翻转卡渲染 → 点击翻面 → 三按钮 → 点「记住」→ 空态 + 进度 1/1。"""
    _open_study(page, server_url)
    assert _seed_mistake(page, "儿科学", "支气管肺炎") == 200
    assert _queue_all(page, "儿科学") >= 1
    page.reload()
    page.wait_for_selector("#tab-study.show", timeout=15000)

    card = page.locator("#rv_body .qcard")
    card.wait_for(state="visible", timeout=15000)
    assert "支气管肺炎" in card.inner_text(), "复习卡正面应显示知识点名"
    assert not card.evaluate("el => el.classList.contains('flipped')"), "初始应未翻面"

    # 进度条已渲染（0/1）
    label = page.locator("#study_progress .sprog-label")
    label.wait_for(state="visible", timeout=15000)
    assert "0/1" in label.inner_text(), f"初始进度应为 0/1，实得 {label.inner_text()!r}"

    # 点击卡面（非按钮）翻面 → 三按钮出现
    page.locator("#rv_body .qcard .qfront .rv-q").click()
    page.wait_for_timeout(450)
    assert card.evaluate("el => el.classList.contains('flipped')"), "点击卡面应翻面"
    assert page.locator("#rv_body .grades3 .g3").count() == 3
    assert page.locator("#rv_body .g3.forget").inner_text() == "忘了"
    assert page.locator("#rv_body .g3.fuzzy").inner_text() == "模糊"
    assert page.locator("#rv_body .g3.got").inner_text() == "记住"

    # 点「记住」→ 卡片移出今日到期 → 空态；进度 1/1
    page.locator("#rv_body .g3.got").click()
    page.wait_for_selector("#rv_body .empty", timeout=15000)
    page.wait_for_function(
        "() => document.querySelector('#study_progress .sprog-label').innerText.includes('1/1')",
        timeout=15000,
    )


def test_study_keyboard_shortcut_flips_then_grades(page, server_url):
    """D-10：快捷键 1/2/3 未翻面仅翻面不评分；已翻面才评分（防误触给首卡打 0 分）。"""
    _open_study(page, server_url)
    assert _seed_mistake(page, "内科学", "心力衰竭") == 200
    assert _queue_all(page, "内科学") >= 1
    page.reload()
    page.wait_for_selector("#tab-study.show", timeout=15000)

    card = page.locator("#rv_body .qcard")
    card.wait_for(state="visible", timeout=15000)
    # 未翻面 → 按 3 只翻面，不评分（卡仍在队列）
    page.keyboard.press("3")
    page.wait_for_function(
        "() => document.querySelector('#rv_body .qcard')?.classList.contains('flipped')",
        timeout=15000,
    )
    assert page.locator("#rv_body .qcard").count() == 1, "未翻面按快捷键只翻面不评分"
    # 已翻面 → 再按 3 → 评分并出队
    page.keyboard.press("3")
    page.wait_for_selector("#rv_body .empty", timeout=15000)
    assert page.locator("#toasts .toast.bad").count() == 0, "快捷键自评不应报错"
