# 工程借鉴与最小改动规则（MedKit）

> 目的：把“抄优秀工程 / 借鉴开源 agent 与 harness”落地为**可执行规则**，避免无依据复制、过度设计、依赖膨胀。
> 适用范围：所有 MedKit 开发与 Agent 任务（功能、UI、管线、打包、测试、文档）。
> 配套：需求与拆分见 `docs/0.10.0-requirement-analysis.md`、`docs/0.10.0-task-split.md`。

---

## 0. 基本原则

1. **最小改动**：能复用现有端点/状态机/DB/样式就不新增；新功能的改动面 ≤ 必要范围。
2. **先调研后动手**：先查仓库现状 + 参考项目，再写方案；禁止凭印象直接照搬。
3. **可验证**：每个改动必须有对应测试（单元/API/浏览器）与 `CHANGELOG.md` 记录。
4. **数据与包纯净**：不得把学科/题目/样例/测试数据打进安装包；学习数据只存用户本机。

---

## 1. 借鉴流程

每引入一个“借鉴点”，必须记录并随提交说明输出：

| 字段 | 说明 | 示例 |
|---|---|---|
| 参考项目 | 项目名 + URL | DSH（DeepSeek Harness） |
| 许可证 | MIT/Apache/BSD/GPL 等 | 仅借鉴思想时同样记录 |
| 借鉴点 | 具体模块/交互/模式 | subagent 分步事件、流式、断点 |
| 转化方式 | 如何映射到 MedKit | Python `orchestrator` 子步骤事件 + checkpoint |
| 落地文件 | 改动/新增文件 | `medkit/core/orchestrator.py` |

**硬性要求**：
- 禁止无来源复制整段代码；优先“看懂思想 → 本地最小实现”。
- 禁止引入缺乏许可证或与项目约束冲突的依赖（零 CDN、PyInstaller onedir、无 GPU/重量级库）。
- 借鉴外部 UI 时，只参考交互与信息架构，不直接搬运品牌/文案/资产。

---

## 2. 最小改动检查清单（每次合入前过一遍）

- [ ] 是否已 grep 现有实现，确认没有可复用的端点/函数/样式？
- [ ] 是否必须新增后端端点？能否用现有端点组合完成？
- [ ] 是否必须新增依赖？体积与许可是否可接受？
- [ ] 前端是否遵循现有组件（卡片/空态/徽章/确认弹窗/快捷键）？
- [ ] 涉及 `medkit/prompts/*.md` 是否同步 `tests/fixtures/llm_cases/` 与 `CHANGELOG.md`（NX-06）？
- [ ] 是否删除/边缘化了旧概念（如“大纲覆盖”）的残留文案与导航？
- [ ] 是否保持零 CDN、明暗主题、窄屏、打印无回归？
- [ ] 是否附带验证（pytest / 浏览器用例 / 手动截图）？

---

## 3. 优秀工程可借鉴清单（示例，持续补充）

| 对象 | 可借鉴点 | 转化方向 | 注意 |
|---|---|---|---|
| DSH（DeepSeek Harness） | subagent 编排、分步事件、流式、断点续跑 | `orchestrator.py` 子步骤事件流；`review-desk.js` 分步视图 | 借鉴思想，不引入其运行时 |
| Cherry Studio | 多服务商 Key 管理、设置壳层、配置收敛 | 现有「我的」页继续收敛 | 仅交互参考 |
| Anki | 卡面、间隔调度、导出 | `core/scheduler.py`/`cards.py` 已接入 | 仅参考交互 |
| Duolingo | 打卡、即时反馈、主动进度 | 刷题“今日进度”与按钮反馈强化 | 仅参考交互 |
| LangGraph / 开源 Agent 框架 | 图式编排、重试、状态机 | 门禁/质检重试策略 | 注意许可证（AGPL 等） |
| med-review-site | 在线题库/手册站 | 数据互通导入（WP-11） | 同源项目，先约定 schema |

---

## 4. 禁止事项

1. 不允许把学科、题目、样例、测试数据随安装包发布（见 WP-12）。
2. 不允许为“像优秀工程”引入大型依赖（torch/electron/浏览器内核等）而破坏现有构建。
3. 不允许无测试合入；不允许改动 prompts 而不同步 fixtures/CHANGELOG。
4. 不允许在新 UI 中保留已废弃概念（如“大纲覆盖”）造成用户歧义。
5. 不允许以“借鉴”为名把外部工程的业务数据/内容资产带入本仓库。

---

## 5. 落地与检查

- 每个 WP 完成时，PR/提交说明必须列出“借鉴点与来源”（`docs/engineering/borrow-rules.md` 表格）。
- `verify.cmd` / CI 至少覆盖：ruff → pytest → 浏览器（可跳过）→ 打包纯净检查（`pack/check-package.py`）。
- `docs/AGENT_HANDOFF.md` 的变更记录须记录规则遵守情况与新借鉴点。
→ 本规则随 0.10.0 任务拆分（`docs/0.10.0-task-split.md` WP-13）落地。

---

## 6. 已落地借鉴点（0.10.0 滚动记录）

| 版本/PR | 参考项目 | 借鉴点 | 转化方式（落地文件） |
|---|---|---|---|
| PR-3（WP-3） | DSH（DeepSeek Harness） | 子任务事件流 + 状态视图 + 超时重试 | `orchestrator.py`：`_substep()` 追加写 `{project}/substeps.jsonl`（status=pending/running/done/failed/retry，保留最近 200 行）；`_run_substep(ttl=60, retries=2)` 守护线程超时/重试，仍失败写人工复核清单并降级继续；`review-desk.js` `renderSubsteps()` 子步骤面板（当前阶段过滤、运行高亮、失败红标、重试提示、可展开详情）；`routers/projects.py` status 返回最近 50 条 |
| PR-2（WP-2） | DSH | 长任务进度可见性（阶段+子步骤） | `progress.json` 增 `sub/sub_done/sub_total`；stepper 空进度“准备中”+“选项校验 1/4” |
| PR-1（WP-1/WP-7） | DSH/常规桌面应用 | 多计划 + 状态来源可感知 | 开始页多场考试计划；侧栏红点 title/data-source 说明 |
| PR-4（WP-4） | 常规桌面应用/文件管理 | 删除前自动导出 + 影响范围确认 | `library.delete_subject_with_backup` 备份到 `~/.medkit/exports/` 后清理错题/知识点/复习卡/记忆卡/会话/讲解；前端“科目管理”弹层 + 确认弹窗（`learn.js`） |
| PR-5（WP-5） | Anki/Notion 类内容管理 | 分组折叠 + 多选批量 + 删除前导出 | 错题本按 科目→章节→标签 三级 `<details>` 分组；多选工具栏（全选/反选/清空/批量删除/批量已掌握/导出 JSON·MD）；`batch_delete_with_backup` 备份到 exports 后删（`learn.js` + `library.py`） |
| PR-6（WP-6） | 浏览器/搜索产品 | 可信来源白名单 + 失败原因可操作 | `websearch.py` `TRUSTED_SUFFIXES`/`TRUSTED_DOMAINS` + `trusted_filter`（可信优先/过滤）+ `【可信】` 标注；`_search_error_hint` 把 401/超时/网络/400 映射为中文；`web_search.trusted_only` 配置 |
| PR-7（WP-8） | 聊天产品/AI 写作（DSH/兼容库） | SSE 流式 + 增量渲染 + 取消 + 降级 | `LLMClient.chat_stream`（yield delta/usage/canceled）；`explain/stream`、`tutor/start/stream` SSE 端点；前端 `consumeSSE` + 增量展示；保留非流式降级 |
| PR-8（WP-10） | 文档/知识管理产品 | 原型+结构化双存储 + 完整性校验 + 角色主次 | `syllabus.structurize_outline`（LLM 抽取 + 95% 完整性 + sha1 原文存储）；教师重点=主要依据、官方306=补充（`official_quota`）；前端“大纲管理”改名与角色标签 |
| PR-9（WP-11） | 数据互通/同步产品 | 外部导出格式 + 幂等去重字段 | `library.import_site_items`：sha1(subject|chapter|question) 幂等；重复导入更新而非新增；`import-export` 端点 + 前端“站点数据(JSON)”入口 |
| PR-10（WP-9） | 静态站点/文档渲染（零依赖） | 本地 Markdown→HTML（转义优先）+ 医学关键词高亮 | `medkit/web/js/md.js`（`mdRender`/`mdHighlight`）；讲解/提问/复习卡统一富文本；表格样式 |
| PR-11（WP-12） | 发布/打包工程 | 黑名单扫描 + 发布包纯净 | `pack/check-package.py`（samples/种子/tests/`__pycache__`/`.pyc` 断言）；`medkit.spec` 移除示例与种子；`/api/sample` available=False + 前端按钮降级 |