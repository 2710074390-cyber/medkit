"""WP-5：错题本分组折叠 + 多选 + 批量删除/标记已掌握（浏览器用例，零 LLM）。

覆盖：分组 <details> 出现且默认展开；勾选 2 道 → 批量删除 → 列表减少；
批量标记已掌握后「只看未掌握」即时隐藏。用后清理避免污染同会话其它用例。
"""

from __future__ import annotations

import json


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
        # R7（V-10）：同会话其它用例可能留下别的科目分组，故按**本用例的科目**定位分组，
        # 不假设它一定是第一组（此前依赖「遗留数据读不到」的缺陷才绿）。
        assert page.locator("#learn_mk .mk-group").count() >= 1
        group = page.locator("#learn_mk .mk-group", has_text=subject).first
        assert subject in group.inner_text()
        assert "3 道" in group.inner_text()

        # 勾选本组前 2 行
        boxes = group.locator(".mkck")
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
            """(subject) => {
              const g = [...document.querySelectorAll('#learn_mk .mk-group')]
                .find(el => el.innerText.includes(subject));
              return !g || g.querySelectorAll('.mk-row').length === 1;
            }""",
            arg=subject,
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

        group = page.locator("#learn_mk .mk-group", has_text=subject).first
        boxes = group.locator(".mkck")
        boxes.nth(0).check()
        page.click('button:has-text("标记已掌握")')
        page.wait_for_function(
            "() => document.getElementById('mk_sel_count')?.innerText.includes('已选 0')",
            timeout=15000,
        )
        # 默认不过滤仍可见本组 2 行（标记只是归档标记）
        assert group.locator(".mk-row").count() == 2
        page.check("#mk_filter_unlearned")
        page.wait_for_function(
            """(subject) => {
              const g = [...document.querySelectorAll('#learn_mk .mk-group')]
                .find(el => el.innerText.includes(subject));
              return !g || g.querySelectorAll('.mk-row').length === 1;
            }""",
            arg=subject,
            timeout=15000,
        )
    finally:
        _cleanup(page, subject)


# ---------------------------------------------------------------- EP-01：Anki .apkg 导入
def _goto_mistakes_no_wait(page, server_url: str) -> None:
    """进错题本视图但**不等错题行**（空库时没有 .mk-row，等它会超时）。"""
    page.goto(f"{server_url}/#start")
    page.wait_for_selector("#tab-start.show", timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('button[data-lv="mistakes"]')
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)


def test_apkg_import_dry_run_then_confirm(page, server_url):
    """Anki 导入：先**体检**（dry_run）再确认——且确认框里必须写明「不参与归因」。

    守的是"闸门如实告知"：Anki 里没有「把握程度 / 当时的想法」，导入的条目一律未过闸门。
    这句话如果不给用户看，他会以为归因没跑是 bug（真实误解）。
    """
    calls: list[dict] = []

    def handler(route):
        body = route.request.post_data or ""
        calls.append({"dry": "dry_run" in body})
        if "dry_run" in body:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "total": 3, "gated": 0, "ungated": 3, "created": 0, "skipped": 1,
                "errors": [], "meta": {"deck": "MedKit :: 儿科学",
                                       "models": {"MedKit 标准卡": ["题干", "选项", "答案", "解析", "溯源"]}},
                "warnings": ["字段映射：MedKit 标准卡 → 题干 / 选项 / 答案 / 解析 / 溯源",
                             "note 9：不是题目（模型「MedKit 记忆卡」）——已跳过"]},
                ensure_ascii=False))
        else:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "total": 3, "gated": 0, "ungated": 3, "created": 2, "skipped": 1,
                "errors": [], "meta": {}, "warnings": []}, ensure_ascii=False))

    page.route("**/api/errors/import/apkg*", handler)
    _goto_mistakes_no_wait(page, server_url)

    assert page.locator("#btn_mk_apkg").count() == 1, "错题本应有 Anki 导入入口"
    page.set_input_files("#mk_apkg", files=[{
        "name": "deck.apkg", "mimeType": "application/octet-stream",
        "buffer": b"PK\x03\x04" + b"\x00" * 64}])

    # 体检 → 确认弹窗（含字段映射 + 闸门说明）
    page.wait_for_selector("#md_body", timeout=15000)
    body = page.locator("#md_body").inner_text()
    assert "字段映射" in body
    assert "不是题目" in body, "跳过的条目要说明原因"
    assert "不会" in body and "归因" in body, "必须告知导入条目不参与归因"
    assert calls and calls[0]["dry"] is True, "应先做 dry_run 体检"

    # 确认 → 真导入（第二次请求不带 dry_run）
    page.click("#md_ok")
    page.wait_for_function(
        "() => { const t = document.getElementById('toasts');"
        " return t && t.innerText.includes('已导入'); }", timeout=15000)
    assert len(calls) == 2 and calls[1]["dry"] is False


def test_apkg_import_empty_package_warns(page, server_url):
    """包里没有可导入题目：明确告知，**不进确认流程**（避免用户点了确认却什么都没导）。"""
    def handler(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps({
            "total": 2, "gated": 0, "ungated": 0, "created": 0, "skipped": 2,
            "errors": [], "meta": {"models": {"MedKit 记忆卡": ["正面", "背面", "类型", "知识点"]}},
            "warnings": ["note 1：不是题目（模型「MedKit 记忆卡」）——已跳过"]},
            ensure_ascii=False))

    page.route("**/api/errors/import/apkg*", handler)
    page.on("dialog", lambda d: d.accept())          # 体检结果用原生 alert 展示
    _goto_mistakes_no_wait(page, server_url)
    page.set_input_files("#mk_apkg", files=[{
        "name": "mem.apkg", "mimeType": "application/octet-stream",
        "buffer": b"PK\x03\x04" + b"\x00" * 64}])
    page.wait_for_function(
        "() => { const t = document.getElementById('toasts');"
        " return t && t.innerText.includes('没有可导入'); }", timeout=15000)
    assert page.locator("#md_body").count() == 0 or not page.locator("#md_body").is_visible()
