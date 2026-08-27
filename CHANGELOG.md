# 更新日志

本项目所有值得记录的变更都记录在该文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

## [Unreleased]

### Added

- **K3/IMP-13 官方大纲文件导入**：`/api/syllabus/seed/parse-file`（预览）与 `/api/syllabus/seed/import-file`（幂等入库 source='seed'）——md/txt → 按「考查内容」切 6 科逐科 `chat_json` + `OutlineSubject` 契约抽取（`core/schema.py` 新契约、`prompts/syllabus_extract.md` 提示词；`max_tokens=16000` 适配推理模型），LLM 不可用回退本地规则；spike 核验 recall 100.0% / precision 96.5% / 10 条抽样 10/10（`docs/spikes/K3_syllabus_extract.py` + `k3_out/` 记录）。
- **大纲标准二选一（教师重点 v4）**：`/api/syllabus/teacher/import` / `teacher/import-file`（PDF 文本层/DOCX/MD/TXT → 两档解析：章/条目结构化 ↔ 要点行 flat，零 LLM，幂等入库 source='teacher'）；迁移 v4：历史 `source='paste'` 归一为 `'teacher'`。

### Fixed

- **NX-02**：FTS `fts_tokens` 在 jieba 缺失/词典损坏时不再抛错，回退 bigram 兜底（打包环境健壮性）。

### Changed

- 仓库整理：S1 审查全套（2026-08-27 需求审查/前端审查/结构化执行方案/工程审查改进指南 + 3 张截图）归档至 `docs/reviews/s1-2026-08-27/`；v05~v07 历史设计规格归档至 `docs/archive/design-specs/`；README 文档引用同步；`.workbuddy-ai/` 移出版本库并加入 `.gitignore`；清理 pytest/ruff 缓存与构建产物。

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

### Changed

- 学习中心由「子导航 + 五视图」扩展为六视图（新增「大纲覆盖」），大纲覆盖页集成「真题考频」卡。
- 项目详情新增「图片素材」卡。

### Fixed

- 网络检索「测试后端」此前把博查 Key 槽位传给内置后端，导致永远「未配置 api key」——已修复为各内置后端复用对应服务商 Key 的逻辑。

### Security

- 产物页表格渲染走 XSS 白名单消毒；`image_ref` 门禁阻止无引用图片的题目进入产物。

[0.8.0]: https://github.com/2710074390-cyber/medkit/releases/tag/v0.8.0
