"""题库练习（bank 域）端到端：导入 → 列表 → 作答判分 → 判错回流错题本（浏览器用例，零 LLM）。

为什么必须有这一层：`bank.js / bank-import.js / bank-drill.js` 的跨片调用（列表 → 作答引擎）
在**加载期闸门**里看不见（那只看加载期前向引用），单元测试也覆盖不到 DOM 交互——
点击时才炸的 `ReferenceError` 只有浏览器用例能抓（实测抓到 `classList.add("")` 抛异常）。

v0.13.0「医考帮式刷题」新增覆盖：点选项即判分、答题卡跳转、背题模式（**不记流水**）、
上一题回看保留作答、多选需「确认作答」、「刷题」页做题入口。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

_MD = ("# 题库\n\n### q1 · A1 型 · 单选 · 理解\n**慢阻肺的诊断**\n"
       "慢阻肺最主要的病因是\n- A. 吸烟\n- B. 感染\n"
       "**✅ 答案：A**\n💡 吸烟是主要危险因素\n\n"
       "### q2 · X 型 · 多选 · 理解\n**哮喘的特征**\n"
       "哮喘的典型特征包括\n- A. 发作性呼吸困难\n- B. 双肺哮鸣音\n- C. 症状可缓解\n- D. 咳大量脓痰\n"
       "**✅ 答案：ABC**\n💡 哮喘三大特征：发作性、可逆性、气道高反应性\n")
_BANK_NAME = "浏览器用例题库"
# R7/V-10 隔离范式：**专属科目**——判错回流会在 `knowledge` 里建同名知识点，
# 而「铺卡（薄弱点全部入队）」会把遗留知识点铺进 `#rv_body`，挤爆 test_study_quiz 的
# 「只有 1 张卡」断言（2026-10-10 实测踩到）。故用专属科目 + 用例结束按科目整体删除。
_SUBJECT = "内科学_题库练习A"
_Q_STEM = "慢阻肺最主要的病因是"


def _seed_bank(page) -> str:
    return page.evaluate(
        """async ([md, name, subject]) => {
          const p = await (await fetch('/api/bank/import/preview', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: md, fmt: 'md'})})).json();
          const c = await (await fetch('/api/bank/import/commit', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({name: name, subject: subject, items: p.items})})).json();
          return c.bank_id;
        }""", [_MD, _BANK_NAME, _SUBJECT])


def _cleanup(page) -> None:
    """清掉本用例播种的题库 + 该科目下的错题/知识点/复习卡（避免污染同一会话的其它用例）。"""
    page.evaluate(
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


def _open_drill(page, server_url: str) -> None:
    page.goto(f"{server_url}/#study")
    page.wait_for_selector("#tab-study.show", timeout=15000)


def _start_first_bank(page) -> None:
    page.click('.bank-item button:has-text("开始练习")')
    page.wait_for_selector(".bank-q", timeout=15000)


def test_bank_import_list_and_correct_answer(page, server_url):
    """单选：**点选项即判分**（不再需要「提交」那一步），判对不进错题本。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector("#tab-study.show", timeout=15000)
        page.wait_for_selector(".bank-item", timeout=15000)
        assert _BANK_NAME in page.locator("#bank_list").inner_text()

        _start_first_bank(page)
        assert _Q_STEM in page.locator(".bank-q").inner_text()
        # 题目里不得带答案（判分前）
        assert "吸烟是主要危险因素" not in page.locator("#bank_drill_body").inner_text()
        # 单选：不该有「确认作答」按钮（点选项即判分）
        assert page.locator("#btn_bank_submit").count() == 0

        page.click("#bopt_0")            # A = 正确 → 立即判分
        page.wait_for_selector(".bank-ans", timeout=15000)
        txt = page.locator(".bank-ans").inner_text()
        assert "答对了" in txt, txt
        assert "吸烟是主要危险因素" in txt, "判分后必须给出解析"

        # 答对不进错题本
        n = page.evaluate(
            """async (stem) => {
              const ms = await (await fetch('/api/library/mistakes')).json();
              return (ms.mistakes || []).filter(m => (m.question || '').includes(stem)).length;
            }""", _Q_STEM)
        assert n == 0
    finally:
        _cleanup(page)


def test_wrong_answer_shows_analysis_and_syncs_to_mistakes(page, server_url):
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector(".bank-item", timeout=15000)
        _start_first_bank(page)

        page.click("#bopt_1")            # B = 错 → 立即判分
        page.wait_for_selector(".bank-ans", timeout=15000)
        txt = page.locator(".bank-ans").inner_text()
        assert "答错了" in txt, txt
        assert "正确答案：A" in txt, txt
        assert "已自动加入错题本" in txt, "判错必须如实告知已回流错题本"

        # 真回流（不是只改了文案）：后端错题本里查得到，且带 user_answer
        row = page.evaluate(
            """async (stem) => {
              const ms = await (await fetch('/api/library/mistakes')).json();
              return (ms.mistakes || []).find(m => (m.question || '').includes(stem)) || null;
            }""", _Q_STEM)
        assert row is not None, "判错后错题本里应有这道题"
        assert row.get("user_answer") == "B", f"回流必须带用户作答，实得 {row.get('user_answer')!r}"
        assert row.get("answer") == "A"
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- v0.13.0 医考帮式交互
def test_multi_choice_needs_confirm(page, server_url):
    """多选（X 型）：点选项只切换选中，须点「确认作答」才判分。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector(".bank-item", timeout=15000)
        _start_first_bank(page)
        # 跳到第 2 题（多选）
        page.click("#btn_drill_card")
        page.wait_for_selector(".dc-cell", timeout=15000)
        page.locator(".dc-cell").nth(1).click()
        page.wait_for_selector(".bank-q", timeout=15000)
        assert page.locator("#btn_bank_submit").count() == 1, "多选必须有「确认作答」"

        page.click("#bopt_0")
        page.click("#bopt_1")
        page.click("#bopt_2")
        assert page.locator(".bank-ans").count() == 0, "多选未确认前不得判分"
        page.click("#btn_bank_submit")
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text(), "ABC 全选应判对"
    finally:
        _cleanup(page)


def test_answer_card_jump_and_prev_keeps_answer(page, server_url):
    """答题卡：题号网格可跳题；「上一题」回看已作答的题保留作答与解析。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector(".bank-item", timeout=15000)
        _start_first_bank(page)
        page.click("#bopt_0")                     # 第 1 题答对
        page.wait_for_selector(".bank-ans", timeout=15000)

        page.click("#btn_drill_card")             # 开答题卡
        page.wait_for_selector(".dc-cell", timeout=15000)
        assert page.locator(".dc-cell").count() == 2, "两题 → 两格"
        assert page.locator(".dc-cell.ok").count() == 1, "答对的那格应标绿"
        page.locator(".dc-cell").nth(1).click()   # 跳到第 2 题
        page.wait_for_selector(".bank-q", timeout=15000)
        assert "第 2 /" in page.locator(".bank-qidx").inner_text()

        page.click('button:has-text("上一题")')    # 回到第 1 题
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text(), "回看必须保留已作答结果"
    finally:
        _cleanup(page)


def test_recite_mode_shows_answer_without_recording(page, server_url):
    """背题模式：直接显示答案与解析，且**不记作答流水**（否则正确率与错题本会被污染）。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector(".bank-item", timeout=15000)
        _start_first_bank(page)
        before = page.evaluate("async () => (await (await fetch('/api/bank/today')).json()).attempts")

        page.click("#btn_drill_runmode")           # 切到背题模式
        page.wait_for_selector(".bank-ans", timeout=15000)
        txt = page.locator(".bank-ans").inner_text()
        assert "答案（背题）" in txt, txt
        assert "正确答案：A" in txt, txt
        assert "吸烟是主要危险因素" in txt, "背题模式也要给解析"
        # 背题模式点选项不该判分/不产生作答（q1 只有 A/B 两个选项）
        page.click("#bopt_1")
        page.wait_for_timeout(300)
        assert page.locator(".dc-cell.no").count() == 0

        after = page.evaluate("async () => (await (await fetch('/api/bank/today')).json()).attempts")
        assert after == before, f"背题模式不得记作答流水（{before} → {after}）"

        # 切回练习模式：又变成「点选项即判分」
        page.click("#btn_drill_runmode")
        page.wait_for_timeout(300)
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text()
    finally:
        _cleanup(page)


def test_study_page_do_view_starts_drill(page, server_url):
    """「刷题」页默认是「做题」子视图：快捷卡片可进入沉浸式作答；复习子标签仍在。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        # v0.15：_open_drill 已落在「刷题」页（#study），再 goto 同址不会触发 hashchange；
        # 用 reload 重跑 initTab → showTab("study") → studyView('do') → 重拉题库渲染快捷卡片。
        page.reload()
        page.wait_for_selector("#tab-study.show", timeout=15000)
        assert page.locator("#sv-do").is_visible(), "默认应停在「做题」子视图"
        # 2026-10-15（奥卡姆精简 T2）：「复习（卡片）」子视图随其域删除 → 节点不再存在
        assert page.locator("#sv-review").count() == 0
        page.wait_for_selector(".do-quick-card", timeout=15000)
        page.locator(".do-quick-card").first.click()
        page.wait_for_selector("#bank_drill_card .bank-q", timeout=15000)
        assert _Q_STEM in page.locator(".bank-q").inner_text()

        page.click("#bank_drill_card button:has-text('退出')")
        page.wait_for_timeout(400)
        assert not page.locator("#bank_drill_card").is_visible(), "退出后沉浸层应收起"
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- F11 / F10
_PROJ_PID = "proj_bank_ui"
_PROJ_SUBJECT = "内科学_项目导入A"
_PROJ_Q = "项目导入的题目——心衰治疗要点"


def _seed_project(home: Path) -> None:
    """往隔离 home 的项目目录里播种一套产物（模拟出题工坊的产出）。"""
    d = home / "projects" / _PROJ_PID / "最终产物"
    d.mkdir(parents=True, exist_ok=True)
    (home / "projects" / _PROJ_PID / "meta.json").write_text(
        json.dumps({"subject": _PROJ_SUBJECT, "pid": _PROJ_PID}), encoding="utf-8")
    (d / "questions_final.json").write_text(json.dumps([{
        "id": "q1", "type": "A1", "bloom": "理解", "subtopic": "心衰治疗",
        "question": _PROJ_Q, "options": ["限盐", "绝对卧床"], "answer": "A",
        "analysis": "限盐是基石",
    }], ensure_ascii=False), encoding="utf-8")


def _drop_project(home: Path) -> None:
    shutil.rmtree(home / "projects" / _PROJ_PID, ignore_errors=True)


# ---------------------------------------------------------------- F2 / F9
def test_manage_edit_answer_and_star(page, server_url):
    """F2：在「题目管理」里改答案 → 判分口径立刻跟着变；F9：收藏 → 「只练收藏」只出这道题。"""
    _open_drill(page, server_url)
    _seed_bank(page)
    try:
        page.reload()
        page.wait_for_selector(".bank-item", timeout=15000)
        page.click('.bank-item button:has-text("管理题目")')
        page.wait_for_selector("#bank_item_list .bank-preview-table", timeout=15000)

        page.click('#bank_item_list button:has-text("编辑")')
        page.wait_for_selector("#bi_q", timeout=15000)
        assert page.input_value("#bi_ans") == "A", "导入的答案应回填到编辑表单"
        page.fill("#bi_ans", "B")      # 把答案从 A 改成 B
        page.check("#bi_star")         # 顺便收藏
        page.click('#bank_item_form button:has-text("保存")')
        # 等**星标真的变亮**（不能用「列表含 B」——q2 的多选答案 ABC 里也有 B，会提前满足）
        page.wait_for_function(
            "() => { const b = document.querySelector('#bank_item_list button[title^=\"收藏\"]');"
            "  return !!b && b.textContent.trim() === '★'; }",
            timeout=15000)
        star_btn = page.locator('#bank_item_list button[title^="收藏"]').first
        assert star_btn.inner_text().strip() == "★", "收藏后列表里星标应点亮"

        # 练习：只练收藏 → 只出这 1 题；判分用**改后**的答案
        _start_first_bank(page)
        page.click('.bank-bar button:has-text("只练收藏")')
        page.wait_for_selector(".bank-q", timeout=15000)
        assert page.locator(".bank-q").count() == 1
        assert _Q_STEM in page.locator(".bank-q").inner_text()
        page.click("#bopt_1")          # B —— 改后的正确答案（点选项即判分）
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text(), \
            "改答案必须真的改到判分口径上（不是只改了显示）"
    finally:
        _cleanup(page)


# ---------------------------------------------------------------- F7 / B7
_RETRY_SUBJECT = "内科学_重练A"
_RETRY_TAG = "重练考点A"
_RETRY_Q = "重练用题干——心衰治疗要点"


def _drop_retry(page) -> None:
    page.evaluate(
        """async (subject) => {
          try {
            await fetch('/api/library/subjects/delete', {
              method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({subject: subject})});
          } catch (e) { /* ignore */ }
        }""", _RETRY_SUBJECT)


def test_mistake_retry_archives_on_correct_and_daily_goal_renders(page, server_url):
    """F7：错题本「→ 重练」直达 → 判对归档「已掌握」（落库）；B7：今日目标把重练算进进度。"""
    page.goto(f"{server_url}/#study")
    page.wait_for_selector("#tab-study.show", timeout=15000)
    try:
        st = page.evaluate(
            """async ([subj, tag, q]) => {
              const r = await fetch('/api/library/mistakes', {
                method: 'POST', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({subject: subj, chapter: '测试章', question: q,
                  options: ['甲', '乙', '丙'], answer: 'B',
                  analysis: '解析：乙是正解', know_tags: [tag]})});
              return r.status;
            }""", [_RETRY_SUBJECT, _RETRY_TAG, _RETRY_Q])
        assert st == 200

        # 从错题本「→ 重练」直达（跨域调用经外壳 `openDrillFor` 转发）
        page.click('button[data-tab="learn"]')
        page.wait_for_selector("#tab-learn.show", timeout=15000)
        page.click('#learnnav button[data-lv="mistakes"]')
        # P-E（奥卡姆精简第二轮）：旧错题本（DB）已并入「错题本」的第二个 pane，
        # 进入后必须显式切到「旧库」——默认 pane 是「本地文件（真身）」。
        page.click('#mv_tabs .mv-tab[data-mv="lib"]')
        page.wait_for_selector('#mv-lib', state='visible', timeout=15000)
        page.wait_for_selector("#learn_mk .mk-row", timeout=15000)
        page.locator(".mk-row", has_text=_RETRY_TAG).locator('button:has-text("→ 重练")').click()
        page.wait_for_selector("#tab-study.show", timeout=15000)
        page.wait_for_selector(".bank-q", timeout=15000)
        assert _RETRY_Q in page.locator(".bank-q").inner_text(), "应直达被点的那道题"

        # 判错 → 仍是未掌握（点选项即判分）
        page.click("#bopt_0")
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答错了" in page.locator(".bank-ans").inner_text()
        assert "仍是未掌握" in page.locator(".bank-ans").inner_text()

        # 重开一轮并判对 → 归档
        page.click('.bank-bar button:has-text("全部错题")')
        page.wait_for_selector(".bank-q", timeout=15000)
        page.click("#bopt_1")          # B = 正解
        page.wait_for_selector(".bank-ans", timeout=15000)
        assert "答对了" in page.locator(".bank-ans").inner_text()
        assert "已归档为「已掌握」" in page.locator(".bank-ans").inner_text()

        row = page.evaluate(
            """async (q) => {
              const ms = await (await fetch('/api/library/mistakes')).json();
              return (ms.mistakes || []).find(m => (m.question || '') === q) || null;
            }""", _RETRY_Q)
        assert row is not None and row.get("learned") is True, "判对必须真的归档（落库，不只是文案）"

        # 归档后「只看未掌握」应查不到它
        page.click('.bank-bar button:has-text("只看未掌握")')
        page.wait_for_selector("#bank_drill_body .empty", timeout=15000)

        # B7：今日目标进度**把错题重练也算进去**（重练 2 次 → 至少 2）
        # 每日目标在「刷题」页（tab-study）的「做题」子视图里
        page.click("#bank_drill_card button:has-text('退出')")
        page.goto(f"{server_url}/#study")
        page.wait_for_selector("#tab-study.show", timeout=15000)
        page.wait_for_function(
            "() => { const t = document.querySelector('#bank_goal_prog');"
            "  return t && /今日已练\\s*[2-9]/.test(t.textContent); }",
            timeout=15000)
        assert page.input_value("#bank_goal") == "20", "默认目标题数"
    finally:
        _drop_retry(page)


def test_import_from_project_and_export_paper(page, server_url, server_home):
    """F11：从出题项目导入题库（弹层 → 命名 → 入库）；F10：题库导出为交互押题卷 HTML。"""
    _seed_project(server_home)
    page.goto(f"{server_url}/#study")
    page.wait_for_selector("#tab-study.show", timeout=15000)
    try:
        page.click('button:has-text("从出题项目导入")')
        page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
        page.wait_for_function(
            "() => document.getElementById('md_body').innerText.includes('" + _PROJ_SUBJECT + "')",
            timeout=15000,
        )
        page.click('button:has-text("导入为题库")')
        page.wait_for_selector("#md_input", state="visible", timeout=15000)
        assert page.input_value("#md_input") == _PROJ_SUBJECT, "默认题库名应回落到项目科目"
        page.click("#md_ok")
        page.wait_for_function(
            "() => document.querySelector('#bank_list').innerText.includes('" + _PROJ_SUBJECT + "')",
            timeout=15000,
        )
        row = page.locator(".bank-item", has_text=_PROJ_SUBJECT)
        assert "题目" in row.inner_text()

        # F10：导出押题卷 → 新开标签页（inline 渲染，内容含题干）
        with page.expect_popup() as pop:
            row.locator('button:has-text("导出押题卷")').click()
        paper = pop.value
        paper.wait_for_load_state(timeout=15000)
        assert _PROJ_Q in paper.content(), "导出的押题卷 HTML 应包含题干"
        assert paper.url.startswith(server_url), "导出页必须是同源（离线可用）"
        paper.close()
    finally:
        page.evaluate(
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
              } catch (e) { /* ignore */ }
            }""", [_PROJ_SUBJECT, _PROJ_SUBJECT])
        _drop_project(server_home)
