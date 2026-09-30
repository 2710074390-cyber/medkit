"""EP-01 图像录入：拍照录入卡片的浏览器用例。

零 LLM / 零网络：所有外部调用都用 `page.route` 注入确定性响应
（能力探测、SSE 识别流），故不依赖模型与 MinerU 是否可用。

覆盖：
- 卡片在「错题本」视图内，能力横幅按 `capability` 渲染（视觉 / OCR 两种口径文案不同）；
- **闸门前置**：`把握程度` + `当时的想法` 未填时「开始识别并归因」必须 disabled，
  填齐才可用（这是"图片入口不放宽闸门"的用户可见证据）；
- **SSE 流式**：`stage` 进阶段日志、`delta` 进增量区、`fields` 渲染识别结果、
  `done` 收尾并提示；
- **失败要有 error 帧**：识别失败时展示各通道原因，而不是静默或只转圈；
- 窄屏（390×844）不横向溢出。
"""

from __future__ import annotations

import json

CAP_VISION = {
    "vision": True, "model": "qwen-vl-max", "preferred": "vision",
    "reason": "模型 qwen-vl-max 带视觉标记，可直接读图",
    "ocr_mode": "agent", "ocr_label": "MinerU 轻量解析（免 Token，按 IP 限频）",
    "ocr_needs_network": True, "max_vision_bytes": 8388608,
}

CAP_OCR = {
    "vision": False, "model": "deepseek-v4-flash", "preferred": "ocr",
    "reason": "模型 deepseek-v4-flash 未带视觉标记——无法确认支持图像输入，将走 OCR 兜底",
    "ocr_mode": "agent", "ocr_label": "MinerU 轻量解析（免 Token，按 IP 限频）",
    "ocr_needs_network": True, "max_vision_bytes": 8388608,
}

FIELDS = {
    "question": "3. 患儿男，6 岁，发热咳嗽 5 天，双肺闻及中细湿啰音。",
    "options": ["A. 急性支气管炎", "B. 支气管肺炎"],
    "answer": "B", "user_answer": "A", "analysis": "双肺固定中细湿啰音是肺炎典型体征。",
    "subject": "儿科学", "chapter": "", "topic": "",
    "answer_from_image": True, "user_answer_from_image": True,
    "legible": True, "uncertain": ["options"], "notes": "",
}


def _sse(*frames: tuple[str, dict]) -> str:
    out = ""
    for ev, data in frames:
        out += f"event: {ev}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return out


def _stub_capability(page, payload):
    page.route("**/api/errors/image/capability*", lambda route: route.fulfill(
        status=200, content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False)))


def _stub_intake_stream(page, body: str):
    page.route("**/api/errors/intake/image*", lambda route: route.fulfill(
        status=200, content_type="text/event-stream", body=body))


def _open_mistakes(page):
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('#learnnav button[data-lv="mistakes"]')
    page.wait_for_selector("#lv-mistakes.show", timeout=15000)


def test_photo_card_present_and_capability_banner(page, server_url):
    """卡片在错题本视图内；横幅按能力口径渲染，并显示识别方式徽标。"""
    _stub_capability(page, CAP_VISION)
    page.goto(server_url)
    _open_mistakes(page)
    page.wait_for_function(
        "() => { const el = document.getElementById('mi_cap_hint');"
        " return el && el.innerText.includes('原生视觉'); }", timeout=15000)
    assert page.locator("#mi_cap").inner_text().strip() == "原生视觉"
    assert page.locator("#mi_drop").count() == 1
    assert page.locator("#mi_go").is_disabled(), "闸门未填时应禁用"


def test_photo_card_ocr_fallback_wording(page, server_url):
    """模型不支持视觉时，横幅必须**明说**会走 OCR 兜底（而不是让用户提交后吃报错）。"""
    _stub_capability(page, CAP_OCR)
    page.goto(server_url)
    _open_mistakes(page)
    page.wait_for_function(
        "() => { const el = document.getElementById('mi_cap_hint');"
        " return el && el.innerText.includes('OCR'); }", timeout=15000)
    txt = page.locator("#mi_cap_hint").inner_text()
    assert "未确认支持图像输入" in txt
    assert "MinerU" in txt, "应告知 OCR 具体走哪条通道"
    assert page.locator("#mi_cap").inner_text().strip() == "OCR"


def test_gate_blocks_until_both_fields_filled(page, server_url):
    """**闸门前置**：两项都填齐前按钮禁用；填齐后才可点（图片入口不放宽闸门）。"""
    _stub_capability(page, CAP_VISION)
    page.goto(server_url)
    _open_mistakes(page)

    btn = page.locator("#mi_go")
    assert btn.is_disabled()
    page.select_option("#mi_conf", "3")
    assert btn.is_disabled(), "只填把握程度还不够"
    page.fill("#mi_reason", "觉得是支气管炎")
    page.wait_for_function(
        "() => !document.getElementById('mi_go').disabled", timeout=5000)
    assert "可以开始识别" in page.locator("#mi_gate_hint").inner_text()


def test_stream_renders_stages_fields_and_done(page, server_url):
    """SSE 全链路：stage 进日志、delta 进增量区、fields 渲染结果、done 收尾。"""
    _stub_capability(page, CAP_VISION)
    _stub_intake_stream(page, _sse(
        ("stage", {"name": "recognize", "label": "识别图片…"}),
        ("stage", {"via": "vision", "label": "原生视觉模型读图…"}),
        ("delta", {"text": '{"question": "3. 患儿男'}),
        ("fields", {"via": "vision", "fields": FIELDS, "warnings": []}),
        ("stage", {"name": "kp_align", "label": "知识点 ID 对齐"}),
        ("done", {"via": "vision", "fields": FIELDS, "card": {"id": "m1"},
                  "stages": {}, "warnings": [], "gate_ok": True, "attributed": True}),
    ))
    page.goto(server_url)
    _open_mistakes(page)
    page.select_option("#mi_conf", "4")
    page.fill("#mi_reason", "只记得湿啰音")

    # 直接把文件喂给隐藏 input（等价于拖拽/粘贴后的状态）
    page.set_input_files("#mi_file", files=[{
        "name": "q.png", "mimeType": "image/png",
        "buffer": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32}])
    page.wait_for_function(
        "() => !document.getElementById('mi_go').disabled", timeout=5000)
    page.click("#mi_go")
    page.wait_for_function(
        "() => { const el = document.getElementById('mi_progress');"
        " return el && el.innerText.includes('已入库'); }", timeout=15000)

    prog = page.locator("#mi_progress").inner_text()
    assert "识别图片" in prog
    assert "原生视觉模型读图" in prog
    assert "知识点 ID 对齐" in prog
    # delta 增量文本确实被显示（等待可感知）
    assert "患儿男" in page.locator("#mi_raw").inner_text()
    # 识别结果渲染
    res = page.locator("#mi_result").inner_text()
    assert "支气管肺炎" in res and "儿科学" in res
    assert "待核对" in res, "uncertain 里的字段应被标注出来"
    assert page.locator("#toasts .toast.bad").count() == 0


def test_recognition_failure_shows_error_frame(page, server_url):
    """识别失败：展示后端给出的原因 + 各通道结果，且不产生"成功"提示。"""
    _stub_capability(page, CAP_VISION)
    _stub_intake_stream(page, _sse(
        ("stage", {"via": "vision", "label": "原生视觉模型读图…"}),
        ("error", {"msg": "识别失败（没有可用的识别通道）",
                   "attempts": [{"via": "vision", "ok": False, "error": "超时"},
                                {"via": "ocr", "ok": False, "error": "网络不可达"}]}),
    ))
    page.goto(server_url)
    _open_mistakes(page)
    page.select_option("#mi_conf", "2")
    page.fill("#mi_reason", "猜的")
    page.set_input_files("#mi_file", files=[{
        "name": "q.png", "mimeType": "image/png",
        "buffer": b"\x89PNG\r\n\x1a\n" + b"\x00" * 32}])
    page.wait_for_function("() => !document.getElementById('mi_go').disabled", timeout=5000)
    page.click("#mi_go")
    page.wait_for_function(
        "() => { const el = document.getElementById('mi_progress');"
        " return el && el.innerText.includes('失败'); }", timeout=15000)

    prog = page.locator("#mi_progress").inner_text()
    assert "没有可用的识别通道" in prog
    assert "超时" in prog and "网络不可达" in prog, "各通道原因应逐条展示"
    # 失败时不应渲染"识别结果"卡片
    assert page.locator("#mi_result .mi-card").count() == 0


def test_photo_card_narrow_no_overflow(page, server_url):
    """390×844 窄屏：投放区/进度日志不撑破页面。"""
    _stub_capability(page, CAP_OCR)
    page.goto(server_url)
    page.set_viewport_size({"width": 390, "height": 844})
    _open_mistakes(page)
    page.wait_for_function(
        "() => { const el = document.getElementById('mi_cap_hint');"
        " return el && el.innerText.includes('OCR'); }", timeout=15000)

    overflow = 999
    for _ in range(8):
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
        if overflow <= 2:
            break
    assert overflow <= 2, f"拍照录入卡片横向溢出 {overflow}px（390px 视口）"
