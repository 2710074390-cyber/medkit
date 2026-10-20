"""IMP-05：大纲管理「粘贴导入 → 解析预览 → 确认入库」+「教师重点文件自动导入」浏览器用例。

流程（对齐 index.html 的 sylPaste / sylParse / sylParseConfirm / sylSetStd）：
1. 打开「设置 → 进阶功能 → 大纲管理」（v0.19 IA 收敛后从「学习中心」迁入，随出题工坊开关显隐）；
2. 触发「粘贴导入」（sylPaste）显示粘贴卡；
3. 填入「一、呼吸系统 / 1、肺通气 / 2、肺炎」→ 触发「解析预览」（sylParse）→ 断言草稿列表
   出现（预览「肺通气」「肺炎」两条）；
4. 触发「确认入库（当前科目）」（sylParseConfirm）→ 确认入库条目 source='teacher'（v4 二选一
   模型：用户自供内容统一为教师重点）→ 默认标准即「教师重点」，等待章树渲染「呼吸系统」。

用例二：教师重点文件（md）上传 → /api/syllabus/teacher/import-file 自动处理全流程
（解析 → 结构化 → 知识点提取 → 幂等入库），断言预览含草稿与「知识点提取」摘要。

v4 说明（相对早期版本）：
- 大纲标准二选一：teacher（教师重点，默认）/ seed（官方大纲）；历史「全部」档已移除（all 仅
  存在于旧版，测试不再依赖）；
- 粘贴/文件导入的条目 source 统一为 'teacher'（迁移 v4 归一），故确认入库后无需切换标准即可
  在默认「教师重点」下看到章树；
- api() 已修复：字符串体自动补 Content-Type: application/json（旧测试缝移除）。

驱动方式说明：v0.19 起本视图不再是 `.learnview`（无 `.show` 激活类），而是「设置」页里
`details.adv` 内的普通区块——判据改为「可见 + #syl_body 已渲染」；仍保留 page.evaluate
调用同名处理函数的写法，与真实点击等价并规避视口滚动问题。
"""
from __future__ import annotations

SYL_TEXT = "一、呼吸系统\n1、肺通气\n2、肺炎"


def _goto_syllabus(page, server_url: str):
    """进入「设置 → 进阶功能 → 大纲管理」。

    conftest 的 page fixture 预置出题工坊为开，故 `#lv-syllabus` 在场；
    `details.adv` 由 applyFeatures 自动展开，这里再显式 open 一次以消除竞态。
    """
    page.goto(f"{server_url}/#mine")
    page.wait_for_selector("#tab-mine.show", timeout=15000)
    page.evaluate(
        "() => { const d = document.querySelector('#lv-syllabus')"
        "?.closest('details.adv'); if (d) d.open = true; }")
    page.wait_for_selector("#lv-syllabus", state="visible", timeout=15000)
    page.wait_for_selector("#syl_body", timeout=15000)


def _wait_tree(page, needle: str, std: str = "teacher"):
    """兜底：初始 sylLoad（含 sync-teacher，耗时不定）可能迟到覆盖 → 收敛循环重触发。"""
    for _ in range(10):
        txt = page.locator("#syl_body").inner_text()
        if needle in txt:
            return
        page.evaluate(f"() => sylSetStd('{std}')")
        page.wait_for_timeout(1000)
    raise AssertionError(f"章树未渲染出「{needle}」（sylLoad 竞态重试耗尽）")


def test_syllabus_paste_parse_confirm(page, server_url):
    _goto_syllabus(page, server_url)

    # 标准二选一：std 切换器仅 teacher / seed 两档（无「全部」）
    pills = page.evaluate(
        "() => [...document.querySelectorAll('#syl_std .css-pill')].map(b => b.dataset.std)")
    assert sorted(pills) == ["seed", "teacher"], f"标准档位应为两档: {pills!r}"

    # 1) 粘贴导入 -> 显示粘贴卡
    page.evaluate("() => sylPaste()")

    # 2) 填入 + 解析预览
    page.evaluate("(t) => { document.getElementById('syl_paste_text').value = t; }", SYL_TEXT)
    page.evaluate("() => sylParse()")

    preview_text = page.locator("#syl_paste_preview").inner_text()
    assert "肺通气" in preview_text, f"解析预览缺少「肺通气」: {preview_text!r}"
    assert "肺炎" in preview_text, f"解析预览缺少「肺炎」: {preview_text!r}"
    assert "预览 2 条" in preview_text, f"解析预览条数异常: {preview_text!r}"

    # 3) 确认入库（source='teacher'，v4）→ 默认「教师重点」标准下等待章树渲染「呼吸系统」
    page.evaluate("() => sylParseConfirm()")
    page.wait_for_function(
        "() => !(document.getElementById('syl_body') || {innerText:''}).innerText.includes('计算覆盖度')",
        timeout=15000,
    )
    _wait_tree(page, "呼吸系统")
    assert "呼吸系统" in page.locator("#syl_body").inner_text()


def test_syllabus_teacher_file_import(page, server_url, tmp_path):
    """教师重点文件（md）→ teacher/import-file 自动处理全流程（解析→结构化→知识点提取→入库）。"""
    _goto_syllabus(page, server_url)
    md = tmp_path / "teacher_shiyan.md"
    md.write_text("一、呼吸系统\n1、肺通气\n2、肺炎\n3、肺结核", encoding="utf-8")

    # 隐藏 input 直接注入文件 → onchange 触发 sylTeacherImport
    page.locator("#syl_teacher_file").set_input_files(str(md))
    page.wait_for_function(
        "() => document.getElementById('syl_paste_preview').innerText.includes('教师重点草稿')",
        timeout=20000)
    pv = page.locator("#syl_paste_preview").inner_text()
    assert "肺通气" in pv and "肺结核" in pv, f"教师重点草稿缺失: {pv!r}"
    assert "知识点提取" in pv, f"知识点提取摘要缺失: {pv!r}"

    # ⚠️ 必须显式确认入库（2026-10-12 三引擎实测修）：导入是**两段式**（preview=1 只解析不落库，
    # 用户点「确认全部入库」才落库）。此前本用例只断言预览就直接等章树 ⇒ 它其实**靠同文件前一个
    # 用例（`test_syllabus_paste_parse_confirm`）已把「呼吸系统」写进同一个 session 级后端**才绿——
    # 单跑本用例在 chromium / firefox / webkit **三引擎全红**（顺序依赖，不是跨浏览器差异）。
    page.evaluate("() => sylParseConfirm()")
    _wait_tree(page, "呼吸系统")
    assert "呼吸系统" in page.locator("#syl_body").inner_text()
