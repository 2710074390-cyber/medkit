"""IMP-05：薄弱组卷（WP-03）浏览器用例（Playwright，零 LLM）。

覆盖「无来源/无科目」的稳定失败路径（以至少 1 条稳定断言为准）：
- UI：概览「⚡ 一键刷薄弱组卷」在科目范围未选（#dash_subject 为空）时点击 →
  弹出 toast「请先在上方选择科目范围」（不触发任何生成请求）；
- API：直接 POST /api/library/gap-paper 且 subject 为空 → 200 + ok:false + 明确 msg
  （当前没有可刷的薄弱知识点……），验证后端空输入是「软失败」，不会报 5xx 或空转 LLM。
"""

from __future__ import annotations


def _open_overview(page, server_url: str):
    page.goto(server_url)
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)


def test_gap_paper_no_subject_autopicks_first(page, server_url):
    """科目范围未选（全部科目）时点「一键刷薄弱组卷」：
    A. 无任何可选科目 → 提示「暂无可选科目…」（新文案，不再报「科目范围」错）；
    B. 有科目 → 自动选中第一个科目并进入组卷流程（不再要求用户先手选）。"""
    _open_overview(page, server_url)
    page.locator("#btn_gap_paper").wait_for(state="visible", timeout=15000)
    page.click("#btn_gap_paper")
    page.wait_for_selector('#toasts >> text=暂无可选科目', timeout=15000)

    # 场景 B：注入一条带科目的错题 → 刷新后科目下拉有选项 → 自动选中
    page.evaluate(
        """async () => {
          await fetch('/api/library/mistakes', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({source: 'manual', subject: '儿科学',
              chapter: '呼吸', question: '测试错题？',
              options: ['甲', '乙', '丙', '丁', '戊'], answer: 'A', analysis: '解析'})
          });
        }"""
    )
    page.reload()
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.locator("#btn_gap_paper").wait_for(state="visible", timeout=15000)
    page.click("#btn_gap_paper")
    page.wait_for_timeout(1500)
    assert "请先在上方选择科目范围" not in page.locator("#toasts").inner_text()
    sel_value = page.evaluate("() => document.getElementById('dash_subject').value")
    assert sel_value == "儿科学", f"应自动选中第一个可选科目，实得 {sel_value!r}"


def test_gap_paper_api_empty_subject_soft_fails(page, server_url):
    """后端空 subject 是软失败（200 + ok:false + msg），不 5xx / 不空转。"""
    _open_overview(page, server_url)
    body = page.evaluate(
        """async () => {
          const r = await fetch('/api/library/gap-paper', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({subject: ''})
          });
          let j = null; try { j = await r.json(); } catch (e) {}
          return {status: r.status, body: j};
        }"""
    )
    assert body["status"] == 200, f"预期返回 200，实得 {body['status']}（body={body['body']!r}）"
    assert body["body"] is not None and body["body"].get("ok") is False, f"应为软失败: {body['body']!r}"
    assert body["body"].get("msg"), "缺少软失败提示 msg"
