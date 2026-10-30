# Agent 待办清单（2026-10-30）

> 用途：交给下一个接手的 Agent。已完成项目已归档到
> `docs/superpowers/archive/2026-10-30/`（设计 + 计划，含完成度表与未做项）。
> 本清单只列**还没做**的事；已完成的不要重做，尤其别再动同步协议与去重规则。
>
> 目标仓库：`c:\Users\38063\Desktop\medkit-web-contest`（网页版源码与账号服务）。
> 本文所在仓库（`medkit`）只是文档与桌面版源码，**不是**网页版源码仓库。

## 0. 交接必读（避免踩坑）

- 任何 `js/*.js`、`css/*.css` 改动后必须跑 `python tools/build_bundle.py`，否则 `index.html` 仍指向旧产物；`--check` 必须绿。
- 全量守卫：`node --test tests/*.test.mjs`（当前 118 项）、`python -m pytest tests/test_server.py -q`（当前 25 项）、`python tools/build_questions.py --check`。
- 经典脚本共享全局作用域：新增顶层函数若被 HTML 内联 `on*` 调用，必须保持顶层声明；跨文件加载期前向引用会直接炸。
- 同步白名单在 `js/store.js` 的 `syncKeys` 与 `server.py` 的 `ALLOWED_STATE_KEYS` **两处**，新增学习数据键要同时登记，BYOK / 主题 / 设备偏好**永远不要**加进去。
- 账号数据目录永不静态托管；同步接口的大小上限（4.5 MB）与同源写校验不要放宽。
- 未提交改动里还有两项与本次无关：`README.md`、`tools/build_questions.py`（题库种子改为本仓 `seed/banks.json` 单源），以及未跟踪的 `docs/1024造物挑战_参赛手册与文案.md`。不要顺手回退它们。

## A. 承接本批（P1，建议优先）

### A1. 把速记卡并入「今日复习」统一队列

- **现状**：速记卡已能生成、查看、按 SM-2 评分（`Store.gradeQuickNote` / `Store.dueQuickNotes`），
  但「今日复习」列表只读错题（`Store.getDueReview` → `renderDue`），速记卡只能从错题详情或书架进入。
- **文件**：`js/store.js`（`getDueReview` / `dueQuickNotes`）、`js/app.js`（`renderDue`、`doRetryDue`、侧栏徽标 `refreshBadges`）。
- **做法**：
  1. 在 `renderDue` 顶部并列取 `Store.dueQuickNotes(cap)`，渲染为「速记卡」区（紧凑摘要：原题摘要 + 待复习状态）。
  2. 卡片内复用现有四档评分（`Store.gradeQuickNote(id, 0|2|4|5)`），不要复制调度数学——SM-2 只在 `Store._applyGrade` 一处。
  3. 徽标计数改成「到期错题 + 到期速记卡」，两处都要有可用空态文案。
  4. 速记卡没有选项，不要塞进 `bankDrillStartMistakes`（那是判分卷）；只用评分按钮。
- **验收**：到期速记卡出现在今日复习；评「记得」后离开列表；撤销入口不因列表变化消失（与 U07 一起验）。
  新增 Node 用例覆盖「有卡/无卡/已掌握/上限截断」，并加浏览器检查（390px 无横向溢出）。

### A2. 变式题去重升级（可选，先验证再动手）

- **现状**：精确与结构去重（题干归一、选项排序、数字替换）已生效；语义层只是提示词约束 + 本地规则，
  模型仍可能给出「同问法不同措辞」的题。
- **约束**：不引入向量服务、不新增依赖、不发额外 API 请求（BYOK 成本敏感）。
- **做法（优先级从低风险到高风险）**：
  1. 字符 bigram Jaccard（与桌面版 `core/kc.py` 同口径，阈值 ≥0.85）作为第三道指纹，加在
     `_variantAccept` 之前；写入 `tests/variant-generation.test.mjs`。
  2. 记录 `variation_type`（病例迁移 / 诊断依据 / 下一步处理 / 鉴别对比 / 机制解释），同类型同错题最多 1 道；
     解析失败时不猜类型，按普通题处理。
  3. 明确**不承诺**语义等价判定；UI 文案保持「本地规则尽力拦截」。
- **验收**：注入「同问法换措辞」样本必须被拦；正常不同角度题不被误杀（至少 2 条正样本）。

### A3. 同步收口（仅在确认有旧客户端时做）

- **现状**：不带 `base_revision` 的旧客户端仍可写入，无法防并发覆盖；新客户端已走 409 + 合并重试。
- **做法**：先查线上 `user_state.updated_at` 与 `revision` 分布（无版本写入会保持 revision 不变），
  确认仍在用旧前端后再加「无版本写入记 `legacy_write` 标记 + 日志」，不直接拒绝（避免旧页面彻底停摆）。
- **验收**：旧格式 PUT 仍 200 且落标记；新格式行为不变；`tests/test_server.py` 两条契约同时绿。

## B. 网页版 UX 台账（已有独立计划，不要在这里重复设计）

- 文件：`docs/superpowers/plans/2026-10-30-medkit-web-ux-optimization.md`（U01–U13 台账 + 阶段 A–E）。
- 仍未实施的重点：U04/U05（恢复会话丢进度，P0）、U06（备份遗漏键，P0）、U02/U03/U11（完成态与未答数）、
  U12（错题按科目直接刷）、U13（今日复习默认折叠）。
- **要求**：每条先复现再修；先写失败用例；不要以静态推断直接改行为；不要做题目稳定 ID 迁移。

## C. 工程与发布（做完 A/B 再执行）

- [ ] `python tools/build_bundle.py && python tools/build_bundle.py --check`
- [ ] `node --test tests/*.test.mjs`、`python -m pytest tests/ -q`
- [ ] `python tools/build_questions.py --check`
- [ ] 真浏览器走查（1440 + 390）：书架两张纸条、速记卡生成/复习/删除、错题详情、多设备登录合并
- [ ] 发布前确认账号数据目录不在静态挂载点、`/api/me` 未登录返回 401 属预期
- [ ] 更新 `README.md` 的功能清单（若新增了今日复习里的速记卡）

## D. 明确不做 / 已否决

- 不给 AI 生成内容加正文编辑器（第一阶段只做查看 / 练习 / 删除，避免破坏溯源）。
- 不把内置题库改成可删除（只允许停用）。
- 不在客户端做「无同步账号的假登录」；没有 `/api/me` 就回退本机模式。
- 不为速记卡单独再造一套复习算法；也不为变式题改写刷题引擎。
- 不提交与本次无关的 `README.md` / `tools/build_questions.py` / 参赛手册文档。

## E. 快速定位

| 想改什么 | 先看哪里 |
|---|---|
| 同步协议 / 冲突 | `server.py` `AccountStore.put_state`、`js/auth.js` `syncNow` / `applyDocument` |
| 同步键登记 | `js/store.js` `syncKeys`、`server.py` `ALLOWED_STATE_KEYS` |
| 变式题 | `js/app.js` `_variantAccept` / `vaultVariant` / `_renderVariantList` |
| 速记卡 | `js/store.js` `saveQuickNote` / `gradeQuickNote`、`js/app.js` `vaultQuickNote` / `_renderQuickNote` |
| 书架 | `js/app.js` `renderShelf` / `shelfFoldToggle`、`css/app.css` `.shelf-group` |
| 今日复习 | `js/app.js` `renderDue`、`js/store.js` `getDueReview` |
