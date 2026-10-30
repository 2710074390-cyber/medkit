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

---

## 追加：问题台账与分阶段实施计划（2026-10-30）

本节为追加计划，以上完成记录、原始验收和待办均保留；与原文有冲突时，以本节的会话验收和首页入口要求为准。目标代码仓库仍为 `C:\Users\38063\Desktop\medkit-web-contest`；本文所在仓库不是网页版源码仓库。下面「线上已复现」依据本次浏览器走查记录；「静态证据」仅表示读到相关代码，不等于已验证完整触发链。优先级 P0 为潜在数据丢失，P1 为主流程错误，P2 为体验和可访问性。

### 问题台账

| 编号 / 优先级 | 问题与证据级别 | 代码位置 | 复现 / 验收与测试 |
|---|---|---|---|
| U01 / P1 | 首页取消「3 道示例题」是明确需求，不是缺陷实测。 | `js/app.js` `_firstRunHTML`、`doSample3`（约 253、545 行） | 全新数据和有/无题库分别打开首页；主入口进入刷题页供选择题库，不自动开始示例或指定题库；无题库仍能找到导入。补首页渲染与导航测试。 |
| U02 / P1 | 三题完成后仍显示剩 1 题且可恢复最后一道：本次线上走查已复现；具体成因是静态分析，需回归验证。 | `js/bank-drill.js` `_bankSessionSave`、`bankDrillFinish`、`bankDrillHide`/`bankDrillExit`；`js/app.js` `loadBankHome`、`renderResumeStrip`、今天页 `sessionLeft` | 三题全部判分→完成→返回首页/刷题页→刷新：均不出现续做且持久会话为空；同时检查 finish 清除、`_drillUnfinished` 改状态及 `loadBankHome` 再保存的时序，禁止完成会话复活。补状态序列及浏览器测试。 |
| U03 / P1 | `total - idx` 表示位置而非未答数量，静态代码确认；非线上单独实测。 | `js/app.js` 今天页和 `renderResumeStrip`，`js/bank-drill.js` marks | 构造乱序跳题、当前题已判分、未判分及旧会话；首页和条带均以当前卷中已判分标记计算未答数，不能因回看产生假剩余；旧格式缺 marks 时明确兼容口径。补边界测试。 |
| U04 / P0 | 恢复失败可能清空进度：静态证据 `doResume` 在 `d.items` 为空时无条件清会话，需区分加载失败和真正不存在。 | `js/app.js` `doResume`；`js/bank-drill.js` 启动加载路径；`js/store.js` 会话存储 | 模拟网络/加载失败、空范围、题库确已删除；前两者保留原始会话并可重试，确实缺失也先提示并由用户明确丢弃；检查启动过程不会覆盖原存档。补失败注入测试。 |
| U05 / P0 | 动态范围恢复可能丢原卷题：静态证据先按当前范围启动，再从结果按 ids 过滤；未证明所有数据场景。 | `js/app.js` `doResume`；`js/store.js` `getDrillItems`；`js/bank-drill.js` 启动函数 | 用未做/错题范围开始，作答改变筛选状态后恢复；原 ids 顺序、题数和 marks 保持不变。缺失部分 ID 时不可静默缩卷或覆盖原会话，提示并提供重试/明确放弃。补范围变化及缺题测试。 |
| U06 / P0 | 备份内置题库作答可能被过滤；daily、goal、考试键可能遗漏：待复现的静态推测。 | `js/store.js` `exportData`、`importPackage` 和键定义；`js/app.js` 考试计划；`tests/import-export.test.mjs` | 内置题库未预加载及已预加载两种状态下写入作答、收藏、笔记、每日进度、目标和考试，导出→干净环境导入→重载逐字段比较；另测真实不存在 ID、旧备份、迁移包和配额失败回滚，不得牺牲现有导入契约。 |
| U07 / P1 | 复习评分后撤销入口可能随到期列表消失：静态证据按钮嵌在 `renderDue` 当前题中，待操作验证。 | `js/app.js` `renderDue`、`reviewGrade`、`reviewUndo`；`js/store.js` `undoReview` | 给唯一到期题评分，列表为空后仍可撤销；撤销恢复评分前完整调度状态，二次撤销不可重复执行；同时测试多题与页面切换。 |
| U08 / P2 | 首次标记掌握提示可能与实际状态不符：静态证据调用 `resolveMistake` 后无条件提示成功，需先核实返回值和状态。 | `js/app.js` `vaultItemResolve`；`js/store.js` `resolveMistake` | 对首次和后续标记分别检查 learned、连续正确次数与提示；提示必须匹配实际状态，不改变既有间隔复习规则。补状态和文案测试。 |
| U09 / P2 | 单文件内部重复题可能静默去重：已有完成记录中的 3 题样本显示 valid 2、duplicates 0；仅为该样本的既有实测，不代表其他导入路径。 | `js/store.js` `previewBank`、`importBank`；`js/app.js` 导入预览；`tests/import-export.test.mjs`、`tests/bank-manage.test.mjs` | 输入文件内部两道相同题、与现有库重复及两者混合；预览分别报告内部重复与外部重复，确认前后数量一致，保留现有去重规则；补断言。 |
| U10 / P2 | 长题干截断可能无法展开：静态证据 `js/app.js` 到期复习题干截取 72 字、近期记录截取 40 字；需确认具体界面是否已有详情入口，不能泛指刷题题干。 | `js/app.js` `renderDue`、近期记录；必要时 `css/app.css` | 以超过 72 字题干检查到期复习及近期记录，确认能通过键盘和触屏读取完整题干（展开或已有详情入口）；短题不多余显示展开，读屏可获完整内容。 |
| U11 / P1 | 未答却显示「全部答对」：静态证据 `_finishDiagnosis` 只检查错题，未检查未答；可达性待验证。 | `js/bank-drill.js` `_finishDiagnosis`、`bankDrillFinish`、`_bankCounts`；`tests/finish-summary.test.mjs` | 通过可用路径进入零作答、部分作答无错、全答正确和有错的总结；前两者不称全部答对，显示未答数；全答正确仍显示正确结论。补四类测试并验证背题模式。 |
| U12 / P1 | 用户要求错题按科目分类，并直接刷某一科的错题；移除“先过 5 道”的入口或门槛。 | `js/app.js` 错题页面及重练入口；`js/bank-drill.js` 错题练习；`js/store.js` 错题与题库数据 | 按科目显示错题数，点击某科直接开始该科全部可用错题，不混入其他科目、不限制为 5 道；覆盖多题库同科、零错题、科目缺失、题目不可用、退出和续做；不改变错题掌握及复习数据。 |
| U13 / P1 | 用户反馈“今日复习”默认展开题目，页面过长；具体线上表现和触发状态实施前复核。 | `js/app.js` `renderDue` 及复习展开状态；必要时 `css/app.css` | 默认显示紧凑的题目摘要，题目详情按需展开及收起；完整题干、答案、评分和撤销仍可操作；大量题目时无需逐题滑过展开详情；验证手机、键盘、读屏及空列表。 |
| U14 / P1 | 用户反馈做题时选择 C 与呼出答题卡的快捷键冲突；具体绑定和触发条件待核实。 | `js/bank-drill.js` 键盘事件处理及快捷键提示；相关答题卡界面 | 核实现有绑定，保留 C 选择选项，答题卡改用不冲突的快捷键并同步界面提示；验证单选、多选、答题卡开关及输入框聚焦时不误触发。 |

### 实施顺序及验收门槛

所有下列任务均为**待实施**；仅当相应复现成立才修复潜在问题。先写可重复失败用例，确认红灯原因，再最小修改并复跑；不以静态断言代替行为测试。不做题目稳定 ID 迁移，不引入新依赖，不擅自提交。

#### 阶段 A：数据安全（U04–U06，P0）

- [ ] 在 `tests/import-export.test.mjs` 增加内置题库备份往返、daily/goal/考试、旧格式、无效 ID、存储失败回滚用例；先盘点 `js/store.js` 实际白名单和 `js/app.js` 考试键，记录导出与导入逐字段差异。验证时隔离 localStorage，禁止覆盖真实数据。
- [ ] 若 U06 成立，修改 `js/store.js`：在已知题库可验证时校验 ID；无法验证时保留原数据并明确提示，不把未加载当成不存在；扩展备份的必要键和兼容逻辑，保留迁移包空考试不清现有考试与原子回滚语义。
- [ ] 在 `tests/drill-reentry.test.mjs` 增加失败注入：读取会话原始值、使题库加载失败，断言恢复前后值一致且可重试；分别覆盖空结果和真正缺失题库。
- [ ] 覆盖 `undone`、`wrong` 和多选并集恢复：模拟状态改变后仍按原始 `ids` 取完整题并恢复顺序、marks、idx；故意缺失一个 ID，断言提示且原存档不变。若成立，调整 `js/app.js` 和 `js/bank-drill.js` 使恢复读取完整题源而非当前动态过滤结果，并避免初始化期间覆盖快照；错题源同样检查。

#### 阶段 B：会话完成与总结（U02、U03、U11）

- [ ] 在 `tests/drill-reentry.test.mjs` 构造三题完成→刷新列表→收起/退出→重新加载序列，断言 `Store.getSession()` 始终为空；确认 `_drillUnfinished`、`loadBankHome` 具体先后及其调用者，再修改 `js/bank-drill.js` / `js/app.js`，使完成、收起、主动重练等状态转换互不误存；未完成退出仍可续做。
- [ ] 统一首页和续做条带未答数：以会话 `ids` 和 `marks[id].graded` 计数；对缺失 ids/marks 的旧存档采用保守兼容策略，不假称已完成，也不静默删除；分别测试回看已答题、跳题、全部已判分及旧数据。
- [ ] 在 `tests/finish-summary.test.mjs` 覆盖 U11 四种结果及背题模式，按已答/未答事实输出总结；如果入口不允许提前交卷，仍以单元测试覆盖诊断函数且记录浏览器可达性。
- [ ] 浏览器重走线上三题路径，含答对自动跳题、手动跳转、错题重练、退出与刷新；检查首页和刷题页无虚假续做，未完成卷恢复仍正常。

#### 阶段 C：首页入口（U01）

- [ ] 修改 `js/app.js` 的首启主按钮及相关文案，跳转现有 `showTab("study")` 对应刷题选库界面；不启动题目、不随机选择题库。检查所有 `doSample3` 引用，仅在无其他调用时移除该专用入口函数，不影响其他快练。
- [ ] 扩展现有首页相关 Node 测试（先查找断言位置），覆盖有题库、无题库以及首次打开；浏览器检查点击后可选择题库、导入入口仍可用，页面无示例题入口。

#### 阶段 D：错题与导入（U07–U09、U12–U13）

- [ ] 核实科目来源及错题关联，按科目聚合并显示数量；去除“先过 5 道”，每科提供直接刷错题入口，沿用现有答题与会话机制，确保仅包含该科可用错题。覆盖同科多题库、空数据、缺失科目、题目不可用、作答及断点恢复。
- [ ] 复核“今日复习”默认展开的复现条件，改为紧凑摘要及按需展开 / 收起；展开后完整阅读题干并进行评分、撤销。验证长列表、空状态、移动端和键盘操作，避免与 U07 撤销入口及 U10 完整题干要求冲突。

- [ ] 先在 `tests/learning-loop.test.mjs` 验证评分后到期列表变化及 `undoReview` 保存字段；若 U07 成立，将撤销入口放在评分后仍存在的区域，提供一次有效撤销，并检查恢复复习调度、列表和统计。
- [ ] 测试 `resolveMistake` 首次及再次调用的真实结果；若 U08 成立，在 `js/app.js` 按实际返回状态显示提示，不改 SM-2 算法或假设首次即归档。
- [ ] 在 `tests/bank-manage.test.mjs` 和 `tests/import-export.test.mjs` 断言文件内重复、现有库重复和混合情况的预览计数与实际导入结果；若 U09 成立，修改预览及确认文案，保持数据去重语义和质量体检结果。

#### 阶段 E：长文本与无障碍（U10、原 Task 5）

- [ ] 核实到期复习和近期记录是否能读完整题干；若不能，为相应区域提供键盘可操作的展开/详情，使用 `aria-expanded` 或语义化详情元素；长短题、390px、Tab/Enter/Space 和读屏均验证，避免未经转义的 HTML。
- [ ] 继续原 Task 5：切页焦点到 `#main`，题库加载及 AI 流期间设置并清除 `aria-busy`，分别检查成功、失败和取消路径，避免与作答层关闭后的焦点返回冲突。

### 执行与交付验证

在 `C:\Users\38063\Desktop\medkit-web-contest` 执行；先运行相关新增测试，再执行以下完整验证，测试数量以实际输出为准，不把原文的 91 项视为当前保证：

```powershell
node --check js/app.js
node --check js/store.js
node --check js/bank-drill.js
node --test tests/*.test.mjs
python -m pytest tests/
python tools/build_bundle.py
python tools/build_bundle.py --check
```

`tools/build_bundle.py` 已核实：正常构建会改写 `index.html`、`assets/asset-manifest.json`、生成哈希 CSS/JS 并删除旧哈希产物；`--check` 检查引用、产物及源标记。将构建产物纳入未来实现的变更审查，先确认不会覆盖他人未提交产物。通过 `python server.py` 启动本地服务后按上述步骤进行浏览器实测，记录环境、复现步骤、结果和失败项；不要把计划或静态推测标成已完成或线上已修复。本次仅追加计划，不运行构建、不改源码、不提交。

### 执行记录（2026-10-30，基线 `0e29d9b`）

已在 `C:\Users\38063\Desktop\medkit-web-contest` 实施并通过自动化验证，未提交：

- **U01** 首启主入口改为「选择题库开始刷题」，不再启动示例题。
- **U02/U03** 完成态不再复活会话；首页与续做条带按 `marks[id].graded` 统计剩余，不再用 `total - idx`。
- **U04/U05** `doResume` 改为按原始 `ids` 从完整题源重建，恢复期间禁止临时态覆盖存档；加载失败、缺题时保留原会话并提示重试，不再静默清除。
- **U06** 备份补齐目标、每日进度、今日完成标记及实际考试键；修复内置题库作答被误剔除、旧备份缺考试字段会清空现有计划、同名用户题库被静默覆盖未报告、回滚失败仍谎称「原数据未动」。
- **U07/U08** 评分后可撤销；首次标记掌握提示与实际 `learned` 一致。
- **U11** 总结按已答/未答事实输出，未答不再显示「全部答对」；保留原有提前交卷行为。
- **U12** 错题按科目聚合、显示数量并可直接练该科全部可用错题，练习范围随会话保存与恢复；移除「先过 5 道」。
- **U13/U10** 今日复习默认折叠，展开后显示完整题干、评分与撤销可用。
- **U14** `C` 保留选择第三项，答题卡改为 `F8`，界面提示同步。
- **U09** 导入预览把「文件内重复」（只保留第一条）与「与现有题库重复」分开计数，确认框和导入后提示分别说明；`duplicates` 仍为外部重复，新增 `internalDuplicates` / `externalDuplicates`。补 `tests/learning-loop.test.mjs` 混合场景断言（6 题样本：内部重复 2、外部重复 1、新增 2）。
- **阶段 E** 复核确认切页焦点（`showTab` → 面板 `tabindex=-1` 并聚焦）与 `aria-busy`（作答加载、AI 生成/流式、聊天）已在源码中生效，本次浏览器实测验证通过。

验证结果：`node --test tests/*.test.mjs` 134 通过；`python -m pytest tests/ -q` 25 通过；`python tools/build_bundle.py --check` 一致；三个源文件 `node --check` 通过。打包产物已重建（`medkit.522637a43a.css` + `medkit.1dbff31a4c.js`）。

浏览器实测（`python server.py` + `tools/ux-check.mjs` / 390×844 窄屏脚本，127.0.0.1:8000）：题库 12 科目 / 2450 题加载正常；今天页显示未完成会话「接着做」；刷题→判分→错题页→设置页闭环无异常；390px 今天/错题/作答页均无横向溢出；切页后焦点落在 `#tab-learn`；作答加载期间 `#bank_drill_body` 的 `aria-busy` 为 `true`，加载完为 `false` 且选项正常渲染。控制台仅有 `/api/me` 404（本机模式探测）与 `favicon.ico` 404。

**仍未完成**：错题助手回答后的引用 chip 与动作行（原 Task 4）。已知限制：错题练习只含有选项、可判分的题目；未做题目稳定 ID 迁移。
