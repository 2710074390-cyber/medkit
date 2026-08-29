# MedKit 全链路用户体验审查（2026-08 · 全量轮）

> 范围：产品端点到端使用链路——安装/首启 → 连接 AI → 素材解析 → 课题创建 → 生成管线 → 逐题审核 → 产物（题库/押题卷/复习手册/Anki）→ 学习中心（六视图）→ 更新/反馈。
> 方法：4 路并行代码级审查（安装配置链 / 素材建课链 / 生成审核产物链 / 学习中心链）+ 交叉验证（B1 配比实测、dist 打包实测、启动实测、pytest 313 passed、ruff 干净）。
> 结论基调：功能完成度高、工程底座扎实；**主要问题集中在「宣传 vs 实际行为」的落差与「长任务反馈黑箱」**，另有 2 处打包缺失导致核心功能在发布版不可用。

---

## 0. 验证记录（本次实测）

| 项 | 结果 |
|---|---|
| `pytest` 全量 | 313 passed（39.6s；2 条上游 deprecation warning） |
| `ruff check .` | All checks passed |
| 开发模式启动（4880） | `/api/health` 200（version 0.8.0）+ 首页 200 ✓ |
| B1 配比实测 | `{A1:40,A2:30,B1:20,X:10}` → 实际出题配比 `{A1:50,A2:38,X:12}`；`{B1:100}` → `{A1:50,A2:40,X:10}`，**B1 任意取值均为 0** |
| dist 打包实测 | `_internal/medkit/data/` 仅含 `samples`；`_internal/data/` 不存在 → **syllabus_seed_306.json 未打包** |

---

## 1. P0（立刻修：核心承诺落空 / 功能不可见 / 数据丢失风险）

### P0-1 记忆卡（WP-05）整块对用户不可见
- **位置**：`medkit/web/js/app.js:10`（`FEATURES` 缺 `cards` 键）；`medkit/web/js/learn.js:873`（生成记忆卡按钮）、`:1292`（医学记忆卡面板）均以 `FEATURES.cards` 为渲染门禁；后端 `medkit/core/state.py:25-33` `flag("cards")` 缺省 True。
- **现状**：`FEATURES.cards === undefined` → 按钮与面板默认不渲染；后端接口可用但前端无入口。CHANGELOG 0.8.0 宣告「记忆卡前端：讲解产物新增🧠生成记忆卡动作（flag("cards") 双端门禁）」——**前端门禁配错了键**。
- **用户影响**：讲解产物无法生成记忆卡，「医学记忆卡」面板永远空白，FSRS 复习闭环缺失一块核心拼图。
- **修法**：`app.js:10` 增加 `cards: true`；并在 `applyFeatures` 中对 `undefined` 键回退默认 true（防御性）。

### P0-2 「判错自动回流」是假的：实为手动按钮 + 内存态
- **位置**：`medkit/web/index.html:356`（薄弱组卷说明「判错自动回流错题本」）、README 多处同文案；真实交互在 `medkit/render/qbank_html.py:546`（判分后需手动点「同步错题到错题本」）、`:625-637`（同步失败提示条 + 重试）。`WRONG_POOL`（:373）仅存内存，不点即丢。
- **用户影响**：押题卷判错后若不点那个不醒目的灰色按钮，错题永不进错题本 → 学习闭环第一环断；「自动」承诺落空。
- **修法**：判分成功后自动 POST `/api/library/mistakes/sync-paper`（幂等已有），按钮降级为手动兜底；同步失败条保留并提示「可在应用内重试」。
- **附带**：产物 HTML 被下载到本地（file://）打开时 `fetch("/api/...")` 必失败——产物页顶部应常驻提示「请在 MedKit 内打开以同步错题」，或改为「同步仅在线打开时可用」。

### P0-3 「已掌握」口径三处断裂，且 toast 直接误导
- **位置**：`medkit/core/library.py:307-321`（`mark_learned` 只翻转 learned 标记，注释明确「不改写掌握度计数」）；`medkit/web/js/learn.js:729` toast **「已计入掌握度」**；对比 `knowledge.state==mastered`（得分驱动）与概览 `loop.mastered`（dashboard）。
- **现状**：错题行点「已掌握」→ 绿色 chip，但对应知识点仍是「薄弱」、概览已掌握环数不变——三者可同屏不同值。
- **用户影响**：用户以为「标记已掌握 = 计入掌握度」，实际掌握度只由真实作答驱动；口径不可信。
- **修法**：toast 改「已标记（仅归档，不影响掌握度）」；在概览/错题本把「已掌握（归档）」与「掌握度（作答驱动）」在文案与视觉上分开。

### P0-4 题型结构配置 ≠ 实际产出（B1 失效 + A3/A4 不可达 + 审核台配套失效）
- **位置**：
  - `medkit/core/orchestrator.py:31` `B1_WEIGHT_REDIST = {"A1":0.5,"A2":0.4,"X":0.1}`（注释「B1 未支持时配额再分配」）、`:140-147` `_effective_ratios` 把 B1 弹出并入 A1/A2/X；
  - 前端 `review-desk.js:261/887` 配比含 B1（默认 20%），`index.html:159-167` 无 A3/A4 输入；
  - `medkit/routers/pipeline.py:103-116` `/api/trial` **不走** `_effective_ratios` → 试出一题可能出 B1、正式生成 0 道 B1（试出≠正式）；
  - 项目详情仅一行小字说明（`review-desk.js:1111`「B1 组题暂由 A1/A2/X 分摊」），新建课题页无任何提示；
  - 审核台过滤含 A3/A4（`review-desk.js:1322`）——实际产物几乎无该类题，过滤多为空；
  - 审核台 B1 题显示/编辑失效：`review-desk.js:1423/1433` 读 `q.options`，B1 共享选项在 `q.group.options`（`medgen.py:180-189`、`schema.py:74`）→ B1 题只见题干+答案，无选项、不可编辑；重掷后破坏组结构。
- **用户影响**：用户每次建课题都在配置一块注定无效的配比（默认 20% 就是 B1）；「B1 真组题」「A3/A4 案例组题」是 README 与 CHANGELOG 的 S3 宣传核心，端到端不可达（提示词支持 + 渲染/门禁/导出兼容，但管线不产生）。
- **修法（二选一，推荐前者）**：
  1. 补齐端到端：`_effective_ratios` 保留 B1（及从 A2 拆分 A3/A4 的配额规则），前端配比增加 A3/A4 输入（或「案例组题占比」），审核台复用 `qbank_html._effective_options`（qbank_html.py:63-70）渲染/编辑 B1 选项；
  2. 或诚实化：建课题页 B1 控件改为禁用+说明「暂未开放」；更新 README/CHANGELOG 收回「B1/A3/A4 已实现」声明；删审核台 A3/A4 过滤项；「试出一题」同步走 `_effective_ratios`。
- 无论哪种，`trial` 与正式管线必须先统一配比口径。

### P0-5 内置大纲种子未打包 → 发布版「导入内置大纲」静默 0 条
- **位置**：`medkit/core/syllabus.py:28` `SEED_FILE = Path(__file__).resolve().parents[2] / "data" / "syllabus_seed_306.json"`（仓库根 `data/`）；`medkit.spec:26-30` `datas` 只含 `medkit/web`、`medkit/prompts`、`medkit/data`（**无根 `data/`**）。实测 dist：`_internal/data/` 不存在；`internal/medkit/data/` 仅 `samples`。
- **现状**：打包版 `ensure_seed` → `seed missing` → `{"imported": 0}`；前端 `learn.js:178-180` toast **「大纲种子导入：新增 0 条（幂等）」**——与「已导入过」的常规输出无法区分，用户以为正常。
- **用户影响**：1291 条/10 科的考试锚定核心数据在发布版不可用，且无任何失败信号。
- **修法**：`medkit.spec` `datas` 增加 `("data/syllabus_seed_306.json", "data")`；`ensure_seed` 对 missing 返回 `note="seed missing"` 时前端 toast 明确提示「内置大纲缺失，请上传官方大纲文件」；理想情况运行时用 `importlib.resources` 双路径查找（开发/打包兼容）。

---

## 2. P1（下轮必修：链路体验断点）

### 2.1 长任务体验（生成链）
| # | 位置 | 问题 | 用户影响 | 修法 |
|---|---|---|---|---|
| 1 | `orchestrator.py:543-546/558-561/601-602/611-612`；`medqc.py:121-167` | MedQC/MedFix/MedReview/渲染阶段只写一次 0% 进度即阻塞；MedQC 分批并发但不回写进度、run.log 无逐批打点 | 100~500 题时 QC 是最长单段（2~8 分钟），进度条与日志完全静止，用户无法判断「还在跑」还是「卡死」 | qc 每批回写 `_set_progress` + 逐批打 log；fixing/reviewing 给出「正在修复 N 题 / 预计 1~2 分钟」 |
| 2 | `orchestrator.py`（progress.json 无 ETA）；`review-desk.js:1070-1219` | 全程无「预计剩余时间」；唯一近似文案 `:298`「首次约 1~3 分钟」仅限网络检索 | 生成十几分钟，用户不知道还要等多久 | 按「已完成切片占比 × 已耗时」估算剩余时间，随进度条展示 |
| 3 | `review-desk.js:1070-1076` | STEPS 缺 `websearch`/`finalizing`；`stepIdx` 对 websearch→「出题」、finalizing→「产物」（真实顺序在其前）、cancelled/error/quota→「出题」 | 网络检索阶段显示「出题」；取消/出错后 stepper 位置全错 | 纳入 websearch/finalizing；cancelled/error 用 stage_label 显示独立状态 |
| 4 | `pipeline.py:71-79`；`orchestrator.py:458/480` | 失败时原始异常字符串写 log + stage=error，前端仅「出错（见日志）」，**无「重试」按钮** | 网络/限流/配额耗尽失败，用户看到陌生异常文本，不知能否重试、是否重复扣费 | 失败分类摘要（网络/配额/契约）+「重试（从断点）」按钮 + 说明重跑范围与费用 |
| 5 | `orchestrator.py:172-186/460-477`；`pipeline.py:32-33` | 断点续跑仅覆盖「切片出题」级；QC/门禁/修复/复习/渲染无断点，取消或出错后**全部重跑并重扣费**；stage=done 后 run 直接 409「删除重建」，无「清空断点重跑」 | QC 中途取消 → 续跑重跑整个 QC；想彻底重生成只能删项目 | 标注各阶段可续范围 +「重跑（清除断点）」入口 |
| 6 | `projects.py:199-201` | /status 日志仅最后 60 行、纯文本无级别高亮，「❌ 管线失败」与普通日志同灰 | 早期关键日志（检索摘要/门禁剔除）看不到；出错行不显眼 | 日志按行着色（❌/⚠️ 标红）+「查看完整日志」 |
| 7 | `review-desk.js:1220-1232` | 「停止生成」无确认弹窗，无「已生成 N 题/进度保留」明确状态 | 用户不清楚停了多少、剩下什么 | 停止前 confirm 说明「已生成 X 题将保留，断点可续跑」 |
| 8 | `orchestrator.py:73-96/504-536/589-591`；`review-desk.js:1049-1068` | 门禁剔除/MedFix 改写/契约失败/渲染前剔除只进 run.log+`人工复核清单.md`；产物网格里清单只是一个无标题文件名 | 用户不知道哪些题被 AI 改过/丢了；要复核得在产物里翻不认识的文件 | 完成 toast 汇总「改写 N · 剔除 M · 人工复核 K」；清单专用图标与入口 |

### 2.2 审核台（正确性）
| # | 位置 | 问题 | 用户影响 | 修法 |
|---|---|---|---|---|
| 9 | `review-desk.js:1349-1354`；`app.js:108-113/232-249` | 脏状态离开无完整提醒：仅「刷新」有 confirm；切主 tab、重进项目详情、关闭/刷新浏览器均不校验 `reviewState.dirty`，无 `beforeunload` | 剔除/编辑几题后切走或刷新，未保存修改**静默丢失** | `beforeunload` + showTab/showProject 前 dirty 二次确认 |
| 10 | `medkit/routers/review.py:113-115` | 审核保存先原子写 `questions_final.json` 再 `_rerender_project`；重渲染抛错 → 500，但编辑已持久化、产物是旧的 | 「已改但产物没更新」不一致态，用户不确定是否生效 | 先渲染成功再写 JSON（或事务化）；失败提示「已保存但产物待更新，请重试」 |
| 11 | `review.py:35-74`；`review-desk.js:1461-1473` | 单题重掷/审核保存都触发**全量重渲染**（题库/押题卷/手册/Anki/.apkg）；按钮文案「约 30 秒」，500 题远超且无进度 | 点「重掷」后按钮卡「重掷中…」很久无反馈 | 只更新受影响产物或异步化；按钮改「任务已提交」+ 进度 |
| 12 | `review-desk.js:1427-1434` | 行内编辑仅 question/analysis/answer/bloom；type/subtopic 无输入（后端 `review.py:107` 接受）、无法增删选项 | 「编辑」名不副实——改不了题型/章节/选项个数 | 补齐 type/subtopic；支持加/删选项；X 型答案键排序校验提示 |

### 2.3 素材与建课（数据正确性）
| # | 位置 | 问题 | 用户影响 | 修法 |
|---|---|---|---|---|
| 13 | `medkit/core/slice.py:45`（S001 起编号）+ `review-desk.js:848-864`（会话合并直接 concat）+ `orchestrator.py:225`（dict 按 sid 去重）+ `projects.py:92-101`（配额对重复 sid 各算） | **多教材合并静默丢切片**：各会话切片同从 S001 编号，合并后同名 sid 覆盖 → 前一教材内容被丢弃；quota 里重复 sid 条目都保留 → 同一文本被按两份配额出题 | README 主打的「多个会话合并载入为教材（多教材合并出题）」最核心链路出错：内容丢失 / 重复出题 / 溯源错乱，全程无警告 | 合并时（或 create_project 时）对全部 slices 重新编号（`S{i:03d}` 全局顺序），或 sid 加会话前缀（`{sessionId}-S001`） |
| 14 | `medkit/core/extract.py:44-46`；`medkit/core/mineru.py:93-100` | 扫描 PDF 报错文案把用户引向外部 WPS 而非内置 OCR；默认开自动 OCR 但无 Token 走轻量 API（≤10MB/约20页上限），真实扫描教材必超限，报错后才被迫去 mineru.net 注册拿 Token（外部账号+配额=隐性成本） | 扫描教材用户第一道墙：先误入 WPS 再撞 MinerU 限额，注册/Token 前置知识缺失 | 报错文案改引导「点 OCR 识别」；素材要求卡前置「扫描版需 MinerU Token（免费额度有限）」，识别前提示文件规模是否超轻量限制 |
| 15 | `medgen.md:69`（软要求「至少 1 题引用图片」）vs `orchestrator.py:495-503`（仅剔非法 image_ref，不保证存活 ≥1 图题）+ `review-desk.js:1159`（承诺「出图/表题」） | 传图后可能 0 图题且无解释 | WP-04 承诺「至少 1 题引用 + image_ref 硬校验」未兑现 | 渲染前「已有图题」检查 + 不存在时提示「本轮无图题（可提高题量/重试）」；或门禁补一轮「至少 1 题引用」强约束 |
| 16 | `medkit/core/quota.py`（全文词频加权）vs `medgen.py:24`（教师重点仅注入前 4000 字）+ `index.html:228`（「超过约 4000 字仅前 4000 字参与锚定」） | 教师重点超长时：配额按全文加权、出题按前 4000 字 → 加权与锚定不一致 | 用户以为全文都参与了权重，实际后段被静默忽略 | 加权与注入统一口径：均用前 4000 字，或「配额提示截断」 |
| 17 | `review-desk.js:571-585`（单文件 >200MB 整组放弃）、`:545-552`（拖拽不校验大小/类型）、`:714`（切片预览仅 slice(0,12)） | 一个超大文件导致整组解析失败；拖入不支持的格式到客户端才报错；预览看不到全部切片 | 错误定位费时；信息不透明 | 单文件超限时跳过该文件继续其余并提示；拖拽即校验；预览显示全部切片（可折叠/分页） |

### 2.4 学习中心（口径与闭环）
| # | 位置 | 问题 | 用户影响 | 修法 |
|---|---|---|---|---|
| 18 | `core/syllabus.py:567-591` vs `index.html:457` | 「已覆盖」= 条目文本与**错题/知识点子串匹配**，≠「出过题」；文案却写「这章考点我出过几题」 | 用户以为「已覆盖=我已出过题」，实际与题目生成完全解耦，数字给出虚假信心 | 改口径文案为「已学习/已建立错题」；或将覆盖判定与该条目是否出过题挂钩 |
| 19 | `core/realexams.py:59-79` | 考频匹配每句只认第一个命中条目（break）+ 条目无长度护栏（短词如「心」大面积命中） | 频次显著低估/虚高；UI 仅写「本地词典统计」未提示一句一记 | 一句多命中也计数 + 过滤过短条目 + 弹窗注明「逐句首命中计数，非语义分析」 |
| 20 | `learn.js:881-890` vs README:107「成本公式前后端单源」 | 学习中心成本预估 `estLlmCost` 为**前端硬编码**（1.4/2.2/0.8 万 token），与后端 `core/cost.py` 不同源；且 `learn.js:898/1105` 在**点击后**才显示预估（并非「触发前确认」） | 讲解/提问成本口径与项目创建预估不一致；用户无法在知道成本后取消 | 前端改调 `/api/cost/estimate`（为讲解/提问增加场景参数）；触发前先展示预估+确认 |
| 21 | `core/tutor.py:45`；`dashboard.py:86`；`learn.js:84-85` | 提问会话无超时/过期；`in_progress` = state≠mastered，弃会话永远「进行中」 | 侧栏「进行中会话」徽章永久 +1，清不掉（只能删会话） | 无活动超时（如 7 天）自动归档；in_progress 只算「今日有活动」 |
| 22 | `learn.js:1226-1238` vs `:1305/1317` | 同页两套自评：SM-2 卡 6 档（0 懵了…5 秒答）vs 记忆卡 4 档（重来0/困难2/良好3/简单5）——同质量分「3」=「勉强」=「良好」 | 用户对「3」迷惑，两套并排易选错 | 视觉分区 + 各自图例；或统一 0~5 六档并注明映射 |
| 23 | `core/scheduler.py:23`；`learn.js:1301/1317` | 「FSRS 默认，SM-2 可切」写进文案，但前端**无切换入口**（创建时硬绑 fsrs） | 用户以为能切 SM-2，实际永远 FSRS | 加显式切换控件，或删「可切」文案 |
| 24 | `core/gap.py:142-165`；`learn.js:405-437` | 薄弱组卷 24h 幂等只对「未完成」项目生效；旧 gap 项目 >24h 未完成再点会**新建重复项目** | 重复点击产生双倍「薄弱专项」课题 | 窗口内复用「最近」项目而非仅「未完成」 |
| 25 | `learn.js:440-454` vs `:1118-1142` | 概览 30s 缓存；tutorSubmit 判分后未 invalidate/loadDashboard | 提问学完回概览，掌握度环/徽章是旧值 | tutorSubmit 后失效缓存并刷新 |
| 26 | `dashboard.py:63-67` vs `learn.js:591` | 概览「错题沉淀」按所选科目过滤，错题本列出全部（`/mistakes` 无 subject 参数） | 切科目后两处数字对不上 | 错题本支持科目过滤，或概览错题环注明「所选科目」 |
| 27 | `learn.js:931-936` | 「↻ 重新生成」先 DELETE 再生成，**无确认**；LLM 失败旧讲解已丢 | 误点一篇讲解就没了 | 确认后执行或保留旧版/版本回退 |
| 28 | `core/explain.py:232-241`；`learn.js:869-870` | 讲解来源仅显示切片标题/URL，**不展示实际注入的切片原文**（slices_used 只存 sid） | 用户无法判断讲解 grounded 在哪几句话上 | 「查看所用切片」展开原文（≤900 字/片） |
| 29 | `learn.js:343`；`core/library.py:240-274` | `rexConfirmAll` 只送前 200 条；批量导入同批重复不去重、缺题干校验 | >200 条静默丢弃；重复/空题干错题入库 | 分批确认或提示上限；批内去重 + 题干非空校验 |
| 30 | `medcard`：`medcards.py:18-19`（3~6 张）vs `core/cards.py:33`（软上限 8）；无记忆卡 Anki 导出入口（项目题卡才有） | 实际可 8 张；「把记忆卡导进 Anki」无入口 | 统一 3~6；记忆卡补「导出 Anki(.txt)」 |
| 31 | `core/library.py:649-768`；`learn.js:560-563` | 乱码修复为整库就地修改，无单条撤销/预览 diff（仅整库 .bak 回滚） | 想恢复某条只能整库回滚 | 修复前出 diff 预览或允许按记录撤销 |
| 32 | `core/tutor.py:123-146` | 已 mastered 知识点仍可开新「提问」，首问继续出 | 逻辑矛盾 | mastered 禁止新开会话或给进阶考法首问 |

---

## 3. P2（体验打磨，可排期）

| # | 位置 | 问题 |
|---|---|---|
| 33 | `run_medkit.py:21-30/33-37`；`start.bat` | 端口 4880-4889 全占时返回 4880 → uvicorn 裸抛 traceback、浏览器永不打开（README:29「自动回退无需处理」落空）。应全占时给中文提示 + 保窗退出 |
| 34 | `review-desk.js:208` | 「保存配置」硬编码 `web_search_enabled:false` → 用户启用网络检索后，任何一次主卡「保存配置」都会**静默关闭**联网（无提示）。应读取 `$("t_web").checked` 或拆分端点 |
| 35 | `main.py:74-79` | 浏览器自动打开失败被 `except: pass` 吞掉；无默认浏览器/策略拦截时用户无入口。应 logger 记录 + UI 主区显示「请手动访问 http://127.0.0.1:{port}」 |
| 36 | `run_medkit.py` + `main.py` | 无单实例锁：重复双击会起第二个实例（4881）共享 `~/.medkit` 配置与项目目录，并发写有丢失更新风险。应单实例检测 + 聚焦现有实例 |
| 37 | `medkit/core/config.py:107-111/75-77` + `review-desk.js:213` | DPAPI 失败时静默回退明文落盘，但 toast 无条件宣称「Key 已加密」。应失败时拒绝加密承诺并在 UI 警示 |
| 38 | `core/config.py:115-148` | `config.json` 损坏时静默回退 DEFAULTS（服务商/Key 重置）仅 logger.warning。应 UI 提示「配置损坏已恢复默认（旧文件已备份）」，`save()` 失败转可读 4xx |
| 39 | `routers/config.py:174-182` | 模型列表获取失败被吞为 `[]`，前端只见通用文案，无法区分 Key 错/网络/端点不支持。应返回真实错误 |
| 40 | `routers/config.py:19` | `/api/health` 返回硬编码 `"stage": "v0.5-S2"`（旧里程碑残留，version 已是 0.8.0）。应清理或改为 `__version__` 派生 |
| 41 | `review-desk.js:1691-1764` | 首启向导 ESC/遮罩关闭不写标记 → 未配 Key 用户每次刷新再次弹窗；「载入示例」在未配 Key 时点击会直接触发出题失败（「30 秒看懂」承诺落空） |
| 42 | `index.html:59` + `review-desk.js:1787-1790` | 版本号 HTML 硬编码 `v0.5`，靠 /api/health 成功后覆盖；health 失败时显示错误版本 |
| 43 | `review-desk.js:1070-1076` | stepper 缺 websearch（见 P1-3，此处为双实例记录） |
| 44 | `qbank_html.py:394-398` | 押题卷判分 `answersEqual` 不空白归一化：答案键「B 」/「 B」时用户选 B 被判错。应先 `replace(/\s/g,"")` 再比较 |
| 45 | `qbank_html.py:641-645` | 押题卷计时为 count-up 秒表，无倒计时/到点自动交卷/时长配置——与「计时答题」宣传有落差。应加可选用时上限或明示「练习计时（不锁定）」 |
| 46 | `qbank_html.py:408/526` | 无选项题被跳过但计入总数（`QUESTIONS.length`/`total`）→ 分母虚高、答题卡错位。应渲染前过滤无选项题 |
| 47 | `qbank_html.py:516` | WRONG_POOL 去重键为题干前 40 字 → 案例组子题（共享前缀）被误判同一题，错题重练/同步只留一道。应改用 `q.id` |
| 48 | `qbank_html.py:182-234` | 题库 HTML 一次性内联全部 `<details>`（含 base64 图），无分页/惰性渲染，100+ 题卡顿。应每页 N 题/懒加载 |
| 49 | `qbank_html.py:124-144` vs `:31-60` | MD 版产物丢失图像/表格，且不标注 image_ref → 与 HTML 不一致。MD 至少渲染 data_table + 注明「图见 HTML 版」 |
| 50 | `review-desk.js:1106-1108`；`learn.js:1340-1363` | Anki 导出无「如何导入 Anki」指引，卡样预览仅前 3 张。应加导入指引弹窗 + 预览翻页 |
| 51 | `review_html.py:168-174` | 复习手册目录 `<details class="toc">` 默认收起且无当前章节高亮 → 目录默认不可见。应默认 open 或露摘要行 |
| 52 | `review-desk.js:1228` | 「开始生成」toast 描述遗漏「汇总题库/渲染」阶段，顺序不完全对应真实流程 |
| 53 | `review-desk.js:1058-1068` + `projects.py:258/273` | Anki 导出被 `stage != "done"` 硬门槛挡住；qbank 却可在 error 态下载——产物存在时行为不一致。应改「文件存在即可下载」 |
| 54 | `medkit/prompts/medgen.md:50/114` | X 型「≤ 本切片题量 10%（不足 5 题不出）」与配比（默认 X=10%）在小切片下必然偏离 → 全局配比与产物可能不符；bloom 配比是全集目标但按切片注入 → 小切片各自「达标」不可满足（已有 n<10 放宽，但 n=11 时仍可能 fail 空转修复轮）。应在配额级说明「按全集收敛」 |
| 55 | `medgen.md:69` 等 | 图片「至少 1 题」在提示词为软要求（见 P1-15） |
| 56 | `medkit/data/samples/样例_儿科学_节选.md` | 示例素材仅儿科学一章节，无法覆盖 A2/X/B1 等场景（示例体验「30 秒看懂」效果有限）。可扩充样例覆盖多题型 |
| 57 | `review-desk.js:953-962` | 「试出一题」成功后仅文字提示「点创建课题 →」，无按钮直达；试出题不展示成本预估 |
| 58 | `learn.js:1216-1230` 等 | 复习页「查看提示」懒加载教材原文切片（零 LLM）——关键词 top-k 匹配结果不可解释，用户可能看到不相关原文。应标注匹配依据 |
| 59 | `pack/make_icon.py:12` | 图标字体硬编码 `C:\Windows\Fonts\segoeuib.ttf` + 需 PIL，非 Windows 环境再生成即抛错（medkit.ico 已提交，仅影响维护者） |
| 60 | `medkit.iss:29` + `pack/build.bat` | `ArchitecturesInstallIn64BitMode=x64compatible` 需 Inno 6.3+，与脚本宣称的「Inno 6 兼容」存在版本张力 |

---

## 4. 文档失真清单（README / CHANGELOG / UI 文案 vs 实际）

| # | 位置 | 失真点 |
|---|---|---|
| D1 | `README.md:63` | 「四个提示词（MedGen·MedQC·MedFix·MedReview）」→ 实际 8 个（还含 medexplain/medtutor/medcards/syllabus_extract） |
| D2 | `README.md:80/94-121` | 目录树与「已实现功能」多处过时（缺 db/syllabus/websearch/scheduler/cards 等模块与 WP-01~05 对应条目不全） |
| D3 | `README.md:99/104` | 「A3/A4 案例组题 + B1 真组题」→ 端到端不可达（见 P0-4） |
| D4 | `README.md:107` | 「成本公式前后端单源」→ 学习中心 estLlmCost 为前端硬编码（见 P1-20） |
| D5 | `README.md:114` | 学习中心「五视图」→ 实际六视图（含大纲覆盖）；侧栏徽章「薄弱知识点数」→ 实际「今日到期复习+进行中会话」 |
| D6 | `README.md:98/106` | 「五阶段管线」vs「六阶段 stepper」描述并存，且均不含网络检索/汇总阶段 |
| D7 | `index.html:356` 等十余处 | 「判错自动回流」→ 实际手动按钮（见 P0-2） |
| D8 | `index.html:457` | 「这章考点我出过几题」→ 覆盖判定与出题解耦（见 P1-18） |
| D9 | `medkit.spec:28` 注释 | 提示词注释列 6 个（缺失 medcards/syllabus_extract） |
| D10 | `CHANGELOG.md:56` | 「记忆卡前端…flag("cards") 双端门禁」→ 前端 FEATURES 缺 cards 键（见 P0-1） |

---

## 5. 优先执行建议（排期视角）

1. **立即（P0，半天内）**：P0-1（FEATURES 补 cards）· P0-5（spec 加 data + seed missing 提示）· P0-2（判分自动同步）· P0-3（toast 文案）· P0-4 的诚实化最小方案（建课页 B1 禁用说明 + README 收回声明 + trial 统一口径；端到端补齐可排下一迭代）。
2. **本轮迭代（P1）**：2.1 长任务反馈（QC 进度回写 + ETA + stepper 修正）· 2.2 审核台（dirty 提醒 + 保存事务 + B1/A3 审核台数据口径）· 2.3 多教材合并 sid 重编号（#13）· 崩溃启动提示（#33）· 保存配置不误伤（#34）。
3. **下轮（P2）**：押题卷（空白归一化/倒计时/无选项题计数/WRONG_POOL/id）· 产物（MD 图、分页、Anki 指引）· 学习中心口径（覆盖/考频/自评/缓存）· 文档修订（D1~D10）。
4. **长线**：B1/A3/A4 端到端补齐（或彻底诚实化）、断点续跑扩展到 QC 后阶段、单实例锁、讲解切片原文可视化。
