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


def test_gap_paper_no_subject_shows_toast(page, server_url):
    """科目范围未选时点「一键刷薄弱组卷」→ 提示先选科目范围。"""
    _open_overview(page, server_url)
    # 概览视图默认激活，按钮静态存在
    page.locator("#btn_gap_paper").wait_for(state="visible", timeout=15000)
    page.click("#btn_gap_paper")
    page.wait_for_selector('#toasts >> text=请先在上方选择科目范围', timeout=15000)
    assert "请先在上方选择科目范围" in page.locator("#toasts").inner_text()


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
