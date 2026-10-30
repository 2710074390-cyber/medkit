# MedKit 网页版 UX 优化 Implementation Plan（目标仓库：medkit-web-contest）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在 `c:\Users\38063\Desktop\medkit-web-contest`（主要接触用户的网页版）上，按已确认的 UX 设计补齐最能被用户感知的缺口，同时保持零构建、零 CDN、经典脚本全局作用域的既有架构。

**Architecture:** 源码在 `js/*.js`、`css/*.css`，`index.html` 只引用 `assets/` 下唯一的 CSS+JS 产物。任何源码改动后必须执行 `python tools/build_bundle.py` 重建产物，否则线上页面不会更新。测试为 `node --test tests/*.test.mjs`（91 项）+ `python -m pytest tests/`（账号服务）。

**Tech Stack:** 原生 HTML/CSS/JS（共享全局作用域）、`Store`（本地数据层）、`AI`（BYOK 直连）、Playwright（人工验证）、node:test。

---

## 已有能力（不重复建设）

以下在网页版里已经实现，经代码核对确认：

- 题库卡片一点即开始、高级选项收进 `<details>`（`app.js:683-690`、`index.html:191-217`）。
- 每题自动存断点，刷新/退出后按 ids+idx+marks 精确恢复（`bank-drill.js:36-52`、`app.js:734-768`）。
- 点选项即判分，判分结果就地插到所选选项下方，答对 3 秒自动跳题可取消（`bank-drill.js:471-546,665-676`）。
- 判分后支持逐选项理由、缺失时降级为整题解析（`bank-drill-result.js:40-84`）。
- 错题 SM-2 四档复习、连续两次正确才归档、leech 处理（`store.js:383-459`）。

## 明确不在本轮范围

- 变式题 / 相似题训练：全仓无任何实现，需要新的数据结构与生成链路，另立计划。
- 虚拟滚动 / 长列表分页：涉及渲染层重构，随后续性能专项处理。
- 题目稳定 ID 迁移（现有 `科目名#序号` 位置 ID 被测试钉住）：属数据迁移工程，不在本轮。

---

## Task 1：今天页把「未完成的那组」摆成唯一主行动（已完成）

**Files:** `js/app.js`（今天页渲染）

- 新增 `sessionLeft` 计算：`Store.getSession()` 的 `total - idx`。
- 文案分支与任务单分支都优先于“今天已完成”分支，保证状态一致。
- 主行动按钮直接调 `doResume()`，不复制恢复逻辑。

**验收（已实测）**：注入一个 idx=1/3 的会话后，今天页只出现一个 `act` 主按钮「接着做」，文案「外科学（教学版） · 还剩 2 题」；点击后进入作答层并停在第 2/3 题。

## Task 2：AI 侧动作防重复提交与可恢复错误（已完成）

**Files:** `js/app.js`

- 出题 `genRun` 拆为外壳 + `_genRunCore`，`_genBusy` 单飞，按钮禁用/文案在 `finally` 恢复。
- `byokModels` / `byokTest` 用 `_byokBusy` 同口径保护（该文件此前无任何防重）。
- 出题失败改用 `showErrorBox`：说明“Key 和文本都还在”并给「再试一次」。

**约束**：`_byokBusy` 必须声明在 `let _byokProvider` 与 `function initSettingsButtons()` 之间——`tests/ai-channel.test.mjs:283` 用该区间切片加载 BYOK 代码，声明在区间外会 `ReferenceError`。

## Task 3：导入预览体检与可恢复错误（已完成）

**Files:** `js/store.js`、`js/app.js`

- `Store.previewBank` 增加 `quality`：缺答案 / 缺选项 / 缺解析 / 题型分布；去重口径不变。
- 导入前有重复、覆盖或体检项时弹确认框，逐条列出问题；纯干净文件仍直接导入，不加摩擦。
- 坏 JSON 与结构不符的提示改为“请重新选择”，并说明支持桌面版导出的单题库 JSON。

**验收（已实测）**：3 题样本（1 题无答案、1 题单选项、1 组重复题干）返回 `valid 2 / duplicates 0 / quality{missingAnswer:1, missingAnalysis:1, missingOptions:1, types:{未标:1,X:1}}`。

## Task 4：错题助手回答后给「下一步动作」（待实现）

**Files:** `js/app.js`（聊天回答渲染，`app.js:1369-1375` 附近）

要做的三件事，按此顺序，每步先补断言再改：

1. 引用 chip：正文里的 `[错题N]` 由 `mdRender` 渲染后，用后处理把纯文本替换成可点击 chip，点击打开对应错题详情。只做已存在错题的映射，不做模糊匹配。
2. 动作行：回答末尾固定一行——「重练这道题 / 加入复习 / 看这个考点」。动作数据来自回答里实际引用的错题；没有引用时不显示动作行，而不是给空按钮。
3. 失败可重试：流式失败已有「重试」；补一条：重试会替换失败气泡而不是追加（保持现有测试 `vault-chat.test.mjs:55` 的语义）。

**验收**：回答含 `[错题1]` 时出现 1 个 chip 与动作行；点击 chip 打开该错题；回答无引用时不出现动作行；`node --test tests/*.test.mjs` 全绿且 `python tools/build_bundle.py` 已重建。

## Task 5：切页焦点与 aria-busy（待实现）

**Files:** `js/app.js`（`showTab`，`app.js:182-201`）、`js/bank-drill.js`

- 切页后把焦点移到 `#main`（已有 `tabindex="-1"` 却从未被聚焦），并让页面标题进入读屏顺序。
- 作答区请求题库、AI 流式生成期间给容器加 `aria-busy="true"`，结束后移除。

**验收**：键盘 Tab 进入页面 → 点导航 → 焦点落在新页主内容；读屏播报页面切换；生成中容器 `aria-busy` 为 true。

---

## 每次改动的固定验收动作

```powershell
node --check js/app.js
python tools/build_bundle.py
node --test tests/*.test.mjs        # 期望 91 项全过
```

外加一次浏览器实测（`python server.py` + Playwright）：今天页、导入预览、作答与判分、390px 窄屏。控制台只允许出现既有的 `/api/me` 401（本机模式探测，非本次改动引入）。
