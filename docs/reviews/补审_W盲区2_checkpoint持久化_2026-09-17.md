# 补审报告 · W 轮盲区 2：断点续跑 checkpoint 持久化面

- 审查日期：2026-09-17
- 审查对象：MedKit 仓库 `C:\Users\38063\Desktop\medkit`（HEAD=`28c1ab1191a51c3851061dd8c38c198a06eda350`，`__version__=0.10.4`）
- 审查方式：只读离线验证（静态走读 + git 取证 + 临时目录/mock 脚本 + 既有离线 pytest 子集）
- 边界声明：未调用任何真实 LLM/OCR/联网 API，未运行消费性管线；详见 §4 验证记录（含一次我自身的隔离失误披露）

---

## 1. 发现汇总表

| 编号 | 标题 | 路径(file:line) | 证据/复现 | 影响 | 严重性 | 修复建议 | 状态 |
|---|---|---|---|---|---|---|---|
| W2-01 | 损坏 checkpoint 静默回退从头，无告警无备份 | `medkit/core/orchestrator.py:392-400`（`_load_checkpoint`） | 场景 B：写入截断 JSON 后调用 `_load_checkpoint`，返回 `(set(), [])`，run.log 无记录、无 .corrupt 备份 | 一旦 checkpoint 损坏（如未来某环节改为非原子写），用户无感知地重出全部题目 = 全额 token 重付；无任何恢复线索 | S2中 | 参照 `config.py:133-141` 的 corrupt 备份模式：捕获到 JSON 异常时先把坏文件改名 `checkpoint.json.corrupt-<ts>.bak`，再写一行 run.log 提示 | 已验证 |
| W2-02 | checkpoint 只覆盖 MedGen 切片级；gate1/qc/review/render 无断点，崩溃后续跑重付下游 token | `orchestrator.py:1026-1027`（仅 `stage=="done"` 特判，其余全量重跑）；checkpoint 字段仅 `done_sids/questions/updated`（`:403-406`） | 场景 D：跑完整管线 → 把 meta.stage 拨回 `"qc"` 模拟崩溃 → 续跑：gen 调用 0（切片跳过✓），但 qc client 重新调用 1 次 | 进程死在 QC/复习手册/渲染阶段时，续跑跳过出题但**重跑 QC 与 MedReview**（均为付费 LLM 调用）；"断点续跑"实际只续到"出题完成" | S2中 | checkpoint 扩展下游完成位（如 `qc_done=True` + `qc_report` 路径指纹），或至少在 run.log/前端文案明示"仅出题阶段断点" | 已验证 |
| W2-03 | 空题切片被无条件记入 done_sids，续跑永久不补生成 | `orchestrator.py:973-978`（并发路径 `done_sids.add(sid_r)` 无条件执行）；串行路径 `:950-954` 同构 | 场景 E：mock 某切片一次返回 `[]` → checkpoint.done_sids 含该切片且其题目数为 0 → 二次续跑 gen 完全不再调用该切片 | 模型偶发空返回时，该切片永久缺失；最终题数少于配额，用户仅靠 run.log 一条 warning（`:1138`）难以察觉 | S2中 | 切片返回空列表时不加入 `done_sids`（或单独记 `failed_sids`，续跑时重试）；`gen_one` 出口加 `if not qs: raise PipelineError(...)` 让该切片走失败重试而非"完成" | 已验证 |
| W2-04 | 删除项目的 RUNNING 检查未持 RUN_LOCK，与启动管线存在 TOCTOU 窗口 | `medkit/routers/projects.py:244-248`；对照启动侧 `medkit/routers/pipeline.py:41-46` | 代码走读：delete 在 `RUNNING.get(pid)` 检查与 `rmtree` 之间未持锁；run 端点持锁后才置 `RUNNING[pid]` | 两个 HTTP 请求并发时，理论上 rmtree 可删掉正在写 checkpoint 的项目目录；Windows 下多因文件占用使 rmtree 报 500（用户可见错误），未造成静默损坏 | S2中 | delete 端点同样在 `RUN_LOCK` 内完成"检查 + （标记删除）"，与启动侧串行化 | 理论风险 |
| W2-05 | `_set_stage` 的 meta 读-改-写无锁，与其他 meta 写路径存在字段覆盖竞争 | `orchestrator.py:277-284`（read→改 stage→原子写）；其他写 meta 处如 `:1146/:1254/:1362`，审核台路由亦写 meta | 代码走读：`_PROG_LOCK`（`:126`）只串行化 progress.json，不保护 meta.json；跨线程 meta 写存在 lost-update | 极端并发下后写者覆盖先写者的非 stage 字段（如 usage/final_count），窗口小、影响为 meta 偶发回退 | S3低 | meta 写统一经 per-pid 锁（或复用 RUN_LOCK 外的第二把 meta 锁） | 理论风险 |
| W2-06 | 用户取消 vs 进程崩溃在恢复路径上无区分；崩溃后无"上次未正常结束"提示 | 取消路径：`orchestrator.py:470/:1132` 置 `stage=cancelled`；崩溃路径：无任何检测，meta.stage 停在中间态 | 走读：`run_project` 入口只特判 `stage=="done"`（`:1026`），不区分 cancelled/error/崩溃残留；status 接口直接回显旧 progress.json 时间戳（`projects.py:222-227`） | 崩溃后续跑可工作（靠 checkpoint），但前端看不出"上次被异常杀死"；陈旧 progress.json 的旧时间戳易误导 | S3低 | 管线启动时若 `stage ∉ {done,cancelled,error}` 且 progress.json 存在，run.log 补一行"检测到上次异常中断，从 checkpoint 续跑" | 已验证（走读+场景 D 佐证） |
| W2-07 | `substeps.jsonl` 非原子（append 后 read-all+trim 重写） | `orchestrator.py:161-165` | 走读：与 checkpoint/progress 走 `write_json_atomic` 不同，substeps 是裸 open-append + 全文 read_text + write_text；崩溃在 trim 写中途留下截断 jsonl | 读取方 `_read_substeps`（`projects.py:195` 附近逐行解析容错）跳过坏行，影响仅限子步骤面板可视化，不影响管线正确性 | S3低 | trim 改为"追加滚动 + 按行数惰性清理"，或写入也走临时名+rename | 已验证 |
| W2-08 | ~~交接文档引用已不存在的测试文件（文档漂移）~~ **【已勘误】** 审查提示词文档引用错误路径 `core/state.py`（真身 `medkit/state.py`） | 原文证据不成立：`docs/AGENT_HANDOFF.md` 全部 **31 个** `tests/*.py` 引用经逐条 `[ -f ]` 校验**全部存在**；`test_pipeline_writes_substeps_e2e` / `test_pipeline_progress_substeps` 是**测试函数名**（真身 `tests/test_pipeline_offline.py:643` / `:618`）而非文件。**改锚点**：`MedKit_专属审查提示词.md:86` 写 `core/state.py`，真身 `medkit/state.py:10-12`；`审查任务书/02_服务端与业务逻辑.md:16` 写的是正确路径 | 逐条文件存在性校验（31/31 命中）+ 提示词与任务书对照 | 文档误导后续审查/复现（路径层面） | S3低 | 更新 `MedKit_专属审查提示词.md:86` 的路径为 `medkit/state.py` | 已验证（**锚点已勘误**） |

**严重性计数：S0=0，S1=0，S2=4（W2-01/02/03/04），S3=4（W2-05/06/07/08）**

---

## 2. checkpoint 机制说明

### 2.1 写入时机（粒度 = 每个切片，非每阶段）
- MedGen 按章节切片出题（`PIPELINE_CONCURRENCY=3` 线程池，`orchestrator.py:36,956-981`）。**每完成 1 个切片立即落盘 checkpoint**：
  - 串行路径：`orchestrator.py:950-954`
  - 并发路径（`as_completed` 每 future）：`orchestrator.py:973-978`
- 中途退出兜底（取消/单切片异常后）：把已完成但未写的切片统一补落一次，`orchestrator.py:982-997`
- 出题阶段确认取消后再落一次：`orchestrator.py:1126-1127`
- **结论：两次写入之间进程被杀，最多丢失"正在生成中的那 1 个切片"的进度（该切片重生成），其余切片全部保留。** 写入为同步（调用方线程内），无异步落盘。

### 2.2 内容字段
`中间产物/checkpoint.json`（`orchestrator.py:403-406`）：
```json
{"done_sids": ["S001", ...], "questions": [<完整题目列表>], "updated": "<iso ts>"}
```
- `done_sids`：已完成切片 id 集合（恢复时据此跳过切片，`orchestrator.py:959`）
- `questions`：全部已生成题目的完整快照（含 sid，恢复时按 sid 回填，`:900-901,1007-1010`）
- **不包含**：当前阶段、当前批次、门禁结果、MedFix 状态、QC 分数——这些只存在于 `meta.json["stage"]` 与阶段产物文件（questions_gate1.json / 质检报告.json），不作为恢复依据。
- 配套持久化：`progress.json`（阶段+子步骤进度，原子写，`_set_progress:287-295`）、`meta.json`（stage/usage/final_count，原子写）、`substeps.jsonl`（子步骤事件流，非原子，见 W2-07）、`run.log`（追加）。

### 2.3 恢复流程
1. `POST /api/projects/{pid}/run`（`routers/pipeline.py:28-47`）：`stage=="done"` 拒跑（409）；否则置 `RUNNING[pid]` 并起后台线程。
2. `_run_project_impl`（`:1017`）：`stage=="done"` 直接返回（`:1026-1027`）；**其余情况一律从 websearch → generating → gate1 → qc → fixing → finalizing → reviewing → rendering 全量重跑**。
3. 恢复生效点仅在 MedGen 内部：`_load_checkpoint`（`:392-400`）→ 已完成切片跳过（`:959-960`）→ 未完成切片重生成；预分配 id 区间按 quota 顺序重算（`:906-911`），故恢复后题号稳定（与既有测试 `test_pipeline_offline.py::test_resume_from_checkpoint` 一致）。
4. 取消与崩溃的终态差异：用户取消 → `stage=cancelled`（`:470,1132`）；进程被杀 → stage 停在最后一次 `_set_stage` 的值（如 `qc`），无崩溃标记。

### 2.4 持久化原子性
- checkpoint/progress/meta/config 统一走 `fsutil.write_json_atomic`（`fsutil.py:38-53`）：唯一随机 tmp 名 + 写完 `Path.replace`（同卷原子改名）+ 失败重试 6 次 + finally 清理 tmp。
- **提交点 = rename**：rename 之前进程被杀，旧文件保持完好（场景 C1 验证）；并发写互不污染（C2：40 并发写后 JSON 合法、无 tmp 残留）。
- 非原子的旁路产物：`questions_raw.json`/`questions_gate1.json`/`质检报告.json`/`stage.json` 等为裸 write（如 `:1130,1170,1213`），但均为可再派生的产物；`stage.json` 当前无任何读取方（grep 全仓仅写处），截断无功能影响。

---

## 3. 离线验证记录

**验证脚本**（位于我的 agent 工作区，非仓库内）：`...\agent_mode\workspace\.sessions\38441953455482882\agents\s_00018zVOyOS\w2_offline_verify.py`
**运行方式**：`python -m pytest w2_offline_verify.py -v`（Python 3.14.7 / pytest 9.1.1）
**临时目录**：pytest `tmp_path` 自动隔离，运行后由 pytest 自动清理（如 `...\Temp\pytest-of-38063\pytest-844\test_A2_...\`）；`projects_dir` 经 monkeypatch 指向该临时目录。
**Mock**：复用仓库 `tests/test_pipeline_offline.py` 的 `FakeLLM`（gen/qc/fix/review 四角色 canned 输出，零网络）+ `build_project` 构造 2 切片样例项目。

| 场景 | 构造 → 执行 | 结果 | 对应发现 |
|---|---|---|---|
| A1 全量断点续跑 | checkpoint 预置 done_sids={S001,S002} → run_project 全包 | gen 调用 = **0**（called=[]），管线直达 done | 机制符合预期 |
| A2 部分断点续跑（≈阶段完成后杀进程） | checkpoint 预置 done_sids={S001} → run_project | gen 仅调用 S002（called=['S002']），S001 题来自断点；完成后 done_sids={S001,S002} | 机制符合预期；验证"丢 ≤1 切片" |
| B 损坏 checkpoint | 写截断 JSON 到 checkpoint.json → `_load_checkpoint` | 返回 `(set(), [])`；原坏文件保留、无 .corrupt 备份、run.log 无记录 | W2-01 |
| C1 rename 崩溃模拟 | patch `Path.replace` 抛错 → write_json_atomic | 旧 checkpoint 内容逐字节不变；tmp 无残留 | 原子性达标（正向） |
| C2 并发写 | 8 线程 × 40 次写同一文件 | 最终文件为合法 JSON（i=36），无 tmp 残留 | 原子性达标（正向） |
| D 崩溃在 QC 阶段 | 跑完整 done → meta.stage 拨回 "qc" → 用同一 qc spy 续跑 | run1: gen=2, qc=1；续跑: gen=**0**（切片跳过）, qc=**1**（QC 重跑） | W2-02 |
| E 空题切片 | mock S002 一次返回 `([], None)` → 跑 done → 再续跑 | checkpoint.done_sids 含 S002 但其题目数=0；续跑 gen 不再调用 S002 | W2-03 |

**既有回归**：`tests/test_pipeline_offline.py::test_resume_from_checkpoint` + `test_orchestrator_progress.py` + `test_pipeline_events.py` 共 14 例全部通过（在仓库自带 conftest 隔离下运行）。

### 3.1 审查过程自我披露（合规事项）
> **2026-09-17 勘误（独立核验会话实测）**：本节原述"新增 1 个 .bak""未删除/修改任何用户文件"**不准确**，已按文件系统实测更正；
> 完整勘误说明见 `审查报告_2026-09-17_W轮补审.md` 附录 A 与附录 C.5。

我最初两版验证脚本未加载仓库 `tests/conftest.py` 的 autouse 隔离 fixture（脚本置于 tests/ 之外），导致 `medkit/core/db.py:30-31` 在 import 时固化的 `DB_PATH` 仍指向真实 `~/.medkit/library/medkit.db`：
- 后果：`db.migrate()` 对真实库执行了一次 **schema 升级**，产生 **2 个** `.bak` 回滚文件（同一次 `_backup_before_migrate` 调用备份 `LIBRARY_DIR/*.json` + `medkit.db`，当时活 JSON 仅 `slice_index.json`）：
  - `~/.medkit/library/medkit.db.pre-db-20260917-120655.bak` = **1,236,992 B ≈ 1.18 MiB**（升级前整库副本）；
  - `~/.medkit/library/slice_index.json.pre-db-20260917-120655.bak` = **7,015 B**。
  - 真实库 `medkit.db` 由 1,236,992 B → **1,265,664 B**（**+28,672 B**）。⚠️ +28KB 是**库文件的体积增量**，**不是** `.bak` 体积。
- 处置：**未删除任何用户数据**，升级前状态有 `.bak` 可完整回滚。**但"未修改任何用户文件"不成立**——`medkit.db` 已被 `migrate()` 改写（字节数已变）。正确表述为："未删除数据；`medkit.db` 经一次 `migrate()` 升级，原库有 `.bak` 可回滚"。第三版脚本补齐全套 R5-01 隔离（DB_PATH + 7 个 JSON 文件常量重定向到 tmp）后复跑，并用「家目录全量文件 SHA1 快照 before/after」哨兵验证**零差异**（对比输出为空）。
- 附带观察（产品侧）：`db.py:30` 在模块 import 时固化 `LIBRARY_DIR/DB_PATH`，任何绕过 conftest 的脚本/调试会话都会按真实库运行——仓库已有 R5-01 哨兵，但哨兵只在 tests/ 套件内生效。建议 db 路径改为惰性解析。

---

## 4. 盲区结论

**该盲区被现有防线覆盖的部分**：
1. **写入粒度与丢失面**：达标——每切片落 checkpoint，崩溃最多丢 1 个切片；取消≠丢弃且取消路径单独落盘（U1/U2 设计成立，A1/A2 验证）。
2. **写入原子性**：达标——checkpoint/progress/meta 均走唯一 tmp + 同卷 rename + 重试，提交点清晰（C1/C2 验证）。
3. **运行互斥**：进程内 per-pid 运行锁 + 删除前 RUNNING 检查，主线竞态被挡住（除 W2-04 的 TOCTOU 窗口）。

**缺口（本盲区真正的暴露面）**：
1. **恢复语义窄于承诺**：checkpoint 只管 MedGen 切片，gate1/QC/MedReview 这些付费阶段在崩溃后续跑会全额重付（W2-02，已验证）——这是"断点续跑"宣传语与实际行为的最大落差。
2. **损坏即静默全量重跑**：损坏 checkpoint 无备份无告警，用户在不知情下重付全量 token（W2-01，已验证）。
3. **完成判定不验产出**：空题切片也算完成，永久缺口（W2-03，已验证）。
4. 两个 S2 级竞态（W2-04/05）与崩溃态不可见（W2-06）属加固不足。

**综合严重程度**：**S2 中**。本盲区不存在 S0/S1 级问题——无素材/Key 外泄、无关键数据不可逆丢失（原子写保住了 checkpoint 本体，损坏路径只是浪费钱而不是丢数据）。核心建议集中在三点：损坏 checkpoint 留备份并告警、下游阶段断点（或至少明示边界）、空题切片不计完成。
