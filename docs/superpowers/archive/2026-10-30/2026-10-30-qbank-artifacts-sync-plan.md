# 出题工坊复习产物与账号同步实施计划

> **For agentic workers:** 按任务逐项执行；每个任务先写失败测试，再实现最小改动并验证。

**Goal:** 修复网页版账号跨设备数据同步，并增加原错题归属的变式题、知识点速记卡和可折叠书架分类。

**Architecture:** 保持 `localStorage` 作为浏览器缓存、每用户 SQLite 作为账号持久层；把同步从整快照覆盖改为带 revision 的实体合并。生成产物统一使用稳定 ID、原错题来源 ID和软删除标记，复用现有 Store/错题/刷题入口，不重写刷题引擎。

**Tech Stack:** FastAPI、SQLite、原生 JavaScript、Node `node:test`、pytest、Playwright。

**状态：已完成并归档（2026-10-30）**，实现提交 `0e29d9b`。各任务完成度见下表；未完成项已移交 `2026-10-30-agent-todo.md`，本文件不再继续执行。

| 任务 | 状态 | 说明 |
|---|---|---|
| Task 1 同步注册表与产物契约 | 已完成 | 同步键集中登记；产物以原错题 id 为键 |
| Task 2 变式题去重与 3/5 上限 | 已完成 | 精确 / 结构去重，语义层仍是本地尽力拦截 |
| Task 3 速记卡与复习关系 | 已完成（复习入口在详情与书架） | 并入「今日复习」队列未做 |
| Task 4 服务端 revision 与冲突合并 | 已完成 | 409 + 最新状态，过期写不落库 |
| Task 5 前端可靠同步与跨设备拉取 | 已完成 | 冲突合并重试、页面隐藏同步、30s 轮询 |
| Task 6 书架分类与折叠 | 已完成 | 原生 details，折叠状态随账号同步 |
| Task 7 错题详情端到端联调 | 已完成 | 浏览器实测：书架行 → 详情 → 速记卡六段 |
| Task 8 全量回归与文档同步 | 部分完成 | Node 118 / pytest 25 / bundle 与题库校验通过；本仓无 CHANGELOG |

---

## 文件地图

- 修改 `c:\Users\38063\Desktop\medkit-web-contest\server.py`：账号状态 revision、同步变更协议、服务端合并和兼容旧快照。
- 修改 `c:\Users\38063\Desktop\medkit-web-contest\js\store.js`：同步键注册表、实体合并、产物存储、去重指纹和软删除。
- 修改 `c:\Users\38063\Desktop\medkit-web-contest\js\auth.js`：revision 同步、待同步状态、定时拉取、页面离开同步。
- 修改 `c:\Users\38063\Desktop\medkit-web-contest\js\app.js`：书架分组、折叠状态、产物入口刷新。
- 修改现有错题详情 JS 文件：在错题完成后展示确认式变式题/速记卡生成入口；先定位实际函数后再改，不新建重复入口。
- 新增或修改 `tests/test_server.py`、`tests/account-sync.test.mjs`、`tests/learning-loop.test.mjs`、`tests/browser/*`：同步、产物、书架和移动端回归。

## Task 1: 固化同步数据注册表与产物数据契约

**Files:**
- Modify: `js/store.js`
- Test: `tests/account-sync.test.mjs`

- [ ] 写失败测试：快照包含复习上限、今日完成状态、生成产物和书架折叠状态；不包含 `mk_web_byok`、`medkit-theme`、设备级自动跳题设置。
- [ ] 写失败测试：变式题必须带 `artifact_id`、`source_mistake_id`、`artifact_type`、时间戳和 `deleted_at`；速记卡同样带来源关系。
- [ ] 运行 `node --test tests/account-sync.test.mjs`，确认新断言失败。
- [ ] 在 `Store.syncKeys` 旁集中声明账号级同步键和设备级排除键；所有新数据只能通过该注册表进入快照。
- [ ] 增加 `Store.artifacts()`、`Store.upsertArtifact()`、`Store.deleteArtifact()`、`Store.artifactsForMistake()`，删除采用 tombstone 而不是物理移除。
- [ ] 增加 `artifactId` 稳定生成规则：同一来源错题、类型和批次不重复；现有数据无 ID 时迁移时补齐。
- [ ] 运行该测试文件，确认通过。

## Task 2: 实现变式题候选去重和 3/5 上限

**Files:**
- Modify: `js/store.js`
- Modify: `js/learn-vault-chat.js` 或实际错题详情 JS（以代码搜索结果为准）
- Test: `tests/learning-loop.test.mjs`

- [ ] 写失败测试：同一原错题已有 5 道有效/已删除变式题时禁止再次生成；默认目标数量为 3；单批请求最多 3 道。
- [ ] 写失败测试：完全相同题干、仅替换年龄/数字/病名、仅选项换序、同一问法类型均被拒绝。
- [ ] 写失败测试：有效候选不足时返回实际数量和剔除原因，不补重复题。
- [ ] 运行 `node --test tests/learning-loop.test.mjs`，确认失败。
- [ ] 实现文本归一化、精确指纹和结构指纹；语义层先复用项目已有的字符串相似度工具，不引入新的远程向量服务。
- [ ] 实现 `Store.filterVariantCandidates(sourceMistakeId, candidates)`：先查历史含 tombstone 的指纹，再按变化类型和总上限筛选。
- [ ] 在现有变式生成确认入口接入筛选结果；生成前显示当前数量/剩余额度，用户确认后才调用模型；模型失败不写半成品。
- [ ] 生成结果写入统一 artifact，同时保留题目可刷题字段和来源错题关系。
- [ ] 运行测试并确认通过。

## Task 3: 增加速记卡与复习关系

**Files:**
- Modify: `js/store.js`
- Modify: `js/learn-vault-chat.js` 或实际错题详情 JS
- Test: `tests/learning-loop.test.mjs`

- [ ] 写失败测试：同一原错题只保留一个有效速记卡；重复生成更新已有卡或拒绝，不新增第二张。
- [ ] 写失败测试：删除速记卡会删除对应复习状态，但原错题和变式题保留。
- [ ] 写失败测试：变式题答错更新原错题，答对只记录迁移证据，不直接标记原错题已掌握。
- [ ] 实现速记卡字段：核心结论、考点、易错点、鉴别要点、记忆锚点、教材依据、主动回忆问题和来源 ID。
- [ ] 把速记卡接入现有复习列表/排期函数；不复制 FSRS/SM-2 算法。
- [ ] 在错题详情增加“生成速记卡”确认入口、查看/复习/删除入口。
- [ ] 运行测试并确认通过。

## Task 4: 服务端 revision 与冲突合并

**Files:**
- Modify: `server.py`
- Test: `tests/test_server.py`

- [ ] 写失败测试：旧客户端仍可 PUT 旧格式 `{"state": ...}` 并被兼容处理。
- [ ] 写失败测试：新客户端携带 `base_revision` 和变更状态时，服务端返回新 revision。
- [ ] 写失败测试：两个客户端基于同一 revision 分别新增作答、错题、笔记、产物，后写入不能覆盖先写入。
- [ ] 写失败测试：旧设备上传已删除实体时，服务端 tombstone 不被旧实体复活。
- [ ] 运行 `pytest tests/test_server.py -q`，确认新增用例失败。
- [ ] 扩展 `user_state` 数据 JSON 为 `{revision, state, tombstones, updated_at}`；缺字段时按 revision=0、空 tombstone 读取。
- [ ] 增加按实体类型的纯函数合并：作答按 `id+ts` 并集、错题/产物按更新时间、对象型数据按实体 ID、每日统计取不回退值。
- [ ] 新 PUT 返回 `{ok, revision, state, merged, updated_at}`；对旧格式返回仍维持 200。
- [ ] 保留大小限制、账号隔离和同源写校验。
- [ ] 运行测试并确认通过。

## Task 5: 前端可靠同步与跨设备拉取

**Files:**
- Modify: `js/auth.js`
- Modify: `js/store.js`
- Test: `tests/account-sync.test.mjs`
- Test: `tests/test_server.py`

- [ ] 写失败测试：pull 后保存服务端 revision；sync 上传 base revision；冲突响应会落地服务端合并结果。
- [ ] 写失败测试：网络失败保留待同步状态，下一次 sync 会重试；401 不清除本机数据。
- [ ] 写失败测试：`visibilitychange`/`pagehide` 触发同步函数；周期检查发现 revision 变化会拉取。
- [ ] 运行 Node 与 pytest 相关用例，确认失败。
- [ ] 在 Auth 中增加 `revision`、`pending`、`syncNow({beacon})` 和冲突回写逻辑；普通请求仍防抖，离开页面优先使用 `navigator.sendBeacon` 或 keepalive fetch。
- [ ] 登录后完整 pull；前台周期只拉元信息或轻量 revision，发现变化后再拉完整 state。
- [ ] 每次 `Store._set` 只标记待同步，不因单次写入重复并发请求；同步成功清除已提交变更。
- [ ] 页面显示“已同步/等待同步/同步失败”，失败不假报成功。
- [ ] 运行测试并确认通过。

## Task 6: 书架分类与折叠交互

**Files:**
- Modify: `js/app.js`
- Modify: 相关 CSS 文件（定位实际书架样式后修改）
- Test: `tests/browser/*` 新增书架场景

- [ ] 写浏览器失败测试：书架显示“内置题库”和“生成内容”两个一级分组。
- [ ] 写浏览器失败测试：两组可独立收起/展开，刷新后状态保持；移动端 390px 不横向溢出。
- [ ] 写浏览器失败测试：生成内容显示变式题集合、速记卡和生成题库，能进入练习/复习/删除。
- [ ] 运行目标浏览器测试确认失败。
- [ ] 增加书架分组渲染和账号级折叠状态；内置题库只读，生成内容操作连接 Store artifact API。
- [ ] 加入空态、删除确认和删除失败提示；不新增无消费者 CSS 类。
- [ ] 运行浏览器测试并确认通过。

## Task 7: 错题详情端到端联调

**Files:**
- Modify: 实际错题详情和变式入口 JS
- Test: `tests/browser/*`

- [ ] 写失败浏览器测试：错题完成后出现推荐，不点击不请求生成；确认后显示 3 道目标和已有数量。
- [ ] 写失败浏览器测试：重复生成只展示有效新题，达到 5 道后入口禁用并说明原因。
- [ ] 写失败浏览器测试：生成速记卡后可在书架查看/复习/删除。
- [ ] 运行测试确认失败。
- [ ] 接通生成、落库、书架刷新、练习和删除流程；异常状态保留原错题并可重试。
- [ ] 运行浏览器测试并确认通过。

## Task 8: 全量回归与文档同步

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/features.md`（仅更新已实现内容）
- Test: 全部现有单元/Node/浏览器测试

- [ ] 运行 `pytest tests/test_server.py -q`、`node --test tests/account-sync.test.mjs tests/learning-loop.test.mjs`。
- [ ] 运行网页版现有 Node 测试集和浏览器测试集，重点检查 bundle、零 CDN、可访问性、390px 溢出和账号隔离。
- [ ] 运行 `python -m pytest -q`（网页版测试目录）并记录失败原因，不修改无关失败。
- [ ] 更新变更记录：同步修复、变式题去重/上限、速记卡和书架分类；不写未验证的完成状态。
- [ ] 复查同步白名单不含 BYOK/主题，产物删除使用 tombstone，旧快照兼容。
- [ ] 交付变更摘要和验证命令结果。
