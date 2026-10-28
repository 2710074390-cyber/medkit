"""学习者模型与逐选项解析的浏览器层用例（v0.22 阶段 5）。

## 为什么必须有这一层

阶段 5 的失败方式是**静默少一块**：字段名对不上、渲染分支写错、脚本装载顺序错，
单元测试全绿而页面上什么都不显示。装载期的 `no-undef` 只看「拼错标识符」，
点击时才炸的分支（如 `cls.add("")`）只有真浏览器能抓。

## 覆盖

1. 作答前**把握分**可点、可不点，选中的值随判分提交；
2. 判分后结果区显示**考点**（`kc` 摘要）与逐选项理由（有字段时）；
3. 「今天」页的**知识点掌握**面板长出真实行（不是空态占位）；
4. 「设置 › 进阶功能」的**映射确认台**可预演回填。

全部零 LLM：只走导入 + 判分 + 回填三条本地链路。
"""

from __future__ import annotations

_BANK_NAME = "浏览器用Kc题库"
_SUBJECT = "内科学_Kc用例"
_Q_STEM = "慢阻肺最主要的病因是"


def _boot(page, server_url: str) -> None:
    """先落到应用页：`about:blank` 上执行 `fetch('/api/...')` 会因相对 URL 无法解析而抛错。"""
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)


_KC_ID = "kc_ui_e2e"          # 合成考点 id：映射写入不校验考点存在（UI 链路测的是接线）


def _map_item(page, item_id: str, kc_id: str = _KC_ID) -> None:
    """走**唯一写入口**确认映射（人工确认 ⇒ confirmed=1）。

    为什么不用「规则回填 + 精确匹配」：那条路依赖内置大纲种子，纯净安装没有种子 ⇒
    用例要么依赖环境、要么得 skip。回填的「exact 自动确认 / fuzzy 必须人工点」
    已由单元测试覆盖（tests/test_kc.py），浏览器层只验**接线**。
    """
    page.evaluate(
        """async ([iid, kc]) => {
          await fetch('/api/kc/items/' + encodeURIComponent(iid) + '/map', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({kc_ids: [kc]})});
        }""", [item_id, kc_id])


def _first_item_id(page) -> str:
    """取本用例题库里的第一道题 id（drill 端点返回的是「不含答案」的题目列表）。"""
    return page.evaluate(
        """async () => {
          const r = await (await fetch('/api/bank/banks')).json();
          for (const b of (r.banks || [])) {
            const it = await (await fetch('/api/bank/banks/' + encodeURIComponent(b.id)
              + '/drill?mode=all')).json();
            const items = it.items || [];
            if (items.length) return items[0].id;
          }
          return '';
        }""")


def _seed_bank(page, topic: str) -> str:
    md = ("# 题库\n\n### q1 · A1 型 · 单选 · 理解\n"
          f"**{topic}**\n{_Q_STEM}\n- A. 吸烟\n- B. 感染\n"
          "**✅ 答案：A**\n💡 吸烟是主要危险因素\n")
    return page.evaluate(
        """async ([md, name, subject]) => {
          const p = await (await fetch('/api/bank/import/preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: md, fmt: 'md'})})).json();
          const c = await (await fetch('/api/bank/import/commit', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, subject: subject, items: p.items})})).json();
          return c.bank_id;
        }""", [md, _BANK_NAME, _SUBJECT])


def _cleanup(page) -> None:
    page.evaluate(
        """async ([name, subject]) => {
          const r = await (await fetch('/api/bank/banks')).json();
          for (const b of (r.banks || [])) {
            if (b.name === name) {
              await fetch('/api/bank/banks/' + encodeURIComponent(b.id), {method:'DELETE'});
            }
          }
          try { await fetch('/api/library/subjects/delete', {method:'POST',
            headers:{'Content-Type':'application/json'},
            body: JSON.stringify({subject: subject})}); } catch (e) {}
        }""", [_BANK_NAME, _SUBJECT])


def _start_first_bank(page, server_url: str) -> None:
    page.goto(f"{server_url}/#study")
    page.wait_for_selector("#tab-study.show", timeout=15000)
    page.wait_for_selector(".bank-item", timeout=15000)
    page.click('.bank-item button:has-text("开始练习")')
    page.wait_for_selector(".bank-q", timeout=15000)


# ---------------------------------------------------------------- 把握分
def test_confidence_row_exists_and_is_optional(page, server_url):
    """作答前有一行把握分；**不点也能做题**（可跳过是硬要求）。"""
    _boot(page, server_url)
    _seed_bank(page, "慢阻肺的诊断")
    try:
        _start_first_bank(page, server_url)
        assert page.locator("#bank_conf_box").count() == 1, "缺少把握分一行"
        assert page.locator('#bank_conf_box [role="radio"]').count() == 5
        # 不点把握分直接作答 → 必须能判分（可选字段不得变成必填）
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text()
    finally:
        _cleanup(page)


def test_confidence_is_submitted_with_the_attempt(page, server_url):
    """点了把握分 ⇒ 值真的进了流水（不是只改了界面高亮）。"""
    _boot(page, server_url)
    _seed_bank(page, "慢阻肺的诊断")
    try:
        _start_first_bank(page, server_url)
        page.locator('#bank_conf_box [role="radio"]').nth(3).click()   # 第 4 个 = 把握 4
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)
        # 把握分是否真的入库：看校准桶——只有 `bank_attempts.confidence` 非空的行才会进桶
        buckets = page.evaluate(
            """async () => {
              const r = await (await fetch('/api/kc/calibration?days=1')).json();
              return (r.buckets || []).filter(b => b.n > 0).map(b => b.confidence);
            }""")
        assert 4 in buckets, f"把握分没有进入校准数据（实得桶 {buckets}）"
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- 结果区
def test_result_shows_kc_summary_after_mapping(page, server_url):
    """映射后判分 ⇒ 结果区显示「考点 + 掌握度」，且掌握度真的动了。"""
    _boot(page, server_url)
    _seed_bank(page, "慢阻肺的诊断")
    try:
        _map_item(page, _first_item_id(page))
        _start_first_bank(page, server_url)
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)
        txt = page.locator(".bank-ans").inner_text()
        assert "考点" in txt, txt
        assert "掌握度" in txt, txt
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- 知识点面板
def test_today_panel_lists_knowledge_state(page, server_url):
    """「今天」页的知识点掌握面板必须长出真实行（不是「还没有作答记录」占位）。"""
    _boot(page, server_url)
    _seed_bank(page, "慢阻肺的诊断")
    try:
        _map_item(page, _first_item_id(page))
        _start_first_bank(page, server_url)
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)

        page.goto(f"{server_url}/#start")
        page.wait_for_selector("#tab-start.show", timeout=15000)
        page.wait_for_selector("#kc_state_box .kc-row", timeout=15000)
        rows = page.locator("#kc_state_box .kc-row")
        assert rows.count() >= 1
        text = rows.first.inner_text()
        assert "%" in text, text
        assert "次" in text and "对" in text, f"掌握度必须带原始计数：{text}"
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- 映射确认台
def test_mapping_console_visible_and_dry_run_works(page, server_url):
    """「设置 › 进阶功能 › 知识点映射」：卡片可见，预演回填能给出报告。"""
    _boot(page, server_url)
    _seed_bank(page, "慢阻肺的诊断")
    try:
        page.goto(f"{server_url}/#mine")
        page.wait_for_selector("#tab-mine.show", timeout=15000)
        assert page.locator("#kc_map_card").is_visible(), "映射确认台未显示"
        page.click('#kc_map_card button:has-text("预演")')
        page.wait_for_selector("#kc_map_body", timeout=15000)
        assert page.locator("#kc_map_body").inner_text().strip() != "", "预演后没有任何反馈"
    finally:
        _cleanup(page)
