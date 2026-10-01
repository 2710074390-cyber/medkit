# Agent 交接文档（MedKit）

> 用途：不依赖对话记忆的工程交接入口。接手的开发者/Agent 先读本文件，再按需深读
> `medkit/core/syllabus.py`、`medkit/routers/syllabus.py`、`medkit/web/js/learn.js` 与测试。
> 目标读者：**任何接手本仓库的人**；本文档按版本滚写，保留最近变更记录。

## 变更记录（最近）

| 日期 | commit | 变更 |
| - | - | - |
| 2026-10-02 | 本次 | **0.10.6 发布**：0.10.5 的产物（构建于 09-28）**早于** EP-01 图像录入 / 本地检索 / D2 / D6 / R12–R30 合入——**文件名对、内容旧**（9 处 `medkit/prompts/` 与 `medkit/web/` 下的文件与源码不一致，五件套 `--strict` 红）。原地重打包会让已分发 `SHA256SUMS.txt` 与线上包矛盾 ⇒ **走 0.10.6**。改动仅四处声明侧（`medkit/__init__.py` / `pack/version.iss` / `CHANGELOG` 收口 `[Unreleased]` → `## [0.10.6]` / `README` 安装包名）+ 重建产物；**无源码行为改动**。 |
| 2026-10-01 | `1939a64` | **D1/D2/D6 结清 + C3 门禁加固（穷尽）+ B6 降级**：① **D1 本地检索**（`0449ed5`）——`core/errsearch.py` + `GET /api/errors/search`（端点 29→30）+ 错题本检索框；**检索面严格限定在自己的数据内**（AST 守卫钉住，不含教材正文）；**不建 FTS 索引**（错题万级以下实时扫 ≈0 ms，建索引反而引入「索引与真身漂移」）；**已知边界**：缩写/别名召回（「心衰」→「心力衰竭」）**不在能力范围内**（真 FTS5 实测同样 0 命中），需别名表（D4）。② **D2 生成链剥离评估**（`b35d183`）——结论**不启动**：Python 侧双向零耦合（唯一 `vision→mineru` 是 OCR 兜底通道），真成本在**信息架构**（「题库」是一级 tab，剥离=摘掉产品入口）；把耦合固化为 `tests/test_frontend_domain_coupling.py`（首次跑即抓到 1 处真违规：`learn-review.js` 调插件域的 `letters()`）。③ **D6 双轨退役演练**（`7959503`）——临时目录跑通「JSON 轨 → 迁移 → SQLite 轨 → 回滚 → JSON 轨」，5 表**逐行 + 逐字节**无损；条件①②满足、③（≥ v0.11.x 连续两版）不满足 ⇒ **不启动**。演练照出**真缺陷**：`db.reset_conn()` 只丢引用不 close（`sqlite3.Connection` 在引用环里 ⇒ 句柄要等循环 GC，Windows 上回滚移不走 `medkit.db`）⇒ 改为确定性关闭。④ **C3 门禁加固（穷尽）**（`16567e6`→`2197a85`）——全仓扫「源码/配置文本判据」**69 处 / 19 文件** → 分诊后修 **7 个文件 / 8 条用例**（**26 条注入全绿**），修掉 **8 处假红 + 5 处 P0 假绿**（含 **CI 闸门：删 `npm ci` 步骤 / 改 `npm install` 都照样绿**——注释里天然含它旁边的命令名）。⑤ **B6 降级 + 补守卫**（`1939a64`）——「`innerHTML` 145 处」量后确认是**粗启发式**（167 处未包 `esc(` 里多为数字 / 静态片段 / **构造时**已转义）；改按风险定点：最高风险路径（**OCR/视觉读出的题面 → innerHTML**）**已全量转义但零守卫** ⇒ 新增行为级 XSS 守卫（载荷须按上下文选：textarea 是 RCDATA、input 是属性上下文）。 |① **D1 本地检索**（`0449ed5`）——`core/errsearch.py` + `GET /api/errors/search`（端点 29→30）+ 错题本检索框；**检索面严格限定在自己的数据内**（AST 守卫钉住，不含教材正文）；**不建 FTS 索引**（错题万级以下实时扫 ≈0 ms，建索引反而引入「索引与真身漂移」）；**已知边界**：缩写/别名召回（「心衰」→「心力衰竭」）**不在能力范围内**（真 FTS5 实测同样 0 命中），需别名表（D4）。② **D2 生成链剥离评估**（`b35d183`）——结论**不启动**：Python 侧双向零耦合（唯一 `vision→mineru` 是 OCR 兜底通道），真成本在**信息架构**（「题库」是一级 tab，剥离=摘掉产品入口）；把耦合固化为 `tests/test_frontend_domain_coupling.py`（首次跑即抓到 1 处真违规：`learn-review.js` 调插件域的 `letters()`）。③ **D6 双轨退役演练**（`7959503`）——临时目录跑通「JSON 轨 → 迁移 → SQLite 轨 → 回滚 → JSON 轨」，5 表**逐行 + 逐字节**无损；条件①②满足、③（≥ v0.11.x 连续两版）不满足 ⇒ **不启动**。演练照出**真缺陷**：`db.reset_conn()` 只丢引用不 close（`sqlite3.Connection` 在引用环里 ⇒ 句柄要等循环 GC，Windows 上回滚移不走 `medkit.db`）⇒ 改为确定性关闭。④ **C3 门禁加固（穷尽）**（`16567e6`→`2197a85`）——全仓扫「源码/配置文本判据」**69 处 / 19 文件** → 分诊后修 **7 个文件 / 8 条用例**（**26 条注入全绿**），修掉 **8 处假红 + 5 处 P0 假绿**（含 **CI 闸门：删 `npm ci` 步骤 / 改 `npm install` 都照样绿**——注释里天然含它旁边的命令名）。⑤ **B6 降级 + 补守卫**（`1939a64`）——「`innerHTML` 145 处」量后确认是**粗启发式**（167 处未包 `esc(` 里多为数字 / 静态片段 / **构造时**已转义）；改按风险定点：最高风险路径（**OCR/视觉读出的题面 → innerHTML**）**已全量转义但零守卫** ⇒ 新增行为级 XSS 守卫（载荷须按上下文选：textarea 是 RCDATA、input 是属性上下文）。 |
| 2026-09-29~30 | `d29380f` | **R12–R30 门禁假绿扫荡（约 20 批）+ EP-01 剩余件 + 事故留档**：① **门禁加固**——逐类修「假绿/假红」：恒真/同源自比 · 只测确定性不测语义 · **文本子串绑书写格式（→AST）** · `pytest.skip` 冒充 pass（**现全仓 0 skip**）· 魔数锁定清单 · 隐含前提未断言（TOML 段序 / `for` 空转）· 排除式判据只禁坏写法不证明好路径 · **循环遍历被测对象空转** · 零断言用例 · 聚合式空集恒真 · 死谓词 · docstring 里的词也算数。**方法论已固化为技能 `gate-falsifiability`**（写/审门禁前先加载）。② **EP-01 剩余件**——图像录入第二步「识别 → 校对 → 入库」（`4c3abd2`，端点 26→27）· Anki `.apkg` 导入解析器（`19a5411`，28→29）· Markdown 复盘笔记（`d204e22`，27→28）。③ **事故留档**——`.git/refs` 与 `objects/pack` **第二次被删**（`1c67fd9`），恢复流程固化为技能 `sandbox-git-rescue`。④ 沙箱「**同一命令执行两次**」留档（`cat >>` 追加两遍 / `git commit` 报 nothing to commit）⇒ 凡追加类写文件必须回读校验。 |
| 2026-09-28 | `21795b0` | **发布收尾 + EP-01 文档结清**：① 0.10.5 五件套同步（`89f6e24`）——`CHANGELOG` 补 `## [0.10.5]` 小节；**新增 `pack/check-release-consistency.py`**（6 项一致性门禁，已接线进 `build.bat`，失败 `exit /b 1`）；**新增 `pack/smoke-run-isolated.py`**（隔离 HOME 的产物冒烟，防污染真实 `~/.medkit`）。② `dist-installer/` 收敛 1.6 GB→218 MB（`470f44b`，22 个历史产物移入 `archive/dist-installer/`）。③ EP-01 两份文档结清（`21795b0`）——去重复段、统一数字口径、加「落地状态」列。 |
| 2026-09-28 | `72f3ea5`→ | **EP-01 错题归因流水线（阶段 1~3，随 0.10.5 发布）**：**这是自 0.10.0 以来最大的功能增量**（2473 行新代码 / 22 个 `/api/errors/*` 端点 / 2 个新提示词 / schema **v8**）。新模块：`core/errorpipe.py`（五阶段编排）· `core/kpid.py`（知识点 ID 对齐）· `core/metacog.py`（**纯函数**元认知统计）· `core/error_events.py`（append-only 流水）· `agents/error_analysis.py` + `agents/socratic_review.py` · `routers/errors.py` · `web/js/learn-meta.js`。详见 `docs/错题归因流水线_EP-01_设计与实现.md`（该文档 §7 列了 10 类「不报错的功能失效」踩坑记录）与 `docs/考研错题分析专项重构方案_2026-09-27.md`。**衔接要点见本文 §7**。 |
| 2026-09-16 | 本批 | **R7 验收 + V 批次（V-01~V-05）**：① **验收**——U-01~U-24 逐项机械核验 **23/23 通过**（U-17 部分、U-23 未执行）；反向验证 6 项「回退即红」全有效；总闸 ruff ✅ / pytest 512 ✅ / browser **35 例可跑**（上轮「浏览器层不可用」结论**已失效**）/ check-package ✅ / npm lint ✅。详见 `docs/验收与改进报告_2026-09-16_R7.md`。② **🔴 P0 发布完整性**——0.10.2 产物构建于 09-15 20:37/20:38，晚于它的四笔提交（`beecdcf` 前端崩溃修复 / `1e8cb2a` 数据管理+日志脱敏+AGPL LICENSE / `b360f90` 讲解流竞态 / `ced0f6d`）未进包；**逐字节取证**：旧产物 `learn.js` == `d0e6d10` 源码而非 HEAD。已 bump **0.10.3** 重出包，旧产物改名 `*-pre-u17` 隔离（V-01）。③ **性能**（实测驱动）：**V-02 读放大**——`dashboard` 为取一个计数全量解析 mistakes 表、并把 knowledge 表整表读两遍 → 新增 `library.count_mistakes()`（`COUNT(*)`）+ `recent_activity(kps=…)` 复用，**144.5→83.8ms**（−42%）；**V-03 单行写放大**——`_store()` 退出对脏表 `replace_all`（全表 DELETE+重插，3000 行库单次 103ms）→ 新增 `db.upsert_rows()` + `library._StoreView`（惰性读）+ `_mark_kp_row/_mark_m_row`（显式登记单行），**`record_quiz` 67.6→15.7ms**（−77%），**缺省登记集为空即退回全表替换**（宁慢不丢），等价性由 `tests/test_v03_rowwise_write.py` 6 例保证。④ **测试网**：V-04 静默 `except Exception: pass` 守卫由「≤5 预算式」收紧为「**=0 零容忍**」（反向验证：注入 1 处即红）；V-05 修 `test_cleanup_stale_sessions` **时间敏感 flake**（按列表下标取会话，跨秒时下标含义改变 → 改为按 `kp_name` 定位 + 递增时间戳确定性复现）。 |
| 2026-09-15 | `be0ffdc`→`b4ebd45` | **仓库事故恢复 + R6 工程审查批次 1**：① **事故** `.git` 被整体删除（后从回收站恢复）→ 对象库损坏（`55c4225` 对象消失、`8f98d44` 根树缺失）+ `master` 被重置回 `9a9058a`。按「备份 → 保全 → 导出悬空对象 → 重建 → 立即推送」恢复：`r5-batch0-1` 分支保全 `eb7855a`（R5 批次 0+1 唯一完整提交），悬空对象导出至 `.git/lost-found/` 并异地归档；R5 批次 2 与其余未提交工作按工作区内容重建为 `be0ffdc`；**内容零损失**。详见 `docs/reviews/仓库恢复记录_2026-09-15.md`。② **§4 陷阱清单新增 12/13/14 条**（未推送提交禁 gc/prune、提交后立即 push、并发时 git 写操作收敛单线）。③ **R6-01 总闸转绿**：`verify.cmd`/CI 单测步显式 `--ignore=tests/browser`（浏览器层 session 级 Playwright 同步上下文会占住线程事件循环，致 `asyncio.run()` 用例必失败——本地红/CI 绿的分叉根因）；`tests/conftest.py` 新增 `run_coro` fixture 并改接 3 个用例；ruff 清零 4 处 + 修正 1 处 invalid noqa。详见 `docs/工程审查改进指南_2026-09-15.md`（R6-01~R6-22）。 |
| 2026-09-01 | 本批 | **R5 全链路复核批次 0/1/2（P0/P1）**：① **数据安全** `tests/conftest.py` 补全 7 个 JSON 文件常量隔离 + session 级家目录哈希哨兵（R5-01；此前测试回落 JSON 直接写真实 `~/.medkit`，实机两次复现）；R5-04 取证修订——备份目录顶层 113KB mistakes.json 实为 08-27 20:43 后测试垃圾，真实数据（1 错题/4 知识点/1 复习卡/1 提问会话）从**污染前**备份 + WAL 回放恢复（`pack/recover-user-data.py`，先快照可回退）· ② **流式主路径** R5-02 dedupe 移入 gen() 首帧前/finally + 守卫降级 `dedupe.is_active` 窥视（实验证实本 FastAPI 版本 Depends teardown 在流完成后执行，守卫持锁会与 gen 内锁自锁）；R5-03 两流式端点 `cancel_ev` 上传 client + gen finally 置位 + `LLMClient.chat_stream` finally `stream.close()` 中止 provider 连接；R5-C-02 usage 累计→取消/异常处快照落账 · ③ **流程信任链** R5-05 提交自查三步（见 §4.11）；CHANGELOG/R4 报告 R4-01/R4-02 失实条目加勘误注 · ④ 体验兜底 R5-A-01 模板键 `medkit-tpl-<pid>`、R5-B-01 assets 累计 600MB 上限、R5-B-05/15 删除复核（残留即 500）+ 孤儿项「残留目录，可清理」；测试 18+2+2 新增，离线全量基线不回归（R5-01 哨兵同时守卫后续每次全量测试） |
| 2026-08-31 | 本批 | **R4 审查批次 3 落地（打磨，18 项 ✅）**：R4-09 子步骤 in-flight 登记 + 取消/异常出口补 cancelled/failed 终态 · R4-10 MedFix/MedQC 输入深拷贝快照隔离超时僵尸线程 · R4-11 冗余写已被 R4-05 重构消除（查证无改动）· R4-13 import-image 200MB 读后即判 400 · R4-14 meta 非 dict → 422 · R4-15 llm/models 复用 `_test_error_hint` 归一 · R4-16 未分类卡分区互斥（rev/cards list_cards + subjects 口径）· R4-17 cards_generate 复用 R3-21 去重 409 · R4-18 短 Key 掩码只露前2后2 · R4-19 自评失败卡重渲恢复 · R4-20 考前提醒真触达（窗口冲刺提示）· R4-21 全选范围明示 · R4-22 批处理在途互斥 · R4-23 切科目前置重渲 · R4-24「清同名卡」显式入口（`POST /api/library/review/purge-same`，review/cards 增 `delete_by_kp`）· R4-25 delPreset 改事件绑定 · R4-26 md.js code 段不再嵌套高亮；测试 `tests/test_r4_batch3.py`（10 例）+ 浏览器 2 例新增；离线 434 · 浏览器 36 全绿 |
| 2026-08-31 | 本批 | **R4 审查批次 2 落地（5 缺陷）**：R4-05 `structurize_outline` 完整性通过即 `add_seed_items` 幂等落库官方大纲（返回 `source`/`added`，付费产物可回读）· R4-06 资产上传 200MB 上限（`_MAX_ASSET_BYTES`，超限 400 不落盘）· R4-07 `config.save` 统一 `write_json_atomic` 原子写 · R4-08 `syllabus._rows`/`list_subjects` 切 `tx(write=False)` 纯读事务 · R4-12 `official_quota` 越界由静默钳制改 400（口径与 web_ref_quota/bloom 一致）；测试 `test_s1_backend.py`/`test_syllabus_manage.py`/`test_wp04.py`，离线 424 passed · ruff 干净 |
| 2026-08-31 | 本批 | **R4 审查批次 1 落地（流式主路径 P0/P1，4 缺陷）**：R4-01 去重锁改绑流生命周期（`dedupe.begin/end` 移入 `gen()` finally，覆盖断连 GeneratorExit）· R4-02 流式取消全链路（前端 `AbortController`+`sseStopUI`「停止生成」+切view/tab abort；服务端 `cancel_ev` 传 client、断开 set）· R4-03 断流不再自动回退非流式（仅流式接口不可用才降级，杜绝双扣费）· R4-04 tutor 流未落定空会话 finally 兜底删除；流式端口统一经 `_explain_client(cancel=...)`/`_tutor_client(cancel=...)` 注入 client（单一 mock 触点）；测试 `test_dedupe.py`/`test_explain_stream.py`/`test_tutor_stream.py`，离线 421 passed · ruff 干净 |
| 2026-08-30 | 本批 | **0.10.0 全部落地（PR-1..PR-11，13 个 WP ✅）**：多场考试/进度/门禁子步骤/科目删除/错题批量/网络检索可信源/流式讲解提问/大纲管理/外部数据导入/富文本/纯净包；工程借鉴规则随程落地 |
| 2026-08-30 | 本批 | **0.10.0 PR-11 落地**：纯净安装包：`medkit.spec` 移除示例/种子 datas；`/api/sample available=False` + 前端按钮降级；`ensure_seed` 无种子提示可上传；`pack/check-package.py` + `build.bat` 自动纯净检查；README 纯净版说明；测试 `tests/test_check_package.py` / `tests/browser/test_sample_purity.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-10 落地**：视觉与富文本输出：本地 `md.js`（`mdRender`/`mdHighlight`，先转义再解析，XSS 安全）；讲解/提问/复习卡统一富文本 + 医学关键词高亮；表格样式；测试 `tests/test_render_markdown.py` / `tests/browser/test_richtext.py`；零 CDN/零新增二进制 |
| 2026-08-30 | 本批 | **0.10.0 PR-9 落地**：外部做题数据导入：`library.import_site_items` sha1(subject|chapter|question) 幂等（命中更新）；`POST /api/library/mistakes/import-export`；错题本「站点数据(JSON)」入口 + JSON items 自动路由；测试 `tests/test_mistake_import_export.py` / `tests/browser/test_site_import.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-8 落地**：大纲管理重构：“大纲覆盖”→“大纲管理”（教师重点=主要依据 / 官方306=补充）；`structurize_outline` AI 结构化 + 原文 sha1 双存储 + 95% 完整性校验；`POST /api/syllabus/outline/structurize`；项目 `official_quota` + 出题教师主线/官方补充；测试 `tests/test_syllabus_manage.py` / `tests/browser/test_syllabus_manage_ui.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-7 落地**：沉浸式讲解/提问 + 流式（SSE）：`LLMClient.chat_stream`；`POST /api/library/explain/stream`、`tutor/start/stream`（meta→delta*→done/error，取消/失败回滚）；前端 `consumeSSE` + `#exp_live`/`#tu_live` 增量渲染，非流式降级保留；测试 `tests/test_explain_stream.py` / `tests/test_tutor_stream.py` / `tests/browser/test_tutor_immersive.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-6 落地**：网络检索可信来源（`TRUSTED_SUFFIXES`/`TRUSTED_DOMAINS` + `trusted_filter` 可信优先/过滤 + `【可信】` 标注）；`web_search.trusted_only` + 自定义域名配置；`_search_error_hint` 失败原因中文化；检索失败降级写 `run.log` + 人工复核清单；测试 `tests/test_websearch.py` / `tests/test_search_router.py` / `tests/browser/test_search_settings.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-5 落地**：错题本多选工具栏（全选/反选/清空/批量删除/批量已掌握/导出 JSON·MD）+ 三级分组折叠（科目→章节→标签 `<details>`）；批量删除自动导出 `exports/mistakes_batch_*.json` 备份；`POST /api/library/mistakes/batch-delete|batch-learn|batch-export`；测试 `tests/test_mistake_batch.py` / `tests/browser/test_mistakes_batch.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-4 落地**：刷题科目卡片图标化 + 「科目管理」弹层；删除科目前自动导出 `~/.medkit/exports/subject_*.json` 备份，清理错题/知识点/复习卡/记忆卡/提问会话/讲解；`POST /api/library/subjects/delete`（SQL 单事务/JSON 原子写）；测试 `tests/test_subject_delete.py` / `tests/browser/test_study_subjects.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-3 落地**：子步骤事件流 `substeps.jsonl`（`_substep` + `_run_substep` 超时/重试/降级）；门禁①/QC 每批/MedFix 每条 issue/渲染逐步上报；status 返回最近 50 条；前端 `renderSubsteps` 子步骤面板（运行高亮/失败红/重试/可展开）；测试 `tests/test_pipeline_events.py` / `test_pipeline_writes_substeps_e2e` / `tests/browser/test_substeps_panel.py`；借用点记录于 `docs/engineering/borrow-rules.md` §6 |
| 2026-08-30 | 本批 | **0.10.0 PR-2 落地**：出题进度子步骤模型（`progress.json` 增 `sub/sub_done/sub_total`、`PIPELINE_STAGES`）；门禁①四类检查/QC 批次/渲染产物逐步上报；stepper 空进度显示“准备中”+ 子步骤计数；测试 `tests/test_orchestrator_progress.py` / `test_pipeline_progress_substeps` / `tests/browser/test_pipeline_progress.py` |
| 2026-08-30 | 本批 | **0.10.0 PR-1 落地**：开始页多场考试计划（`renderExamPlans` 系列 + 旧键 `medkit-exam-date` 自动迁移）+ 红点来源可感知（侧栏 badge title/说明条 + 学习中心来源卡）；新增 `tests/browser/test_start_exams.py`；规划文档 `docs/0.10.0-requirement-analysis.md` / `docs/0.10.0-task-split.md` / `docs/0.10.0-work-breakdown.md` / `docs/engineering/borrow-rules.md` |
| 2026-08-29 | 本批 | **PRD/交接配置入库 + 差距审查**：`docs/archive/reviews/2026-08-29/gap-audit-prd-2026-08-29.md`（现状 vs PRD/交接配置全量对照 + 断点清单 H-1/H-2/H-3）；两份交接文档归档 `docs/archive/product/`；产品方向 5 项决策已拍板（见 §6）；批次 A 修复：H-1 脚本时序（initTab 推迟 DOMContentLoaded）/ H-2 声明序 / H-3 后端全局异常兜底 + 浏览器 hash 直达回归用例 |
| 2026-08-27 | `74d999b` | NX-02：打包环境 jieba 兜底（fts_tokens 仅 bigram、spec 收集 jieba 数据） |
| 2026-08-27 | `8b4baf4` | K3/IMP-13：官方大纲文件导入闭环（LLM 契约抽取 → seed）+ 教师重点 v4 后端（+前端官方大纲入口） |
| 2026-08-27 | 本批 | **大纲标准二选一收尾**：教师重点文件导入前端入口 + 知识点提取（`extract_teacher_kps`）+ 标准切换去「全部」档 + api() Content-Type 修复 + 本交接文档 |
| 2026-08-27 | `b321215` | NX-03：契约层闭环（MedQC 硬闭环 validate_or_repair + score=-1 人工复核；MedGen 软校验计数落 meta，概览卡可见） |
| 2026-08-27 | 本批 | **NX-04（WP-05）**：记忆卡工厂（`agents/medcards.py` + `medkit/prompts/medcards.md` + `CardDraft` 契约）+ `core/cards.py`（迁移 v5）+ `core/scheduler.py`（py-fsrs 6.3.2 默认 / SM-2 legacy 可切，创建时绑定）· 讲解产物「生成记忆卡」+ 复习计划「🧠 医学记忆卡」面板 · `export_memory_apkg` |

## 1. 大纲选择机制（v4 · 标准二选一）

从本版起，大纲标准**只有两档**（前端 `syl_std` 两个 pill，默认「教师重点」）：

| source | 含义 | 来源 | 写入路径 |
| - | - | - | - |
| `seed` | 官方大纲（西综306） | ① 内置种子 `data/syllabus_seed_306.json`（`ensure_seed` 幂等导入）；② **上传官方大纲 md/txt**（LLM 契约抽取，`/api/syllabus/seed/import-file`） | 种子文件 / `add_seed_items` |
| `teacher` | 教师重点 = **用户自供内容**（粘贴/文件/项目 teacher 切片，历史 `paste` 已由迁移 v4 归一为 `teacher`） | ① 粘贴「解析预览→确认」；② 文件上传自动处理；③ `sync_teacher` 扫项目切片 | `add_teacher_items` / `sync_teacher` |

- **标准切换语义**：`/api/syllabus/coverage?source=seed|teacher`（内部聚合用 `all`，前端不再提供「全部」档）。
- **覆盖口径**（`match_status`，零 LLM）：条目 vs 学习库「知识点名/错题主题/tag」池 →
  covered（命中）/ mastered（命中且知识点 state ∈ solid|mastered）/ pending（未覆盖）。
- **前端**：学习中心 ⑥ 大纲覆盖 → `#syl_std` 两档 pill + 粘贴卡（解析预览/确认入库/上传教师重点文件/上传官方大纲）。

## 2. 教师重点处理流程（自动处理 · 零 LLM）

```
文件(PDF文本层/DOCX/MD/TXT) ──extract.py 文本抽取──▶ 全文
  │ (扫描件PDF <200字 → mode='error'，提示先 OCR；文本文件 <20字 同样拒绝)
  ▼
import_teacher_text 两档解析（自动判定，无需用户选择）
  ├─ structured：带「章+编号条目」结构（parse_text，≥2 条）→ 章/条目层级
  └─ flat：无显式结构（讲义/PPT 式要点行）→ _teacher_items 行级提取（≥6 字，cap 200）
         → 全部挂「教师重点」章
  ▼
extract_teacher_kps 知识点提取（见下）
  ▼
add_teacher_items 幂等落库（source='teacher'，sha1 id 幂等）
```

- **接口**：`POST /api/syllabus/teacher/import`（文本一键）· `teacher/import-file`（文件，
  限 20MB，`subject` 可选 Form 字段）· `GET /status` · `sync-teacher`（项目切片同步，幂等）。
- **知识点提取**（`extract_teacher_kps`，零 LLM）：条目 → 知识点名（去「重点掌握/考点…：」
  前缀、去尾部标点、超 40 字在最后「、」收束、(subject,name) 去重保序），随导入响应
  `knowledge` 字段返回（预览展示前 10 条）。
  ⚠️ **设计边界**：知识点名**不写入学习库掌握度状态机**（掌握度仅由真实错题/判分事件驱动，
  避免凭空生成 weak 知识点涌入推荐池）；供人核 + 后续出题/记忆卡（WP-05）锚点使用。
- **前端入口**：大纲覆盖 →「粘贴/导入大纲」卡 →「上传教师重点文件」
  （accept=.pdf,.docx,.md,.markdown,.txt,.text；onchange → `sylTeacherImport`）。

## 3. 官方大纲文件导入（K3/IMP-13 · seed 通道 · 唯一 LLM 触点）

- **提示词**：`medkit/prompts/syllabus_extract.md`（逐科 one-subject JSON 契约；质量红线：不臆造/不合并/不混入章标题）。
- **契约**：`medkit/core/schema.py` `OutlineChapter/OutlineSubject/SyllabusOutline`（extra=ignore、
  空条目/空章/空科目剔除、科目名必填、条目去编号与句尾）。
- **流程**：`split_subjects`（「考查内容」锚点 + 中文数字顶级标题，兼容 Markdown `#` 前缀）
  → **逐科** `chat_json`（`max_tokens=16000` 🔑）→ 科目名归一（去尾部括号注释，如「外科学(含骨科学)」→「外科学」）
  → 保序合并；任科失败仅记 `errors`；全败返回 None → 路由回退本地 `parse_text`。
- **接口**：`POST /api/syllabus/seed/parse-file`（预览）· `seed/import-file`（预览 + `add_seed_items`
  幂等入库 source='seed'）。
- **核验结论**（`docs/archive/spikes/K3_syllabus_extract.py` + `k3_out/`）：独立解析器真值 402 条 →
  recall 100.0% / precision 96.5% / 章名 66/75 / **10 条抽样 10/10（≥80% 闸门通过）**。

## 4. 陷阱与注意事项（踩过的坑）

1. 🔑 **推理模型 token 预算**：`deepseek-v4-flash` 会把 `reasoning_tokens` 计入 `max_tokens`；
   `max_tokens=6000` 时大科（内科/外科）返回**空**（finish=length）；**≥16000 才稳定**。
   改 `extract_outline` 参数前先修这条。科目名必须随正文下发（`f"{name}\n{body}"`），否则
   模型猜名（曾把「内科学」猜成首章「诊断学」）。
2. **零 LLM 原则边界**：主流程（教师重点/覆盖判定/报告/考频）全本地零 LLM；唯一 LLM 触点 =
   官方大纲 seed 文件导入（且有本地回退）。新增功能默认本地实现，LLM 需走 ADR-003 契约。
3. **迁移 v4 不可精确回滚**：`syllabus_items` 行 id 是 sha1（不含 source），`_V4_DOWN` 为空；
   历史 `paste` 行归 teacher 后无法按行还原。
4. **前端静态路径**：拆分后静态资源挂 `/assets`（`app.mount("/assets", StaticFiles(...))`），
   链接一律 `/assets/...`；经典脚本共享全局作用域，跨文件函数加载期前向引用会挂。
   **U-17（2026-09-15）新增前端静态防线**：`npm run lint`（ESLint，`--max-warnings 0`）已入 CI。
   - 配置在 `eslint.config.js`：**不引入打包器**（产物仍是零构建、零 CDN 的原生 `<script src>`）。
   - 因为经典脚本共享全局作用域，配置**逐文件自动收集「其它文件的顶层声明 + `window.X =`/`global.X =` 挂载」**
     作为共享全局符号 → `no-undef` 能抓「拼错函数名」（原先只有运行时才炸），且不误报跨文件调用。
   - **新增顶层声明若被其它文件或内联 HTML 处理器引用，须在该文件首行 `/* exported a, b, c */` 声明**
     （脚本模式下 `no-unused-vars` 靠它识别「对外暴露」）——否则会被误报为未使用。
   - 实测价值：上线即抓到 `learn.js` 调用**不存在的 `rexAnalyzeRender()`**（运行时必抛 TypeError，
     且「确认前 200 条后」这条分支才走到，长期未被发现）。
   - **V-15（2026-09-16）按域拆分超大脚本**：`learn.js` 2401 行 → 4 片
     （`learn` / `learn-study` / `learn-live` / `learn-review`）；`review-desk.js` 2301 行 → 4 片
     （`review-desk` / `-materials` / `-project` / `-review`）。**仍是经典脚本、零构建、零 CDN**。
     ⚠️ 三条硬约束（`tests/test_v15_frontend_split.py` 已把它们变成闸门，改前端前先读）：
     1. **加载顺序即契约**：`index.html` 里 `<script>` 的顺序必须与分片族顺序一致，且**每个文件恰好一次**；
     2. **加载期不得跨片前向引用**：某片在**加载期立即调用**的函数，其定义必须在**同片或更早片**
        （函数提升只在同一脚本内生效）。拆分时 `learn.js` 首片保留了 `showLearnView` /
        `sylLoad` / `sseAbortAll` / 三个 IIFE —— 因为 `initLearnView()` 在加载期就会经
        `showLearnView` 调到后两者，**跨片即 ReferenceError**；
     3. **每片首行自带 `/* exported ... */`**（原文件的清单已按名字落位到各片）；
        新增顶层声明若被其它文件/内联 HTML 引用，务必补进去，否则 `--max-warnings 0` 会红。
     **未做 ES Module 化**（U-17 的另一半）：那会改 `index.html` 的加载语义（`type="module"` +
     顶层 `const` 不再是全局词法绑定），收益是工程整洁，风险是整站静默失效——判据（≤800 行/文件）
     已用更低风险的「纯搬迁」达成，故明确留档不做。
5. **api() 契约**：字符串体自动补 `Content-Type: application/json`（已修复，浏览器测试不再
  打 fetch 补丁）；FormData 原样透传（不设 header）。改 api() 需同步
   `tests/browser/test_syllabus_view.py`。
6. **标准切换两档**：前端 `syl_std` 只保留 `teacher`/`seed`；browser 用例
   `test_syllabus_paste_parse_confirm` 断言「无 all 档」。改档位需同步该用例 + `sylRender`
   空态文案（learn.js 的 `stdName`/提示分支）。
7. **多 Agent 并发**：本仓库曾出现两个会话并发改同一批文件导致互相覆盖/回滚（git clean 会删
   未跟踪文件）。建议：长任务改完**立即提交**；重要产物放跟踪路径；勿用 `git clean`。
10. **旧实例勿并行（数据分叉）**：JSON→SQLite 双态下，**旧版本实例**可能在 SQL 模式建立后继续
   往 JSON 写活数据（`mistakes.json` 一度 182 条 vs DB 0 条），`import_from_json` 现已按 id
   幂等补导兜底（一次性 imported 门禁已移除）。排查学习中心数据缺失时先查
   `~/.medkit/library/mistakes.json` 与 `medkit.db` 行数是否一致；改存储逻辑后用**新实例**并
   关闭旧实例，避免双写。
8. **verify.cmd**：`ruff → pytest → 浏览器(Playwright)`；本地无浏览器/无网时
   `SET SKIP_BROWSER=1` 跳过浏览器层（CI 不含浏览器层，仅本地门）。
9. **教师重点文件处理**：extract.py 对「扫描件 PDF（无文本层）」直接拒绝（mode='error' 提示先
   OCR）；本机未下载 MinerU 通道前，扫描件走外部转换（如 WPS/OCR）再上传 md/txt。
11. **修复类提交自查三步（R5-05，2026-09-01 血泪）**：R4-01/R4-02「已落地」记载被 R5 复核
    实锤为从未进入提交（`8e603a0` 提交信息写着「dedupe 移入 gen() finally」，diff 实际引入的是
    FastAPI Depends 守卫；CHANGELOG/AGENT_HANDOFF/R4 报告三处同步失实，用户以为已修）。
    此后每个修复提交必须：① 提交前 `git diff --cached` 对关键标识 grep（如 `dedupe.begin`
    应出现在 `gen()` 邻域；`cancel_ev` 应出现在端点函数内）；② CHANGELOG/handoff 条目只引用
    「已 diff 验证的 file:line」，引用意图=失实源头；③ 批量提交后立即 `git show HEAD` 抽查关键行
    （配合本节第 7 条「改完立即提交」——批次全部落库前不要宣布落地）。
12. **⚠️ 有未推送提交时，禁止 `git gc` / `git prune` / `git repack -a -d`（2026-09-15 事故）**：
   本仓库 `.git` 曾被整体删除后从回收站恢复，造成对象库损坏（提交 `55c4225` 对象消失、
   `8f98d44` 根树缺失）。这类残留对象**是唯一的可恢复来源**，一次成功的 gc 或 git 自动触发的
   `geometric-repack` 就会把它们清掉（事故当时 `git count-objects -v` 的 `prune-packable: 98` 即引信；
   实测提交时会自动触发 repack 并因缺失对象失败，**成功即毁灭证据**）。
   规则：① 存在未推送/悬空提交时保持 `gc.auto=0`、`maintenance.auto=false`、`gc.autoDetach=false`；
   ② 恢复期只做只读命令（`status/log/rev-parse/cat-file/fsck`）+ 整仓备份，禁止一切写引用/写对象操作；
   ③ 用 `git fsck --lost-found` 导出悬空对象并**异地归档**后，才考虑恢复自动维护。
13. **提交后立即 push**：未推送的提交只存在于本地对象库，是单点故障——2026-09-15 事故中 R5 三个提交
   （含 P0 数据安全修复）正是「改完不推」才在 `.git` 损坏后无法从远端找回。与第 7 条合起来即
   「改完 → 提交 → **立刻推送**」闭环；`git rev-parse origin/master` 必须等于 `HEAD`。
14. **多会话并发时，git 写操作必须收敛为单线**：动手前 `git status` + `git log -1` 与本会话预期不符
   → **立即停手取证，不要继续写**。实测（2026-09-15）：两个会话同时做仓库恢复，导致
   `refs/remotes/origin/*` 被反复删除、悬空对象面临被二次 gc 的风险——同一 `.git` 只能有一条恢复线。

15. **修复类改动必须「先红后绿」（U-12，verify-the-test-fails）**：写修复代码**之前**先写出一个
    会失败的用例（或断言），跑出红；再改代码到绿。理由见第 11 条——R5-02 的失实正是「测试按
    修复版写法写」，红都没红过就无法证明它在测缺陷。对关键接缝（去重锁、取消事件、门禁剔除、
    打包纯净）另加**结构性防丢断言**（断言源码里存在该结构，而非断言行为）：见
    `tests/test_stream_wiring.py`。反向验证同样适用于改进项：临时回退该修复，用例必须变红。

## 5. 常用开发入口

- 测试：`python -m pytest tests/ --ignore=tests/browser -q`（单元）· `python -m pytest tests/browser -q`（浏览器，需先 `pip install playwright && playwright install chromium`）。
- 总闸：`verify.cmd`（Windows）。
- 覆盖率（U-12）：`python -m pytest -q --ignore=tests/browser --cov=medkit --cov-report=term-missing`；
  2026-09-15 实测基线 **82%**，CI 门槛 `--cov-fail-under=80`（只升不降）。
- 单测隔离：`tests/conftest.py` 把 `dbs.DB_PATH` 等指向 tmp；新增库表/迁移需同时覆盖 `tests/test_db.py`（migration 标记）。
- 打包：`pack/build.bat`（PyInstaller，`medkit.spec`；version 单源 `medkit/__init__.py`）。
- 规划（0.10.0）：需求整理 `docs/0.10.0-requirement-analysis.md` · 任务拆分 `docs/0.10.0-task-split.md` · 工作包细化 `docs/0.10.0-work-breakdown.md` · 工程借鉴规则 `docs/engineering/borrow-rules.md`。

### 5.1 本地总闸 与 CI 步骤对应表（U-12，消除「CI 绿 / 本地红」分叉）

| # | 本地 `verify.cmd` | CI verify job | 说明 |
|---|---|---|---|
| 1 | `python -m ruff check .` | `Lint` | 静态检查 |
| 2 | `python -m pytest -q --ignore=tests/browser` | `Test`（+ `--cov --cov-fail-under=80`） | **两侧都必须显式 `--ignore=tests/browser`**；否则本地会因 session 级 Playwright 同步上下文占住事件循环而必红（R6-01） |
| 3 | `python -m pytest tests/browser -q`（可 `SKIP_BROWSER=1`） | 独立 `browser` job（ubuntu + chromium） | 浏览器层单独进程/单独 job |
| 4 | `python pack/check-package.py` | `Package purity check` | 打包纯净检查（R6-11） |
| 5 | — | `Migration tests`（`-m migration`） | 迁移升级/回滚/幂等 |
| 6 | — | `Dependency integrity`（`pip check`） | 依赖冲突 |
| 7 | — | `Dependency vulnerability audit`（`pip-audit --strict`，U-16） | 已知 CVE |

> 判据：**任一步在本地与 CI 的定义不一致即为分叉**。新增闸门步骤时，本表两侧必须同批更新。

## 6. 产品方向（2026-08-29 交接 · 已拍板决策）

> 来源：`docs/archive/product/medkit-agent-handover-2026-08-29.json`（执行阶段 PHASE-1~5 / API 契约 /
> 风险）+ `docs/archive/product/medkit-prd-v1.0.md`（PRD：仪表盘/4Tab/卡翻/三按钮/双场景/视觉规范）。
> 全量差距对照见 `docs/archive/reviews/2026-08-29/gap-audit-prd-2026-08-29.md`。

1. **产品形态 = 桌面形态下的卡片刷题**（不转移动端范式）：保留现有侧栏与桌面广度，
   以「卡片翻转 + 底部三按钮」补齐刷题沉浸感；不做底部 Tab/手势优先的全屏移动化。
2. **API 契约以现有实现为准**（复用 `/api/library/review/today`、`/api/library/review/grade`
   等，文档对齐而非新增端点）。交接配置里的映射：`GET /api/today-tasks` →
   `GET /api/library/review/today`（`{cards,total,stats}`，stats.total/new/due/in_progress/review）；
   `POST /api/review/feedback {task_id,rating,timestamp}` → `POST /api/library/review/grade`
   `{card_id,quality}`（quality 0~5，next_review/interval 在返回的 `card.due/interval/state` 内）。
3. **rating 保留 0~5/四档**（FSRS 四档 + SM-2 六档），三按钮（忘/糊/记）做前端映射，不在调度器加档位。
4. **真题来源标注本期做**：题目契约/管线支持可选 `source_year/source_type`，考频解析年份维度，
   渲染标签 + 筛选器（无标注题显示「未标注」）。
5. **交接文档归档入库**（已做，本目录 `docs/archive/product/`）。

### 执行批次（2026-08-29 已全部落地，提交 `e1332a3`→`f510b9f`→`49fa1df`→`950875d`→`c912df7`）

- **批次 A（止血收尾）✅**：H-1 hash 直达脚本时序（initTab 推迟 DOMContentLoaded + 主 Tab 选择器收窄 `[data-tab]`）· H-2 声明序 · H-3 后端全局异常兜底（`main.py` `_unhandled_exception`，INTERNAL_ERROR）——浏览器回归用例 `test_hash_direct_navigation_initializes_tab`。
- **批次 B（导航重组）✅**：5 Tab IA（开始/刷题/题库/学习中心/我的）+ 开始仪表盘（今日任务四卡 + 开始学习大按钮 + 考试倒计时 localStorage `medkit-exam-date` + 最近项目）+ P2 收纳进「我的」；**修复 renderReview 全局重名**（学习中心复习卡列表自 `2b572d1` 起静默不渲染的潜伏 P0）；设计文档 `docs/archive/design-specs/2026-08-29-ia-restructure.md`。
- **批次 C（卡片化刷题）✅**：复习卡/记忆卡 3D 翻转卡 + 三按钮（忘红/糊黄/记绿；映射：SM-2 忘0糊2记4、FSRS 忘0糊2记3）+ 快捷键 1/2/3 + 今日进度条 + 科目卡片（`/api/library/subjects` 增 stats）+ 解析关键词高亮（12 词，`learn.js hlKw`）；浏览器用例 `tests/browser/test_study_quiz.py`。
- **批次 D（视觉统一）✅**：主色青绿（浅 #2A6B5A / 深 #3aa58c）+ 正文浅色 #2E3440 + 字号四级 21/16/14/12（.ptitle/.card h2/.cardh h2/body/.hint）。
- **批次 E（真题标注）✅**：迁移 v6 `realexam_freq.year`（幂等升级）+ 段落/句子年份提取 + `QuestionItem.source_type/source_year` + 三处标注接入（orchestrator 写回 / review.py 读取兜底 / 审核前补齐）+ 题库/押题卷/审核台「20XX 真题」标签 + 年份筛选器（题库 localStorage 记忆、审核台下拉）。
- **批次 F（RAG 无原文回退）**：讲解/提问/复习提示三处检索未命中 → 「先说明 + 联网补充 + 模型知识输出」；`explain_knowledge` 返回 `grounded`、讲解产物存 `grounded`、tutor 响应带 `grounded`/`note`、`_resolve_search_fn()` 统一后端解析、MedTutor 注入 `web_materials`；前端复习卡提示一键「结合网络与模型知识生成提示」（成本前置）、讲解卡片「无教材原文」标签、tutor 首问/判分提示。
- 待办（后迭代）：每日推送上限/考前加大强度（BE-2）· 速刷手势 · 统计图表 · 离线 SW · 虚拟滚动。
- 验证：每批 `verify.cmd`（ruff → pytest 单元 → 浏览器）全绿后立即提交（见 §4 第 7 条多 Agent 并发警示）。

## 7. 错题归因流水线（EP-01）· 接手必读

> 功能全貌见 `docs/错题归因流水线_EP-01_设计与实现.md`（**该文档 §7 是核心价值**——
> 记录了 10 类「不报错的功能失效」的踩坑过程）。本节只讲**接手时必须知道的约束**。

### 7.1 三条不可动的设计红线

| 红线 | 是什么 | 违反后果 | 守卫 |
|---|---|---|---|
| **答案只能由用户提供** | `error_analysis` / `socratic_review` **绝不判对错** | 归因可信度崩塌（《总纲》§3.2 红线） | `tests/test_socratic.py` 泄漏用例 + `_strip_answer_echo` 出口剥离 |
| **confidence 不可补填** | 录入时必填；**无补填端点**，PUT 也拒改该字段 | 校准曲线失真，元认知数据资产作废 | `test_guard_actually_catches_backfill_route`（路由表扫描，**注入反向验证过**） |
| **AI 标签与人工标签并列** | `error_tag`（人）与 `ai_error_tag`（AI）**分列存储**，派生 `tag_match` | 覆盖其一即失去「不一致样本」这个最高价值数据 | `metacog.agreement()` 端到端用例 |

### 7.2 三个「看着能用其实不通」的已知断点（已修，但同类错误易复发）

它们都属于**静默降级**——不抛异常、不写日志、功能看起来在跑。**新增代码时优先怀疑这三处**：

1. **`hasattr` 探测不存在的函数** → 恒 `False` → 静默返回空。
   实例：`_freq_map()` 探测 `realexams.list_freq`（真名是 `freq_view`）→ 减法清单的
   `freq_missing` **永远为 `True`**。**禁止用 `hasattr` 做能力探测**；直接调用存在的函数，
   异常走 `errs.swallow`。
2. **白名单字典静默丢弃新键** → `library.add_mistake` 的 record 是固定形状，
   `confidence`/`my_reasoning` 全被丢掉但 API 返 200。
   **新增元认知字段必须同时改 `_META_FIELD_DEFAULTS` + `routers.library.MistakeBody`**。
3. **`list(dict)` 静默丢文本** → `list({"A": "增加"}) == ["A"]`，选项文字全丢且不报错。
   JSONL 的 `options` **是 dict**，唯一格式转换点是 `errorpipe._norm_options()`。

### 7.3 源码扫描类守卫的强制写法

本项目已**第八次以上**踩同一个根因（见实现说明 §7.5/§7.7/§7.9/§7.10，以及 2026-10-01 的
C3 穷尽扫荡：全仓 69 处候选 → 修 7 文件 / 8 用例，**8 处假红 + 5 处 P0 假绿**）。
完整方法论在技能 **`gate-falsifiability`**（写/审门禁前**先加载它**）。以下是最硬的五条：

> **① 判「源码里有没有某结构」→ 一律 AST，不用文本子串/切片。**
> 子串会被**注释**骗过（而且注释里**天然**含它旁边的命令名——人写注释就是为了解释那行）；
> 位置切片会被**无关字符**截断（注释里的一个 `]`、一个 `###` 都会）。
> 调用用 `ast.Call`（`Name` / `Attribute` **两种形态都要认**）；函数体用 `FunctionDef`；
> docstring 用 `ast.get_docstring()`。**同文件里别只改一处**——迁移漏改是常见形态。
>
> **② 只能扫文本时，先明确层次，再动手。**
> - 扫**源码** → 先剥注释（**多行 `/* */` 先于单行 `(?<!:)//`**），再抹掉字符串字面量；
> - 扫 **UI** → 只扫**控件**（`button`/`a`/`label`）的可点击文本，**不扫说明文案**；
> - 扫**配置文件**（CI / YAML / spec / bat）→ **先剥注释**（`line.split("#", 1)[0]`）。
>
> 顺序写反或层次搞错，会**同时**造成假绿（删代码留注释）与假红（注释里解释了原因）。
>
> **③ 边界串必须是「完整标记」，不能是它的前缀。**
> `"## "` 会被 `"### 子标题"` 匹配（含子串）⇒ 小节正文被**提前截断**；
> 正确写法 `re.split(r"^## ", …)`。同类：`"<tag>"` vs `"<tag attr>"`、`"=="` vs `"==="`。
>
> **④ 不要按行过滤找「某调用的实参」。**
> `[ln for ln in seg.splitlines() if "f(" in ln]` —— 把调用改成**多行**、参数落到续行，
> 循环体一次都不命中 ⇒ **假绿**（实测：icacls 调用改多行 + `/grant:r`，守卫照样绿）。
> 要取 AST 节点的实参，递归进 list / tuple / 关键字参数。
>
> **⑤ 判据别带「当时那一种写法」的前缀。**
> 想拦「不许出现 `npm install`」就写 `re.search(r"\bnpm\s+install\b", code)`，
> **别**写 `"|| npm install" not in text`——后者只是当时那一种写法，
> 把 `npm ci` 改成 `npm install` 照样绿（实测）。
>
> **一句话：判据要绑「这件事」，不要绑「它当时的写法 / 它旁边的文字」。**

**注入证伪时，三个位置都可能打偏 —— 任一打偏，验证就退化成安慰剂：**

| 打偏的位置 | 伪装成 | 纪律 |
|---|---|---|
| **锚点** | 替换成功、自检全过、**报绿但什么都没测**（命中了同名但非目标的位置，如 docstring 里的同名字符串） | 注入前断言 `text.count(anchor) == 1` |
| **语法** | `rc=4`（**收集失败**，不是测试失败）——「红了」但毫无意义 | 判据必须是 `rc == 1` |
| **载荷** | 判据**恒绿**（载荷在该上下文里触发不了坏结果，如 `<textarea>` 是 RCDATA，喂 `<img onerror>` 被当纯文本） | 按上下文选载荷（textarea 需 `</textarea>` 闭合逃逸；input 需 `"` 逃逸） |

> 三者都是「**先怀疑自己的注入，再怀疑守卫**」。实测已各踩一次以上。

### 7.4 v8 迁移

- `MIGRATIONS = [1..8]`；`mistakes` +6 列 + `kp_alias` / `error_events` 两新表。
- **必须幂等**：v8 分支先 `PRAGMA table_info` 探测列，否则测试模拟旧库时抛 `duplicate column`。
- `_V8_DOWN = []`（`ALTER ADD COLUMN` 不可精确回滚）→ **依赖 ADR-005 升级前自动备份**。
- 新模块（kpid/error_events）**不提供 JSON 回落分支**，一律 `dbs.migrate()`（对齐 ADR-006）。

### 7.5 本地检索（阶段 4）的已知边界 —— **别当 bug 修**

`core/errsearch.py` + `GET /api/errors/search`（错题本检索框，2026-10-01 落地）：

- **检索面严格限定在自己的数据内**（题干 / 想法 / 错因 / 修正 / 解析 / 科目章节），
  **不含教材正文**（《总纲》§2.4）——有 AST 守卫钉住；
- **不建 FTS 索引**：错题万级以下实时扫一遍 ≈0 ms，建索引要动 schema 且引入
  「索引与真身漂移」这一**新失效模式**；
- 排序**先覆盖率、再分数**；片段**不取自答案字段**（免得搜索页变成「答案速查」）；
  高亮**先 esc 再包 `<mark>`**。

⚠️ **缩写 / 别名召回不在能力范围内**：「心衰」搜不到「心力衰竭」——
**缩写不是前缀**（「心力衰竭」前两字是「心力」），真 FTS5 实测同样 **0 命中**。
这是**已知边界，不是待修的 bug**；要解决只能上**别名表**（待办 **D4**，需用户提供常用表述）。

**两条已经试过、确认无效的路（别重走）**：
① 分词**前缀**扫描——**严格弱于子串匹配**（jieba token 与二元组都是原文的连续子串），
一次都救不回东西，只白花 ≈340 ms/千条；② **二元组兜底**——在**子串**匹配里它管的是
「部分重叠召回」，不是「词内查找」（「湿啰音」命中「中细湿啰音」靠的是**子串**）。

### 7.6 产物冒烟的正确姿势（血的教训）

**任何启动产物的冒烟测试必须走 `pack/smoke-run-isolated.py`。**

起因：2026-09-28 我直接启动解包后的安装包做冒烟，`main._lifespan` 里的 `dbs.migrate()`
**把用户真实 `~/.medkit` 的库从 v7 升到了 v8**（`USERPROFILE` 指向真实家目录）。
零数据损失（自动备份在），但这是**和 2026-09-15 毁 `.git` 同一个错误模式：
动手前没隔离会影响用户真实数据的作用域**。

该脚本会重定向 `USERPROFILE` / `HOME` / `MEDKIT_LOG_DIR` 到临时目录，
并扫 **127.0.0.1:4880-4889** 的 `/api/health` 判就绪。

**注意两个反直觉点**：
- `MEDKIT_PORT` 是**输出不是输入**——`run_medkit.py::pick_port()` 会硬扫 4880-4889 并**覆盖**它；
- 环境里有 `http_proxy=127.0.0.1:62171`，探测 **127.0.0.1 必须用无代理 opener**，否则 502。
