"""EP-01：元认知视图（校准曲线 / 热力图 / 迁移矩阵 / 减法清单）浏览器用例。

零 LLM：本视图**只读统计**，不触发任何模型调用，冷启动即可测。

覆盖：
- 子导航第 6 项「元认知」可切换、Alt+6 直达、刷新后记忆；
- **真实 API 渲染**：用 `page.route` 注入确定性的 overview 响应，断言四张表按数据渲染
  （而不是「有 HTML 就算过」——那对空数据也成立）；
- **空库不发请求也能优雅降级**：断言出现可读提示、无错误 toast、不白屏；
- **红线守卫**：视图内不存在任何可修改 confidence / my_reasoning 的输入控件
  （这两个字段必须看答案前填，事后不可补填）；
- 窄屏（390×844）不横向溢出。

为什么用 `page.route` 注入而不是直接依赖隔离库的数据：
过滤/聚合是本视图的核心逻辑，固定输入 → 固定输出才能断言「渲染正确」；
否则换个随机种子数据就假失败。
"""

from __future__ import annotations

import json

# 与 metacog.py 返回结构逐字段对齐的确定性样本
# 场景：自评 5 的 3 道题只对 1 道（33% << 90%）→ 必须触发过度自信预警
FAKE_OVERVIEW = {
    "calibration": {
        "buckets": [
            {"confidence": 5, "n": 3, "correct": 1, "accuracy": 0.3333,
             "expected": 1.0, "gap": -0.6667, "low_sample": False},
            {"confidence": 4, "n": 3, "correct": 2, "accuracy": 0.6667,
             "expected": 0.8, "gap": -0.1333, "low_sample": False},
            {"confidence": 3, "n": 3, "correct": 1, "accuracy": 0.3333,
             "expected": 0.6, "gap": -0.2667, "low_sample": False},
            {"confidence": 2, "n": 3, "correct": 2, "accuracy": 0.6667,
             "expected": 0.4, "gap": 0.2667, "low_sample": False},
            {"confidence": 1, "n": 0, "correct": 0, "accuracy": 0.0,
             "expected": 0.2, "gap": 0.0, "low_sample": True},
        ],
        "brier": 0.3733,
        "jol_bias": 0.0167,
        "rated": 12,
        "unrated": 2,
        "unknown_result": 0,
        "alert": True,
        "alert_msg": "自评 5 的题正确率仅 33%（<90%）：在凭直觉做题且不自知。"
                     "建议对自评 4-5 的题强制写出推理再对答案。",
    },
    "heatmap": {
        "tags": ["知识盲区", "记忆偏差", "机制混淆", "概念偷换", "审题失误", "推理跳步"],
        "subjects": ["生理学", "生化"],
        "rows": [
            {"subject": "生理学", "counts": {"知识盲区": 2, "记忆偏差": 2, "机制混淆": 3,
                                             "概念偷换": 1, "审题失误": 2, "推理跳步": 1},
             "total": 11, "top_tag": "机制混淆", "top_ratio": 0.2727,
             "advice": "本阶段主要在「机制混淆」上失分：建议把因果链画成图再背。"},
            {"subject": "生化", "counts": {"知识盲区": 0, "记忆偏差": 0, "机制混淆": 0,
                                           "概念偷换": 1, "审题失误": 0, "推理跳步": 0},
             "total": 1, "top_tag": "概念偷换", "top_ratio": 1.0, "advice": ""},
        ],
        "untagged": 3,
        "field": "auto",
    },
    "migration": {
        "chains": [
            {"kp_id": "kp1_ed0410bae453564e",
             "tags": {"早鸟轮": "机制混淆", "跟课轮": "机制混淆", "强化轮": "机制混淆"},
             "verdict": "未变", "msg": "跨 3 轮仍是「机制混淆」——修补动作无效，换方法"},
            {"kp_id": "kp1_0feecc0c1eb4fa5c",
             "tags": {"早鸟轮": "知识盲区", "跟课轮": "记忆偏差"},
             "verdict": "改善", "msg": "「知识盲区」→「记忆偏差」，方向对了（问题转向更表层）"},
            {"kp_id": "kp1_d33e5ea40319c55c",
             "tags": {"冲刺轮": "推理跳步"}, "verdict": "数据不足", "msg": "只录了一轮，看不出迁移"},
        ],
        "stuck": [
            {"kp_id": "kp1_ed0410bae453564e",
             "tags": {"早鸟轮": "机制混淆", "跟课轮": "机制混淆", "强化轮": "机制混淆"},
             "verdict": "未变", "msg": "跨 3 轮仍是「机制混淆」——修补动作无效，换方法"},
        ],
        "stuck_count": 1,
        "rounds": ["早鸟轮", "跟课轮", "强化轮"],
    },
    "agreement": {"compared": 9, "agreed": 7, "rate": 0.7778, "mismatches": []},
    "subtract": {
        "rows": [
            {"key": "生理学|消化", "subject": "生理学", "chapter": "消化", "n": 2, "rated": 2,
             "accuracy": 1.0, "freq": 1.0, "score": 2.0, "skip_this_week": True},
            {"key": "生理学|循环", "subject": "生理学", "chapter": "循环", "n": 4, "rated": 4,
             "accuracy": 0.25, "freq": 1.0, "score": 1.0, "skip_this_week": False},
        ],
        "skip": [
            {"key": "生理学|消化", "subject": "生理学", "chapter": "消化", "n": 2, "rated": 2,
             "accuracy": 1.0, "freq": 1.0, "score": 2.0, "skip_this_week": True},
        ],
        "freq_missing": True,
        "cut": 1,
    },
    "counts": {"cards": 14, "events": 12, "gated": 14, "kp_ids": 11},
}

EMPTY_OVERVIEW = {
    "calibration": {"buckets": [], "brier": None, "jol_bias": None, "rated": 0,
                    "unrated": 0, "unknown_result": 0, "alert": False, "alert_msg": ""},
    "heatmap": {"tags": [], "subjects": [], "rows": [], "untagged": 0, "field": "auto"},
    "migration": {"chains": [], "stuck": [], "stuck_count": 0, "rounds": []},
    "agreement": {"compared": 0, "agreed": 0, "rate": None, "mismatches": []},
    "subtract": {"rows": [], "skip": [], "freq_missing": True, "cut": 0},
    "counts": {"cards": 0, "events": 0, "gated": 0, "kp_ids": 0},
}

_BLOCKERS = ["加载中", "汇总中", "计算中", "汇总…"]


def _open_meta(page):
    """打开学习中心 → 切到元认知视图。"""
    page.wait_for_selector('button[data-tab="learn"]', timeout=15000)
    page.click('button[data-tab="learn"]')
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.click('#learnnav button[data-lv="meta"]')
    page.wait_for_selector("#lv-meta.show", timeout=15000)


def _stub_overview(page, payload):
    """拦 /api/errors/overview 返回确定性数据（其余请求放行）。"""
    page.route("**/api/errors/overview*", lambda route: route.fulfill(
        status=200,
        content_type="application/json",
        body=json.dumps(payload, ensure_ascii=False),
    ))


def _wait_render(page, sel, timeout=15000):
    """等某块渲染完成：占位文案（加载中/汇总中/计算中）消失。"""
    page.wait_for_function(
        "args => { const el = document.querySelector(args[0]); if (!el) return false;"
        " const t = el.innerText || '';"
        " return !args[1].some(s => t.includes(s)); }",
        arg=(sel, _BLOCKERS), timeout=timeout,
    )


# ------------------------------------------------------------------ 导航
def test_meta_view_switchable_and_remembered(page, server_url):
    """第 6 个子导航「元认知」可切换、Alt+6 直达、刷新后记忆。"""
    page.goto(server_url)
    _open_meta(page)
    btn = page.locator('#learnnav button[data-lv="meta"]')
    assert btn.get_attribute("aria-selected") == "true"
    assert page.locator("#lv-meta").evaluate("el => el.classList.contains('show')")

    # 刷新后应从 sessionStorage 恢复
    page.reload()
    page.wait_for_selector("#tab-learn.show", timeout=15000)
    page.wait_for_selector("#lv-meta.show", timeout=15000)

    # Alt+6 直达
    page.click('#learnnav button[data-lv="overview"]')
    page.wait_for_selector("#lv-overview.show", timeout=15000)
    page.keyboard.press("Alt+6")
    page.wait_for_selector("#lv-meta.show", timeout=15000)


# ------------------------------------------------------------------ 真实渲染
def test_meta_renders_calibration_from_api(page, server_url):
    """校准曲线按数据渲染：柱子数 = 桶数，过度自信预警必须出现。"""
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_cal")

    cols = page.locator("#mt_cal .mt-col")
    assert cols.count() == 5, f"应有 5 个把握程度桶，实际 {cols.count()}"
    # 过度自信预警（alert=True）必须渲染出来
    cal_text = page.locator("#mt_cal").inner_text()
    assert "过度自信预警" in cal_text, "alert=True 时应显示预警"
    assert "33%" in cal_text, "预警文案应含实际正确率"
    # Brier / JOL 偏差
    assert "0.3733" in cal_text, "应展示 Brier 分数"
    # 有效样本数进 meta
    assert "12" in page.locator("#mt_cal_meta").inner_text()


def test_meta_renders_heatmap_and_migration(page, server_url):
    """热力图按「科目 × 标签」出格，迁移矩阵按轮次出列，滞留点有提示。"""
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_heat")
    _wait_render(page, "#mt_mig")

    # 热力图：2 个科目 → 2 行；表头 6 个标签列 + 1 个行头列
    heat_rows = page.locator("#mt_heat tbody tr")
    assert heat_rows.count() == 2, f"应有 2 个科目行，实际 {heat_rows.count()}"
    assert page.locator("#mt_heat thead th").count() == 7
    # 针对性建议（advice 非空时必须渲染）
    assert "机制混淆" in page.locator("#mt_heat").inner_text()
    # 未归类提示
    assert "3" in page.locator("#mt_heat").inner_text()

    # 迁移矩阵：3 条链、3 个轮次列
    mig_rows = page.locator("#mt_mig tbody tr")
    assert mig_rows.count() == 3, f"应有 3 条 kp 链，实际 {mig_rows.count()}"
    mig_text = page.locator("#mt_mig").inner_text()
    assert "未变" in mig_text and "改善" in mig_text
    assert "没有改善" in mig_text or "换" in mig_text, "stuck_count>0 应有换方法提示"


def test_meta_renders_subtract_list(page, server_url):
    """减法清单：命中的章节进列表，并提示未导入真题考频。"""
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_sub")

    text = page.locator("#mt_sub").inner_text()
    assert "消化" in text, "skip 命中的章节应出现"
    assert "本周不排" in text
    assert "考频" in text, "freq_missing=True 应提示未导入真题考频"


def test_meta_overview_shows_counts_and_unrated_hint(page, server_url):
    """总览用 counts 口径（不是虚构的 total/rated 顶层字段），未填把握程度要有提示。"""
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_overview")

    text = page.locator("#mt_overview").inner_text()
    assert "14" in text, "错题总数应来自 counts.cards"
    assert "11" in text, "知识点数应来自 counts.kp_ids"
    assert "2" in text and "把握程度" in text, "unrated=2 应提示去填写"


# ------------------------------------------------------------------ 空态 / 降级
def test_meta_empty_state_is_readable(page, server_url):
    """空库：四块都给出可读提示，无错误 toast，不白屏。"""
    _stub_overview(page, EMPTY_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_cal")
    _wait_render(page, "#mt_heat")
    _wait_render(page, "#mt_mig")
    _wait_render(page, "#mt_sub")

    assert page.locator("#toasts .toast.bad").count() == 0, "空库不应报错"
    # 四块都不应停留在加载态
    for sel in ("#mt_overview", "#mt_cal", "#mt_heat", "#mt_mig", "#mt_sub"):
        txt = page.locator(sel).inner_text().strip()
        assert txt, f"{sel} 不应为空"


def test_meta_api_failure_degrades_gracefully(page, server_url):
    """/api/errors/overview 500 时给出可读错误，且不抛出未捕获异常。"""
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/api/errors/overview*", lambda route: route.fulfill(
        status=500, content_type="application/json", body='{"detail":"boom"}'))
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_overview")
    assert "失败" in page.locator("#mt_overview").inner_text()
    assert not errors, f"不应有未捕获的页面异常：{errors}"


# ------------------------------------------------------------------ 红线守卫
def test_meta_view_has_no_confidence_edit_control(page, server_url):
    """红线①：元认知视图**不得**提供任何补填 confidence / my_reasoning 的入口。

    这两个字段必须在看答案前填（《总纲》§3.4），事后补填会让校准曲线失去意义。
    这里从**用户可见控件**侧再堵一道（后端已有 403 + 源码级守卫）。
    """
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    _open_meta(page)
    _wait_render(page, "#mt_overview")

    scope = page.locator("#lv-meta")
    # 任何输入控件都不允许出现（该视图是纯展示页）
    for tag in ("input", "textarea", "select"):
        assert scope.locator(tag).count() == 0, \
            f"元认知视图不应含 <{tag}>（补填风险）"
    # 渲染出的文本里也不应出现「补填/修改把握程度」这类诱导
    txt = scope.inner_text()
    assert "修改把握程度" not in txt
    assert "补填" not in txt or "无法补填" in txt


# ------------------------------------------------------------------ 布局
def test_meta_view_narrow_no_overflow(page, server_url):
    """390×844 窄屏：元认知视图无横向溢出（热力图靠容器滚动，不撑破页面）。"""
    _stub_overview(page, FAKE_OVERVIEW)
    page.goto(server_url)
    page.set_viewport_size({"width": 390, "height": 844})
    _open_meta(page)
    _wait_render(page, "#mt_heat")

    overflow = 999
    for _ in range(8):
        page.wait_for_timeout(300)
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        if overflow <= 2:
            break
    assert overflow <= 2, f"元认知视图横向溢出 {overflow}px（390px 视口）"
