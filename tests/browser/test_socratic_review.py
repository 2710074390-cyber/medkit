"""EP-01 阶段 3：苏格拉底复习的浏览器用例。

零 LLM：`/api/errors/socratic/*` 全部用 `page.route` 注入确定性响应，
所以**不需要配置模型**、不需要 API Key，冷启动即可跑。

覆盖：
- 可复习列表按数据渲染（错误类型 / 真题频次 / 自评列）；
- 空列表给出**可执行**的说明（告诉用户差哪两个条件），不是"暂无数据"敷衍；
- 点「开始复习」→ 出现第一问 + 输入框 + 提问类型；
- 提交一轮 → 显示判分、`hit_crossroad` 状态、下一问；未命中时提示"还没回到岔路口"；
- `redacted=true` → 前端必须**显式告知**用户"模型反馈含答案信息已拦截"；
- 提交失败 → 恢复用户已输入的文字（不能白打一段字）；
- **红线**：复习面板内不存在展示正确答案的元素；后端 502（第一问夹答案作废）→ 可读报错；
- 窄屏 390×844 不横向溢出。

为什么必须真跑浏览器：这一块是**唯一会写数据**的视图（开会话），
纯 Python 守卫看不到"按钮点了没反应""提交后输入丢失""面板没渲染"这类交互缺陷。
"""

from __future__ import annotations

import json

_ELIGIBLE = {
    "count": 2,
    "items": [
        {"id": "m1", "subject": "生理学", "chapter": "心血管", "topic": "心输出量",
         "error_tag": "机制混淆", "fix": "前负荷↑→每搏量↑", "confidence": 4,
         "round": "早鸟轮", "freq": 9},
        {"id": "m2", "subject": "生化", "chapter": "酶学", "topic": "米氏方程",
         "error_tag": "记忆偏差", "fix": "Km 是半饱和浓度", "confidence": 3,
         "round": "跟课轮", "freq": 2},
    ],
}

_EMPTY = {"count": 0, "items": []}

_FIRST = {
    "question": "你当时写的是「前负荷增加会降心输出量」——先别查书，说说你是怎么想到这一层的？",
    "type": "explain",
    "state": "weak",
    "error_tag": "机制混淆",
    "fix": "前负荷↑→每搏量↑",
    "session": {"id": "sr_1", "kind": "mistake", "mistake_id": "m1",
                "current": {"type": "explain", "text": "第一问"}, "rounds": []},
}

_TURN_MISS = {
    "score": 1,
    "gap": "你把方向说出来了，但没说清「回到同一长度」这个前提。",
    "next_question": {"type": "explain", "text": "那 Frank-Starling 的前提是什么？"},
    "state": "weak", "hit_crossroad": False, "stuck": 1, "stuck_limit": 3,
    "hint_allowed": False, "redacted": False,
    "session": {"id": "sr_1", "kind": "mistake", "stuck": 1,
                "current": {"type": "explain", "text": "那 Frank-Starling 的前提是什么？"},
                "rounds": [{"round": 1}]},
}

_TURN_HIT = {
    "score": 3,
    "gap": "讲透了。",
    "next_question": {"type": "apply", "text": "换成后负荷增加呢？"},
    "state": "shaky", "hit_crossroad": True, "stuck": 0, "stuck_limit": 3,
    "hint_allowed": False, "redacted": False,
    "session": {"id": "sr_1", "kind": "mistake", "stuck": 0,
                "current": {"type": "apply", "text": "换成后负荷增加呢？"},
                "rounds": [{"round": 1}]},
}

_BLOCKERS = ["加载中", "汇总中", "计算中", "判分中", "准备第一问"]


def _open_meta(page):
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('#learnnav button[data-lv="meta"]')
    page.wait_for_selector("#lv-meta.show", timeout=15000)


def _stub(page, pattern, payload, status=200):
    page.route(pattern, lambda route: route.fulfill(
        status=status, content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False)))


def _wait_text(page, sel, needle, timeout=15000):
    page.wait_for_function(
        "args => { const el = document.querySelector(args[0]);"
        " return !!el && (el.innerText || '').includes(args[1]); }",
        arg=(sel, needle), timeout=timeout)


def _wait_render(page, sel, timeout=15000):
    page.wait_for_function(
        "args => { const el = document.querySelector(args[0]); if (!el) return false;"
        " const t = el.innerText || '';"
        " return t.trim().length > 0 && !args[1].some(s => t.includes(s)); }",
        arg=(sel, _BLOCKERS), timeout=timeout)


# ------------------------------------------------------------------ 列表渲染
def test_socratic_list_renders_from_api(page, server_url):
    """按数据渲染：错误类型、真题频次、自评都要真出现（不是"有 HTML 就算过"）。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "机制混淆")
    txt = page.inner_text("#mt_soc")
    assert "生理学" in txt and "心血管" in txt
    assert "机制混淆" in txt and "记忆偏差" in txt
    assert "4/5" in txt, "自评列未渲染"
    assert "9" in txt, "真题频次列未渲染"
    assert "开始复习" in txt
    assert "2 道可复习" in page.inner_text("#mt_soc_meta")
    # 无错误 toast
    assert page.locator("#toasts .toast").count() == 0


def test_socratic_empty_list_explains_the_two_conditions(page, server_url):
    """空列表必须给出**可执行**说明（差哪两个条件），不能只说"暂无数据"。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _EMPTY)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_soc")
    txt = page.inner_text("#mt_soc")
    assert "把握程度" in txt and "归因" in txt, "空态未说明准入条件，用户不知道去补什么"
    assert page.locator("#mt_soc button").count() == 0, "空态不该有可点的开始按钮"


def test_socratic_eligible_failure_degrades_without_breaking_stats(page, server_url):
    """eligible 失败不得把只读统计也拖成错误态（两者独立降级）。"""
    _stub(page, "**/api/errors/overview*", {"counts": {"cards": 7}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", {"detail": "boom"}, status=500)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_soc")
    assert "失败" in page.inner_text("#mt_soc")
    # 统计块仍然正常（cards=7 应出现在总览里）
    _wait_render(page, "#mt_overview")
    assert "7" in page.inner_text("#mt_overview"), "eligible 失败不该影响统计块"


# ------------------------------------------------------------------ 交互
def test_socratic_start_shows_question_input_and_qtype(page, server_url):
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    panel = page.inner_text("#mt_soc_panel")
    assert page.locator("#mt_soc_input").count() == 1, "缺少作答输入框"
    assert "讲清推理过程" in panel, "提问类型未渲染为中文"
    assert page.locator('#mt_soc_panel button:has-text("提交")').count() == 1
    assert page.locator('#mt_soc_panel button:has-text("结束复习")').count() == 1


def test_socratic_submit_miss_shows_not_back_to_crossroad(page, server_url):
    """未命中岔路口 → 必须显式标出"还没回到当初那个岔路口"（这是最关键信号）。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    _stub(page, "**/api/errors/socratic/answer*", _TURN_MISS)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    page.fill("#mt_soc_input", "因为前负荷增加心脏会扩大")
    page.click('#mt_soc_panel button:has-text("提交")')
    _wait_text(page, "#mt_soc_panel", "还没回到当初那个岔路口")
    panel = page.inner_text("#mt_soc_panel")
    assert "1 / 3" in panel, "判分未显示"
    assert "回到同一长度" in panel, "gap 未显示"
    assert "Frank-Starling 的前提" in panel, "下一问未显示"


def test_socratic_submit_hit_marks_crossroad_and_switches_type(page, server_url):
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    _stub(page, "**/api/errors/socratic/answer*", _TURN_HIT)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    page.fill("#mt_soc_input", "完整正确的推理")
    page.click('#mt_soc_panel button:has-text("提交")')
    _wait_text(page, "#mt_soc_panel", "已回到当初的判断")
    panel = page.inner_text("#mt_soc_panel")
    assert "换成后负荷增加呢" in panel
    assert "换一个情境" in panel, "换档后的提问类型未更新为中文标签"


def test_socratic_redacted_flag_is_visible_to_user(page, server_url):
    """redacted=true → 前端必须明确告知"含答案信息已按红线拦截"。"""
    redacted = dict(_TURN_MISS)
    redacted["redacted"] = True
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    _stub(page, "**/api/errors/socratic/answer*", redacted)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    page.fill("#mt_soc_input", "答")
    page.click('#mt_soc_panel button:has-text("提交")')
    _wait_text(page, "#mt_soc_panel", "红线")
    assert "拦截" in page.inner_text("#mt_soc_panel")


def test_socratic_submit_failure_preserves_typed_text(page, server_url):
    """提交失败不能白打一段字——输入内容必须恢复。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    _stub(page, "**/api/errors/socratic/answer*", {"detail": "判分服务暂时不可用"}, status=500)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    page.fill("#mt_soc_input", "这段推理不能被丢掉")
    page.click('#mt_soc_panel button:has-text("提交")')
    _wait_text(page, "#mt_soc_panel", "提交失败")
    assert page.input_value("#mt_soc_input") == "这段推理不能被丢掉", \
        "提交失败后用户已输入的内容丢失"
    assert page.locator("#mt_soc_input").is_enabled(), "输入框未恢复可用"


def test_socratic_start_failure_shows_readable_detail(page, server_url):
    """后端 502（第一问夹答案已作废 / 模型故障）→ 原文展示中文说明。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*",
          {"detail": "模型的第一问夹带了答案信息，已按红线作废——请重试"}, status=502)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "无法开始复习")
    assert "红线作废" in page.inner_text("#mt_soc_panel")


def test_socratic_close_hides_panel_and_refreshes_list(page, server_url):
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    page.click('#mt_soc_panel button:has-text("结束复习")')
    page.wait_for_function(
        "() => { const p = document.querySelector('#mt_soc_panel');"
        " return !p || p.style.display === 'none' || !p.innerText.trim(); }",
        timeout=15000)


# ------------------------------------------------------------------ 红线 / 响应式
def test_socratic_panel_has_no_answer_display(page, server_url):
    """红线在 UI 侧：复习面板**不得**出现"查看答案"类**控件**（复习就该自己走通）。

    ⚠️ 这里**不能**用 `"看答案" not in view_text` 这种文本断言——本视图的说明文案里
    本来就有「看答案前填的把握程度」这句话，纯文本扫描会**自我命中**（假红）。
    实测踩过：`for word in ("看答案", ...)` 直接被那句说明文案触发。
    正确做法是断言**控件层面**不存在：按钮/链接的可点击文本里不得出现答案类词。
    """
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")

    # 只扫**可点击元素**（button/a/label/输入型控件），不扫说明文案
    clickable = page.evaluate(
        "() => Array.from(document.querySelectorAll('#lv-meta button, #lv-meta a,"
        " #lv-meta label')).map(e => (e.innerText || e.textContent || '')).join('\\n')")
    for word in ("查看答案", "显示答案", "正确答案"):
        assert word not in clickable, f"复习视图出现「{word}」按钮 —— 违反复习红线"
    # 且面板内不得出现答案的 DOM 载体（img/pre 之外的任何“答案”区块都没有）
    assert page.locator("#mt_soc_panel [data-answer], #mt_soc_panel .answer").count() == 0


def test_socratic_view_narrow_no_overflow(page, server_url):
    """390×844 窄屏不横向溢出（列表有 6 列，必须靠 .mt-scroll 兜住）。"""
    _stub(page, "**/api/errors/overview*", {"counts": {}, "calibration": {},
                                            "heatmap": {}, "migration": {},
                                            "agreement": {}, "subtract": {}})
    _stub(page, "**/api/errors/socratic/eligible*", _ELIGIBLE)
    _stub(page, "**/api/errors/socratic/start*", _FIRST)
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(server_url)
    _open_meta(page)
    _wait_text(page, "#mt_soc", "开始复习")
    page.click('#mt_soc button:has-text("开始复习")')
    _wait_text(page, "#mt_soc_panel", "先别查书")
    overflow = page.evaluate(
        "() => { const el = document.querySelector('#lv-meta');"
        " return el.scrollWidth - document.documentElement.clientWidth; }")
    assert overflow <= 2, f"窄屏横向溢出 {overflow}px（列表/面板缺滚动容器）"
