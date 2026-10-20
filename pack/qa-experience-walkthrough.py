# -*- coding: utf-8 -*-
"""医学生视角体验质检：隔离环境里把主路径走一遍，逐步截图 + 收 console 报错 + 打印摩擦点。

**绝不触碰真实 ~/.medkit**：全程走 tests/browser/server_launcher.py（它把 CONFIG_DIR /
projects_dir / LIBRARY_DIR / DB_PATH 全部重定向到临时 home）。

用法：python pack/qa-experience-walkthrough.py [截图输出目录]\n\n产出：截图若干 + stdout 摩擦点清单（供 `docs/体验质检_医学生视角_2026-10-10.md` 复核）。
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")  # CI/Windows：中文输出必须强 UTF-8

ROOT = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("TEMP", "/tmp")) / "medkit_qa_home"
SHOTS = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else Path(os.environ.get("TEMP", "/tmp")) / "medkit_qa_shots"
)
SHOTS.mkdir(parents=True, exist_ok=True)


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


# ---- 医学生自备题库（执业医师风格；题型覆盖 A1/A2/B1/X） ----
BANK_MD = """# 内科学·呼吸与循环（执业医师冲刺）

### 1. A1 型 · 单选 · 记忆
**慢阻肺的诊断**
慢阻肺（COPD）最主要的致病因素是
- A. 吸烟
- B. 感染
- C. 空气污染
- D. 遗传因素
- E. 过敏原
**答案：A**
**解析：** 吸烟是 COPD 最重要的环境致病因素，吸烟者患病率显著高于非吸烟者。职业粉尘与生物燃料烟雾为次要因素。

### 2. A2 型 · 单选 · 应用
**慢阻肺的诊断**
男性，62 岁，吸烟 40 年，慢性咳嗽咳痰 10 年，近 1 年活动后气促。肺功能：FEV1/FVC 62%，FEV1 占预计值 55%。最可能的诊断是
- A. 支气管哮喘
- B. 慢性阻塞性肺疾病
- C. 支气管扩张
- D. 肺结核
- E. 肺癌
**答案：B**
**解析：** 吸入支气管扩张剂后 FEV1/FVC < 70% 是诊断 COPD 的必备条件（GOLD 标准）。本例 62% < 70%，且 FEV1 55% 属 GOLD 2 级。

### 3. X 型 · 多选 · 应用
**心衰治疗**
下列属于慢性射血分数降低型心衰（HFrEF）"新四联"基石治疗的是
- A. ARNI/ACEI
- B. β 受体阻滞剂
- C. 醛固酮受体拮抗剂
- D. SGLT2 抑制剂
- E. 地高辛
**答案：ABCD**
**解析：** HFrEF 的"新四联"为 ARNI/ACEI、β 阻滞剂、MRA、SGLT2i，可显著降低死亡率。地高辛仅改善症状、不降低死亡率，不属基石。

### 4. B1 型 · 共用选项
**急性冠脉综合征**
- A. ST 段抬高型心肌梗死
- B. 非 ST 段抬高型心肌梗死
- C. 不稳定型心绞痛
- D. 稳定型心绞痛
**4.1** 患者胸痛 2 小时，心电图 V1–V4 导联 ST 段抬高，肌钙蛋白升高。诊断是
**答案：A**
**解析：** 持续性 ST 段抬高 + 心肌损伤标志物升高 = STEMI，需尽早再灌注。

**4.2** 患者胸痛 30 分钟，心电图 ST 段压低 0.2mV，肌钙蛋白正常。诊断是
**答案：C**
**解析：** 缺血性胸痛 + ST 段压低但肌钙蛋白正常 = 不稳定型心绞痛（NSTEMI 需肌钙蛋白升高）。

### 5. A1 型 · 单选 · 记忆
**糖尿病诊断**
空腹血糖达到下列哪项即可诊断糖尿病
- A. ≥ 5.6 mmol/L
- B. ≥ 6.1 mmol/L
- C. ≥ 7.0 mmol/L
- D. ≥ 7.8 mmol/L
- E. ≥ 11.1 mmol/L
**答案：C**
**解析：** 空腹血糖 ≥ 7.0 mmol/L，或 OGTT 2h 血糖 ≥ 11.1 mmol/L，或随机血糖 ≥ 11.1 mmol/L 伴典型症状，即可诊断。
"""

PROJECT_QS = [
    {
        "id": "q1",
        "type": "A1",
        "bloom": "记忆",
        "subtopic": "急性胰腺炎",
        "question": "急性胰腺炎最常见的病因（我国）是",
        "options": ["胆道疾病", "大量饮酒", "高脂血症", "创伤", "药物"],
        "answer": "A",
        "analysis": "我国以胆道疾病（胆石症）最常见，西方以酒精为主。",
    },
    {
        "id": "q2",
        "type": "A1",
        "bloom": "记忆",
        "subtopic": "急性胰腺炎",
        "question": "急性胰腺炎时提示病情危重的体征是",
        "options": ["Murphy 征", "Grey-Turner 征", "Courvoisier 征", "Blumberg 征", "Babinski 征"],
        "answer": "B",
        "analysis": "Grey-Turner 征（腰胁部瘀斑）与 Cullen 征提示出血坏死性胰腺炎。",
    },
]


def seed(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    d = home / "projects" / "proj_qa_pancreas" / "最终产物"
    d.mkdir(parents=True, exist_ok=True)
    (home / "projects" / "proj_qa_pancreas" / "meta.json").write_text(
        json.dumps({"subject": "内科学·消化", "pid": "proj_qa_pancreas"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (d / "questions_final.json").write_text(
        json.dumps(PROJECT_QS, ensure_ascii=False), encoding="utf-8"
    )


class Walk:
    def __init__(self, page, base: str):
        self.page = page
        self.base = base
        self.log: list[dict] = []
        self.console: list[str] = []
        self.errors: list[str] = []
        page.on(
            "console",
            lambda m: (
                self.console.append(f"[{m.type}] {m.text}")
                if m.type in ("error", "warning")
                else None
            ),
        )
        page.on("pageerror", lambda e: self.errors.append(str(e)))

    def shot(self, name: str, note: str = "") -> None:
        self.page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=False)
        self.log.append({"step": name, "note": note})

    def goto(self, hash_: str = "") -> None:
        self.page.goto(f"{self.base}/{hash_}")
        self.page.wait_for_timeout(700)

    def txt(self, sel: str) -> str:
        loc = self.page.locator(sel)
        return loc.inner_text() if loc.count() else "(缺失)"

    def overflow(self) -> tuple[int, int]:
        return self.page.evaluate("() => [document.documentElement.scrollWidth, window.innerWidth]")


def main() -> None:
    shutil.rmtree(HOME, ignore_errors=True)  # 每次从干净状态走
    seed(HOME)
    port = free_port()
    env = dict(os.environ, MEDKIT_NO_BROWSER="1", MEDKIT_PORT=str(port))
    proc = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "tests" / "browser" / "server_launcher.py"),
            str(port),
            str(HOME),
        ],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            br = p.chromium.launch(headless=True)
            ctx = br.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
            page = ctx.new_page()
            w = Walk(page, base)

            # 等服务器起来
            for _ in range(60):
                try:
                    page.goto(base, timeout=2000)
                    break
                except Exception:
                    time.sleep(0.5)
            page.wait_for_selector("#tab-start.show", timeout=15000)

            # ===== S0 首启：直接落在「开始」页，无任何遮罩（v0.15 已移除向导 + 模式门）=====
            w.shot("s00_first_run", "真首启：无遮罩，直接可用")
            overlays = page.evaluate(
                """() => ['#modegate', '#wizard_mask', '#modebar']
                     .filter(s => document.querySelector(s))"""
            )
            print("S0 残留首启遮罩：", overlays or "(无)")
            print(
                "S0 侧栏页签：",
                page.evaluate(
                    """() => [...document.querySelectorAll('nav button[data-tab]')]
                     .filter(b => b.style.display !== 'none').map(b => b.innerText.trim())"""
                ),
            )

            # ===== S2 进入「刷题」页（无题库空态） =====
            page.click('button[data-tab="study"]')
            page.wait_for_selector("#tab-study.show", timeout=15000)
            page.wait_for_timeout(900)
            w.shot("s02_drill_empty", "刷题页（无题库）")
            print("S2 题库空态：", w.txt("#bank_list").replace("\n", " | ")[:200])

            # ===== S3 粘贴导入 → 预览 =====
            page.click('button:has-text("粘贴文本导入")')
            page.wait_for_selector("#bank_paste_text", state="visible", timeout=15000)
            page.fill("#bank_paste_text", BANK_MD)
            w.shot("s03_paste", "粘贴文本导入（医学题库 md）")
            page.click('button:has-text("解析预览")')
            page.wait_for_selector("#bank_import_body .bank-preview-table", timeout=15000)
            w.shot("s04_preview", "解析预览表")
            print("S3 预览摘要：", w.txt("#bank_import_meta"))
            rows = page.locator("#bank_import_body .bank-preview-table tbody tr").count()
            print("S3 预览行数：", rows, "（原文 6 题：A1×3 + X×1 + B1×2）")
            dump = page.evaluate(
                """async (md) => {
                     const r = await (await fetch('/api/bank/import/preview', {
                       method: 'POST', headers: {'Content-Type': 'application/json'},
                       body: JSON.stringify({text: md, fmt: 'md'})})).json();
                     return (r.items || []).map(i => ({
                       q: (i.question || '').slice(0, 46),
                       type: i.type, ans: i.answer,
                       opts: (i.options || []).length,
                       ana: (i.analysis || '').slice(0, 30),
                       sub: i.subtopic || '',
                     }));
                   }""",
                BANK_MD,
            )
            print("S3 逐题 dump（原文块：1/2/3/B1组头+4.1+4.2/5）：")
            for i, it in enumerate(dump, 1):
                print(f"   {i}. {it}")

            # ===== S4 入库 → 列表 =====
            page.fill("#bank_new_name", "内科学·呼吸与循环")
            page.click("#btn_bank_commit")
            page.wait_for_selector("#bank_list .bank-item", timeout=15000)
            w.shot("s05_bank_list", "题库列表（1 个题库）")
            print("S4 题库行：", w.txt("#bank_list").replace("\n", " | ")[:220])

            # ===== S5 开始练习 → 答题 → 判分 =====
            page.click('.bank-item button:has-text("开始练习")')
            page.wait_for_selector(".bank-q", timeout=15000)
            w.shot("s06_drill_q", "练习页第 1 题")
            print("S5 第 1 题题干：", w.txt(".bank-q")[:80])
            page.click("#bopt_1")  # B = 错（正确答案 A）
            page.click("#btn_bank_submit")
            page.wait_for_selector(".bank-ans", timeout=15000)
            w.shot("s07_graded_wrong", "判错 + 解析")
            print("S5 判错文案：", w.txt(".bank-ans").replace("\n", " | ")[:260])

            # 连续答几题，造出「今日已练」与错题
            for _ in range(4):
                page.click('#bank_drill_body .bank-bar button:has-text("全部")')
                page.wait_for_selector(".bank-q", timeout=15000)
                page.click("#bopt_0")
                page.click("#btn_bank_submit")
                page.wait_for_timeout(400)
                page.click('button:has-text("下一题")')
                page.wait_for_timeout(300)
            w.shot("s08_goal", "每日目标进度")
            print("S5 每日目标：", w.txt("#bank_goal_prog"))

            # ===== S6 错题（一级页签：左栏错题助手 + 右栏薄弱项/今日复习/错题列表） =====
            page.click('button[data-tab="learn"]')
            page.wait_for_selector("#tab-learn.show", timeout=15000)
            page.wait_for_timeout(1200)
            w.shot("s09_mistakes", "错题页（错题助手 + 错题列表两栏）")
            print("S6 错题行数：", page.locator("#vault_list .vault-row").count())
            print(
                "S6 首行内容：",
                page.locator("#vault_list .vault-row").first.inner_text().replace("\n", " | ")[:200]
                if page.locator("#vault_list .vault-row").count()
                else "(空)",
            )

            # ===== S7 错题重练直达 =====
            page.click('button[data-tab="study"]')
            page.wait_for_selector("#tab-study.show", timeout=15000)
            page.locator('button:has-text("错题重练")').first.click()
            page.wait_for_selector(".bank-q", timeout=15000)
            w.shot("s10_retry", "「错题重练」直达刷题页")
            print("S7 重练页标题：", w.txt("#bank_drill_title"))
            print("S7 模式条：", w.txt("#bank_drill_body .bank-bar").replace("\n", " | ")[:160])

            # ===== S8 题库（出题工坊） =====
            page.click('button[data-tab="bank"]')
            page.wait_for_selector("#tab-bank.show", timeout=15000)
            page.wait_for_timeout(1200)
            w.shot("s11_gen_mode", "题库（出题工坊）")

            # ===== S9 从项目导入 =====
            page.click('button[data-tab="study"]')
            page.wait_for_selector("#tab-study.show", timeout=15000)
            page.wait_for_timeout(600)
            page.click('button:has-text("从出题项目导入")')
            page.wait_for_selector("#modal_mask", state="visible", timeout=15000)
            page.wait_for_timeout(800)
            w.shot("s12_project_import", "从出题项目导入（弹层）")
            print("S9 弹层内容：", w.txt("#md_body").replace("\n", " | ")[:220])

            # ===== S10 管理题目（编辑/收藏/笔记） =====
            page.click("#md_cancel")
            page.wait_for_timeout(400)
            page.click('.bank-item button:has-text("管理题目")')
            page.wait_for_selector("#bank_item_list .bank-preview-table", timeout=15000)
            w.shot("s13_manage", "题目管理")
            print("S10 管理表头：", w.txt("#bank_item_list thead").replace("\n", " | "))
            page.click('#bank_item_list button:has-text("编辑")')
            page.wait_for_selector("#bi_q", timeout=15000)
            w.shot("s14_edit_form", "编辑表单")

            # ===== S11 导出押题卷（v0.19：题库行动作收敛进 ⋯ 更多菜单） =====
            page.click("#bank_item_form button:has-text('取消')")
            page.wait_for_timeout(400)
            row = page.locator(".bank-item").first
            row.locator("details.bank-more summary").click()
            with page.expect_popup() as pop:
                row.locator('.bank-more-menu button:has-text("导出押题卷")').click()
            paper = pop.value
            paper.wait_for_load_state(timeout=15000)
            paper.screenshot(path=str(SHOTS / "s15_paper.png"))
            print("S11 押题卷 URL：", paper.url)
            print("S11 押题卷含题干：", "慢阻肺" in paper.content())
            paper.close()

            # ===== S12 窄屏（手机，390×844） =====
            page.set_viewport_size({"width": 390, "height": 844})
            page.goto(f"{base}/#study")
            page.wait_for_timeout(1200)
            w.shot("s16_mobile_drill", "窄屏 390×844 刷题页")
            sw, iw = w.overflow()
            print(
                f"S12 窄屏溢出：scrollWidth={sw} innerWidth={iw} → {'溢出!' if sw > iw + 1 else 'OK'}"
            )
            if sw > iw + 1:
                bad = page.evaluate(
                    """() => {
                         const vw = document.documentElement.clientWidth, out = [];
                         document.querySelectorAll('#tab-study *').forEach(el => {
                           const r = el.getBoundingClientRect();
                           if (r.right > vw + 1) out.push({tag: el.tagName.toLowerCase(),
                             id: el.id, cls: (el.className||'').toString().slice(0,36),
                             w: Math.round(r.width), right: Math.round(r.right),
                             txt: (el.innerText||'').slice(0,18).replace(/\\n/g,' ')});
                         });
                         return out.slice(0, 12);
                       }"""
                )
                print("S12 溢出元凶：")
                for b in bad:
                    print("   ", b)
            page.click('button[data-tab="learn"]')
            page.wait_for_timeout(1200)
            w.shot("s17_mobile_learn", "窄屏 学习中心")
            sw, iw = w.overflow()
            print(f"S12 窄屏溢出(学习中心)：scrollWidth={sw} innerWidth={iw}")

            # ===== 汇总 =====
            print("\n=== console error/warning ===")
            for c in w.console[:20] or ["(无)"]:
                print(" ", c[:200])
            print("=== pageerror ===")
            for e in w.errors[:20] or ["(无)"]:
                print(" ", e[:200])
            br.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
    print(f"\n截图目录：{SHOTS}")


if __name__ == "__main__":
    main()
