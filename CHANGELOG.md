# 更新日志

本项目所有值得记录的变更都记录在该文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

> **规范（NX-06）**：凡 `medkit/prompts/*.md` 有改动，当版必须新增「`### Prompts`」小节
> （列改动与影响），并同步 `tests/fixtures/llm_cases/` 对应样本——prompt 与契约、fixtures 三者一致才可合入。

## [Unreleased]

### Fixed

- **JSON→SQLite 导入稳健性**：`import_from_json()` 移除「一次性 imported::表」门禁——导入后被写入的 JSON（旧实例/导入源回流）将按 id 幂等补导并改名，杜绝「JSON 活数据永远进不了 DB」的丢失分叉（曾致 182 条错题在学习中心不可见）。
- **一键刷薄弱组卷**：科目范围为「全部科目」时自动选中第一个可选科目再组卷（不再空报「请先在上方选择科目范围」）；无任何科目时提示「暂无可选科目：请先在错题本导入」。
- **审核台 B1/选项组显示修复**：逐题审核台按 `group.options` 显示与编辑 B1 共享选项（此前读 `q.options` 为空 → 组题只见题干、无选项、不可编辑）。

### Changed

- 启动页/学习中心不再展示不可恢复的占位知识点（`data_broken` 且 subject='???' 的历史垃圾记录已在本地数据修复时清理，快照保留于 `~/.medkit/library-backup-*`）。
- **学习中心徽章口径**：侧栏「学习中心」红点改按**真实待办**（今日到期复习卡数 + 进行中提问会话数），无待办不显示；错题本子徽章不再标红（资料库规模非待办）。
- **删除/取消能力补齐**：大纲覆盖条目逐条删除（`DELETE /api/syllabus/items/{id}`，误删可重新导入）；大纲/真题考频解析草稿「取消草稿」与逐条移除；真题考频已确认频次记录逐条删除（`freq_view` 条目带 id）；错题/讲解/提问会话/复习卡/记忆卡删除此前已具备。
- **长任务进度可见性**：MedQC 质检按批回写进度（`qc_batch(on_progress=...)` → progress.json，前端「质检中… 第 n/N 批」）；项目详情 stepper 补「汇总」阶段，可选/终态阶段（网络检索/取消/出错/配额）不再误高亮「出题」；日志行按 ❌/⚠️ 着色高亮；进度条显示更新时间。
- **B1 组题端到端启用**：`orchestrator._effective_ratios` 不再把 B1 配比并入 A1/A2/X（配额原样直达 MedGen，HC-7 契约 + 门禁/渲染/导出早已就绪）；项目详情配额说明移除「B1 暂由 A1/A2/X 分摊」。
- **押题卷判错自动回流**：判分成功后自动 POST `/api/library/mistakes/sync-paper`（幂等）——「判错自动回流错题本」承诺兑现；「同步错题到错题本」按钮保留为手动兜底/重试；错题去重键改用题目 id（案例组子题题干共享前 40 字不再被误并）。
- **记忆卡前端门禁修复**：`FEATURES` 补 `cards:true`（此前缺键导致「🧠 生成记忆卡」按钮与「医学记忆卡」面板默认不渲染，WP-05 前端不可见）；`applyFeatures` 对缺键回退默认开。
- **「已掌握」文案去误导**：错题本「标记已掌握」toast 改为「仅归档标记；掌握度仍由真实作答驱动」（`mark_learned` 本身不改写掌握度计数，属有意设计）。
- **多教材合并 sid 重编号**：多个素材会话合并载入为教材时，前端合并后统一重编号（S001..），后端 `create_project` 对重复 sid 防御性重编号——消除 orchestrator `slice_by_sid` 按 sid 覆盖导致的「前一教材切片静默丢失/配额错配」。
- **内置大纲种子打包**：`medkit.spec` 增加 `data/syllabus_seed_306.json`（此前打包版「导入内置大纲」静默 imported=0）；前端在 `seed missing` 时明确提示上传官方大纲。
- **逐题审核/重掷保存一致性**：先备份题库 JSON → 写盘 → 重渲染；重渲染失败自动回滚并返回可读错误（消除「编辑已持久化但产物未更新」的不一致态）。
- **审核台脏状态守卫**：未保存修改时切换主 tab / 进入其他项目 / 刷新关闭页面均拦截确认（补 `beforeunload` + `showTab/showProject` 前置检查）。
- **失败/停止体验**：生成失败后「开始生成」变为「重试生成（从断点）」提示；停止生成带确认弹窗（说明断点保留与重跑范围）；端口 4880~4889 全占时给出中文提示并保窗，而非 uvicorn 裸抛 traceback；浏览器自动打开失败打印可访问地址。
- **保存配置不误伤**：「保存配置」按钮不再硬编码 `web_search_enabled:false`（此前任何一次主卡保存都会静默关闭网络检索）；改取当前开关与后端字段并保留博查 Key。
- **扫描 PDF 报错引导**：文案改指向内置 MinerU OCR（此前引导到外部 WPS）。
- **教师重点口径统一**：配额词频加权与出题注入同用前 4000 字（`quota.TEACHER_TEXT_LIMIT`）。
- **学习中心口径**：真题考频改为「一句可命中多条」并加短词护栏（此前每句只记首个命中，频次系统性低估）；大纲覆盖卡片文案改为「命中错题/掌握度标签」口径（此前「出过几题」易误导）；提问会话「进行中」口径改为 7 天内有活动（弃会话不再永久污染徽章）；提问判分后失效学习中心 30s 缓存；「重新生成讲解」加确认；记忆卡上限 3~6 与提示词一致；真题考频单次确认 >200 条时分批提示；薄弱组卷 24h 窗口内已有完成项目时提示「先查看/删除」而非重复建卷。
- **Anki 导出门槛**：`/export/anki`、`/export/apkg` 改为「产物文件存在即可下载」（不再依赖 stage=done）。
- **文档同步**：README 修正「四提示词→八提示词」「B1 已实现口径」「成本公式前后端单源→项目创建单源/学习中心粗估」「五视图→六视图」「徽章口径」等描述。

### Added（P2 轮）

- **押题卷限时模式**：可开「限时模式 + N 分钟」，到点自动判分；默认练习计时（页面明示「不锁定」），计时器显示剩余时间。
- **题库 HTML 分页**：每页 50 题（‹ 上一页 / 第 n/N 页 / 下一页 ›）；筛选/搜索时自动跨页显示全部匹配并隐藏分页条；过滤状态记忆保持。
- **Anki 导入指引**：项目详情新增「Anki 导入指引」弹窗（.apkg 双击导入 / .txt 文件→导入 / 手机端 AnkiDroid 用法）。
- **讲解产物「查看教材切片原文」**：懒加载展示讲解所依据的教材切片（复用 explain/slices 检索，零 LLM）。
- **试出一题直达**：成功卡片新增「满意，创建课题 →」按钮与「再试一题」。

### Changed（P2 轮）

- **押题卷判分归一化**：答案键带空格/逗号（如" B "、"B, D"）不再误判（answersEqual 先归一化再比较）。
- **押题卷无选项题防御**：缺选项题不参与判分与计数，页面提示剔除数量。
- **题库 MD 版图/表**：data_table 渲染为 Markdown 表格；含图题标注「请查看 HTML 版」。
- **复习手册目录默认展开**（原默认收起，长手册找不到入口）。
- **图片题零产图提示**：已有图片素材但本批未产出图题时，项目详情展示提示（可重试/加大题量）。
- **单实例锁**：重复双击 MedKit.exe / start.bat 提示已运行并退出（`~/.medkit/app.lock`；非 Windows 自动放行）。
- **配置安全提示**：DPAPI 加密失败时保存反馈明确「Key 未加密」；配置损坏时前端提示「已备份并恢复默认，请重新配置」；`/api/health` 将残留阶段字段清理为 "ready"。
- **模型列表错误透传**：「获取模型列表」失败返回真实原因（Key 错/网络/端点不支持）。
- **版本号占位**：页脚 `v—` 由 /api/health 渲染（失败不再显示错误的 v0.5）。
- **首启向导**：ESC/遮罩关闭后本会话内不再重复弹窗（下次启动仍提示）。
- **make_icon 字体回退**：segoeuib.ttf 缺失时回退候选字体/PIL 默认；README 注明 Inno Setup 需 6.3+（x64compatible）。

## [0.8.0] - 2026-08-27

### Added

- **WP-01 大纲覆盖度引擎**（v0.8 · 考试锚定）
  - `core/syllabus.py` + 存储迁移 v2（`syllabus_items` 表）；
  - `/api/syllabus/*` 接口：ensure / parse（本地规则零 LLM）/ confirm（人工确认门）/ coverage / report / export；
  - 学习中心第 6 视图「大纲覆盖」：统计卡 + 大纲章节树 + 覆盖状态 chip + 粘贴导入 + 导出 Markdown；
  - medgen 大纲锚定注入（≤800 字），题目按大纲条目覆盖出题；
  - 种子大纲 1291 条 / 10 科（GoldenSet 真题 + 知识库素材教材元数据构建）。
- **WP-02 真题考频**（v0.8 · 考试锚定）
  - `core/realexams.py` + 存储迁移 v3（`realexam_freq` 表）；
  - 粘贴 / 上传自备真题 → 本地词典匹配计数（零 LLM）→ **人工确认门**（未确认不进任何权重）；
  - 章节 × 频次热力表 + 导出；产品不展示真题原文，仅展示频次结构。
- **WP-03 缺陷驱动智能组卷**（v0.8 · 考试锚定）
  - `core/gap.py`：`plan()` 纯本地配题（优先级 × 考频 × 未覆盖约束，单知识点 ≤3 题）+ 24h 幂等窗；
  - 复用课题创建通道（薄弱点清单注入 + scope=gap + 成本预估前置）；
  - 学习中心概览「⚡一键刷薄弱组卷」入口。
- **WP-04 医学图像 / 表格题**（v0.8 · 结构性补齐）
  - 项目详情「图片素材」上传（教材图 / 心电图 / 血常规截图 → `assets/fig_N` + image 切片）；
  - 出题注入「至少 1 题引用 + 题干写『如图所示』」，`image_ref` 门禁硬校验（不匹配剔除）；
  - 产物渲染：base64 内嵌 `<figure>`（单文件可移动）+ Markdown 表格 → `<table>`（XSS 白名单 + 打印防跨页）；
  - 错题随图回流：学习中心错题本可查看原图。
- **四项体验升级**
  - 网络检索「测试后端」修复：内置后端（DeepSeek / 智谱 / 千问）复用服务商 LLM Key，博查缺 Key 时明确提示；
  - 错题导入多格式：批量导入支持 **.json / .csv / .md / .txt**（CSV 表头别名 + A~F 列、MD/TXT 按题号切块、JSON 兼容官方结构，全部本地解析零 LLM）；
  - 厂商信息去时效化：注记不再固化模型代际 / 版本断言，统一「以官方最新为准」引导 +「获取模型列表」动态拉取；
  - 「以教师重点为纲」：大纲覆盖默认标准 =「教师重点」，自动扫描所有项目教师重点切片 → 考点条目（幂等同步）；「官方大纲 / 全部」标准可切换。
- **K3/IMP-13 官方大纲文件导入**（R2 收尾批）：`/api/syllabus/seed/parse-file`（预览）与 `/api/syllabus/seed/import-file`（幂等入库 source='seed'）——md/txt → 按「考查内容」切 6 科逐科 `chat_json` + `OutlineSubject` 契约抽取（`max_tokens=16000` 适配推理模型），LLM 不可用回退本地规则；spike 核验 recall 100.0% / precision 96.5% / 10 条抽样 10/10。
- **大纲标准二选一（教师重点 v4）**：`/api/syllabus/teacher/import` / `teacher/import-file`（PDF 文本层/DOCX/MD/TXT → 两档解析：章/条目结构化 ↔ 要点行 flat，零 LLM，幂等入库 source='teacher'）；迁移 v4：历史 `source='paste'` 归一为 `'teacher'`（不可精确回滚，升级前自动备份）。
- **教师重点知识点提取**：`core/syllabus.py::extract_teacher_kps`（条目 → 知识点名：去「重点掌握/考点…」前缀、≤40 字收束、去重保序），随导入响应 `knowledge` 字段返回；设计边界：不写入学习库掌握度状态机。
- **大纲标准二选一前端收尾**：`syl_std` 移除「全部」档（仅 教师重点/官方大纲 两档）；大纲覆盖视图新增「上传教师重点文件」（PDF/DOCX/MD/TXT）与「上传官方大纲(md/txt)」一键导入入口；`api()` 修复：字符串体自动补 `Content-Type: application/json`。
- **WP-05 医学记忆卡工厂（NX-04）**：讲解产物 → 3~6 张记忆卡（`CardDraft` 契约：value 数值 / mnemonic 口诀 / contrast 鉴别 / concept 概念；`chat_json(schema=CardDrafts)` 硬校验）→ `core/cards.py` 幂等入库（迁移 v5 `cards` 表；≤8 张/篇）。
- **WP-05 Scheduler 协议 + FSRS**：`core/scheduler.py`——py-fsrs 6.3.2 默认（quality 0~5 → Again/Hard/Good/Easy，`enable_fuzzing=False` 可测可解释），SM-2 legacy 可切（复用 `core/review`）；算法按「创建时」绑定卡片，切换只影响新卡（队列不丢、可回滚）。
- **记忆卡前端**：学习中心「讲解产物」每篇新增「🧠 生成记忆卡」动作（`flag("cards")` 双端门禁）；「复习计划」新增「🧠 医学记忆卡」面板（今日到期、四档自评：重来/困难/良好/简单）。
- **记忆卡 Anki 导出**：`render/apkg.py::export_memory_apkg`（独立「MedKit 医学记忆卡」牌组，类型/知识点标签）。
- **Agent 交接文档**：`docs/AGENT_HANDOFF.md`（大纲选择机制、教师重点处理流程、官方大纲抽取链路、陷阱与注意事项）。

### Changed

- 学习中心由「子导航 + 五视图」扩展为六视图（新增「大纲覆盖」），大纲覆盖页集成「真题考频」卡。
- 项目详情新增「图片素材」卡。
- 仓库整理：S1 审查全套归档至 `docs/reviews/s1-2026-08-27/`；v05~v07 历史设计规格归档至 `docs/archive/design-specs/`；README 文档引用同步；`.workbuddy-ai/` 移出版本库并加入 `.gitignore`。
- 仓库卫生（NX-09）：删除 `docs/superpowers/` 空目录；`medkit.spec` 提示词模板注释校正为「六个」。

### Fixed

- 网络检索「测试后端」此前把博查 Key 槽位传给内置后端，导致永远「未配置 api key」——已修复为各内置后端复用对应服务商 Key 的逻辑。
- **NX-02**：FTS `fts_tokens` 在 jieba 缺失/词典损坏时不再抛错，回退 bigram 兜底（打包环境健壮性）；`medkit.spec` 收集 jieba 子模块与词典数据。
- **NX-03（R-2 返工）**：ADR-003 契约层闭环——① MedQC 判分 JSON 改走 `validate_or_repair` 硬闭环（校验失败 → 带错误重发 1 次修复 → 仍失败 score=-1 不计分，批次进项目「人工复核清单.md」，聚合平均分跳过 -1）；② MedGen 软校验告警计数落项目 meta（`contract_warnings`），学习中心概览卡在计数 >0 时显示「最近一轮生成有 N 条输出未通过契约校验」。

### Prompts

- **新增 `medkit/prompts/medcards.md`**（NX-04/WP-05）：讲解 → 3~6 张医学记忆卡（value/mnemonic/contrast/concept 四型契约、正反面必填、不臆造红线）；契约模型 `CardDraft/CardDrafts`（`medkit/core/schema.py`），fixtures 新增 `tests/fixtures/llm_cases/medcards.json`。
- **新增 `medkit/prompts/syllabus_extract.md`**（K3/IMP-13）：官方大纲逐科 one-subject JSON 契约抽取（推理模型 `max_tokens=16000` 关键参数，见 `docs/AGENT_HANDOFF.md` §4）。

### Security

- 产物页表格渲染走 XSS 白名单消毒；`image_ref` 门禁阻止无引用图片的题目进入产物。

[0.8.0]: https://github.com/2710074390-cyber/medkit/releases/tag/v0.8.0
