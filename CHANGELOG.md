# 更新日志

本项目所有值得记录的变更都记录在该文件中。格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)。

> **规范（NX-06）**：凡 `medkit/prompts/*.md` 有改动，当版必须新增「`### Prompts`」小节
> （列改动与影响），并同步 `tests/fixtures/llm_cases/` 对应样本——prompt 与契约、fixtures 三者一致才可合入。

## [0.10.4] - 2026-09-17

> **本版为何要 bump**：2026-09-17 全面代码审查（build-web-apps 插件三路深查：后端质量 /
> 前端质量 / 安全专项 + 浏览器运行时 QA）发现 1 个可致 **API Key 泄露**的前端注入漏洞与
> 2 个并发正确性/费用缺陷，本版一次性根治（批次 ID `RV1`~`RV7`，`RV` = ReView 2026-09-17）。
> 审查基线：ruff/eslint 全绿、573 测试全过、覆盖率 83%、浏览器 QA 7/7（零 console 报错）——
> 以下缺陷均为审查新发现，非既有回归。

### 安全（Security）

#### Fixed

- **RV1（严重）行内 `onclick`/`ontoggle` 的 JS 注入 → 存储型 XSS → 密钥泄露链**：
  `esc()` 只做 HTML 转义，属性值内的 `&#39;` 会被浏览器**先解码再交 JS 解析**，单引号复活
  即可闭合字符串注入（`x');alert(1)//`）。可控源覆盖错题/真题导入的 subject、知识点名与
  LLM 输出（连 "Hodgkin's" 类正常医学术语也会击穿）；完整攻击链：导入他人分享的错题
  JSON → 打开学习中心触发 XSS → `PUT /api/config` 把 base_url 指向攻击者 → 下次生成时
  解密后的 Key 以 Bearer 外发。**修复**：全仓 35 处「动态值进内联 JS」统一改为项目既有的
  `data-* + this` 范式（R3-02 立过），函数双签名兼容程序化调用；渲染端（`qbank_html.py`）
  核查为静态字面量参数，无注入面。**附带修正**：刷题快捷键 1/2/3 此前对 `#mem_area`
  记忆卡误调 `rvGrade3`（SM-2 复习端点）必报错，现按卡片所属容器分流到 `memGrade3`（FSRS）。

### 后端正确性与费用（Fixed）

- **RV2（严重）`_SUBSTEP_INFLIGHT` 跨项目污染**：在飞子步骤登记原为模块级平铺 dict
  （键 `stage:step` 不含 pid），而运行守卫按 pid 隔离、**不同项目允许并发**——A 项目
  terminate 时会把 B 项目在飞的子步骤一并清除并误写进 A 的事件流（B 侧面板永久「运行中」）。
  现按 pid（= 项目目录名）分桶，terminate 只清本项目；桶空即回收不累积。
- **RV3（严重）子步骤超时后僵尸线程持续烧 token**：`_run_substep` 超时后旧 daemon 线程
  无法 kill、继续执行 LLM 调用，重试叠加最多 3 个线程并发烧同一份 token。现 `fn` 签名改为
  `fn(ev)`——超时即置位本次尝试专属取消事件，fn 内以 `_or_cancel(项目取消, ev)` 组合源
  按尝试构造的 LLMClient 流式读取检测后**提前退出**；项目级 cancel 置位后不再发起新尝试。
  fix/qc 客户端改为「按尝试工厂」：注入（测试 Fake）原样直用保住离线测试通道。
- **RV7 `row_to_dict` 损坏行语义漂移**：data JSON 损坏时原返回只含 id 的空壳 dict，被
  业务层当作「存在但字段为空」的记录（空题干卡/空科目）。现返回 `None`——`list_rows`
  过滤并按表汇总留痕（提示从备份恢复），`find_row` 视同未找到。

### 质检门禁（Fixed）

- **S1-1（R8+W 审查 P0）质检故障不再静默降级为「通过」**：`agents/medqc.py` 单批质检 LLM 异常
  原返回 `PASS_WITH_FIXES` + warn 级 `QC_ERR` 并计 50 分，而聚合层只看 issue 的 `severity`
  （批次自身的 `decision` 收了却从未参与判定，属死代码）——**一次 LLM 故障即让整批 20 题
  跳过事实校验**。现与既有 `QC_CONTRACT` 契约失败**同语义**：`QC_UNVERIFIED` / `severity=fail` /
  `score=-1`（不计分）/ `decision=BLOCKED`；聚合层显式承认批次级 `BLOCKED`
  （`has_fail or "BLOCKED" in decisions`，两道守卫纵深防御）。
  `core/orchestrator.py` 的「整个质检子步骤失败/超时」路径同样由 `PASS_WITH_FIXES` 改为
  `BLOCKED` 并注入同码 issue，`QC_UNVERIFIED` 与 `QC_CONTRACT` 一并写入「人工复核清单.md」
  （新增 `_append_unverified_review`，自然语言说明「这几批没经过事实校验，请人工过一遍」）。
  **零额外成本**：该 issue 的 `q_id` 未命中题库，`medfix.fix_questions` 先 `fail_ids &= by_id`
  后直接返回，不触发任何 LLM 调用。产物仍照常生成（`BLOCKED` 走 MedFix + 门禁① 复核分支，
  不中断管线），只是决策层不再谎报「已通过质检」。

### 数据可靠性（Fixed）

- **S2-15 / S2-26（R8+W P1）迁移前 DB 备份改为一致性快照 + 可读校验**：原实现仅 `copy2` 主库，
  WAL 模式下最新事务可能仍在 `-wal` 里 → 备份得到「缺表/缺行」的库（W1 E2E §C 实证：
  `wal_autocheckpoint=0` 时只拷主库 → `no such table: t`），**ADR-005「可一键回退」在最需要它的时候打折**。
  现改用 **SQLite online backup API**（`core/db.py::_backup_db_snapshot`）把 WAL 一并落到单一文件，
  并用 `PRAGMA integrity_check` + 表数校验**确认可读**后才算成功；非 SQLite 文件不再产出「看似成功」的备份。
- **S2-27（R8+W P1）备份失败不再静默继续迁移**：原 `_backup_before_migrate` 忽略 `_backup_one` 的
  `None` 返回值、无条件进迁移——等于「连备份都没做成仍执行不可逆结构变更」。现收集失败项并抛
  `db.BackupError` **中止迁移**（启动路径已有兜底：留痕后退回 JSON 轨，数据始终可读，下次启动重试）。
- **S2-13（R8+W P1）「清空全部数据」不再谎报成功**：原 `_clear_all_data` 逐 child 吞掉 `OSError`
  后恒回 `ok=true`——运行中 SQLite 被占用（Windows `WinError 32`）时主库根本删不掉，用户却在
  「已清空」的错觉下重启看到数据「复活」。现 `_clear_all_data` 返回 `(removed, failed)`，接口在
  有失败项时回 **`ok=false` + `failed` 列表 + 可执行指引**（「请完全退出 MedKit 后再清空一次」）；
  前端 `dmClear` 相应显示 `⚠️ 部分数据未能删除`。同时清空范围补 `sessions/`、`logs/`、`exports/`
  （**保留 `exports/backups`**——那是清空前刚生成的自动备份，一并删掉等于亲手毁掉安全网）。
- **S2-14（R8+W）一键备份补 `sessions/`**：原 `_INCLUDE_PATHS` 漏掉素材会话，换机恢复后会话全丢。
- **S3-18（R8+W）核心备份带回滚点**：核心备份原排除全部 `.bak`，连 `.pre-db-*.bak`（迁移/导入回滚点）
  一起丢掉；现只跳过 `-wal/-shm` 与 `.corrupt-*`（无恢复价值），保留真正的回滚点。

### 供应链（Security / Fixed）

- **S2-24（R8+W P1）starlette 升到 1.6.0**（原 1.2.1，低于 CVE-2026-54283 修复线 1.3.1）：
  fastapi 0.136.3 的约束是 `starlette>=0.46.0`（**无上限**），故直接升版即可，无需联动 fastapi。
  升版后全量 **573 passed / 0 failed**、浏览器层 **37 passed**。新增守卫
  `test_starlette_meets_cve_fix_line`（防止被误降级回去）。
- **S2-17（R8+W）`requirements.lock` 加全平台 `--hash=sha256:` 钉版**：原 lock 只钉版本不钉哈希，
  同一 `==x.y.z` 若被上游重新上传（或投毒）仍会被静默接受。新增可复跑生成器
  `pack/gen-lock-hashes.py`（查 PyPI JSON 取该版本**全部发布文件**的 sha256——必须列全平台，
  否则在别的平台 `--require-hashes` 会因「该平台产物无已知哈希」而失败）。实测
  `pip install --require-hashes --dry-run -r requirements.lock` 通过。
- **S2-16（R8+W）CI 漏洞审计对象改为产物实际闭包**：原 `pip-audit -r requirements.txt` 审的是
  **浮动声明**，pip 会解析到「最新」版本，与真正打包进去的锁定版本不同——等于「审计图 ≠ 产物图」
  （starlette CVE 正是这样漏网的）。现审计 `requirements.lock`，浮动侧仅作前瞻提示（`::warning::`，不判闸）。
- **S2-20（R8+W）workflow 的 actions 全部 pin 到完整 commit SHA**（`actions/checkout`、
  `actions/setup-python`、`actions/cache` 共 7 处），不再用可变的 `@v4`/`@v5` 标签；
  `dependabot.yml` 补 `github-actions` 与 `npm` 两个生态（原只覆盖 pip）。
- **S2-19（R8+W）新增真实打包 job**：原 `check-package.py` 在 CI 上因「未构建 dist」直接
  `[跳过] … return 0`，等于空操作，真实的纯净断言只存在于维护者本机。现 CI 增加 `package` job
  （windows-latest，装 lock → PyInstaller 构建 → `check-package.py --strict`），并给脚本加了
  `--strict`（无产物即失败）。
- **S2-18（R8+W）产物闭包断言**：`pack/check-package.py` 新增「`dist/_internal` 里的发行包必须是
  `requirements.lock` 闭包子集」检查——把「产物实际闭包 ≠ lock/notices」从人工比对变成自动判定。
  实测当前产物含 **4 个未声明发行包**（`attrs` / `email-validator` / `importlib-metadata` /
  `itsdangerous`，构建机环境污染），另有 33 个已声明依赖未随产物保留 dist-info（→ 需重出包修复，
  已在 `THIRD_PARTY_NOTICES.md` 如实记录）。
- **S2-22（R8+W）`THIRD_PARTY_NOTICES.md` 闭包补漏**：34 项 → **38 项**（漏掉的正是
  `uvicorn[standard]` 的 4 个 extras：`httptools` / `python-dotenv` / `watchfiles` / `websockets`），
  并同步 `starlette` 版本。⚠️ **同时更正一处事实错误**：报告原文称产物含「LGPL chardet」，
  实测 `chardet 7.4.3` 的元数据为 **`License-Expression: 0BSD`**（宽松），非 LGPL-2.1
  （后者适用于 chardet ≤5.x）；且产物内的 `chardet/` 是**残件**（缺顶层 `__init__.py`、无 dist-info）。
- **S2-23（R8+W）新增锁文件漂移守卫**（`tests/test_lock_closure.py`）：从 `requirements.txt`
  按已装元数据推导完整运行时闭包（含 extras/marker），断言与 `requirements.lock` **完全一致**
  （实测 38 == 38）；另断言 notices 闭包条目数 == lock 条目数。CI 增加独立步骤跑该文件。

### 医学事实门禁（Fixed）

- **S1-2（R8+W P0）新增不依赖 LLM 的数值锚点**（`gates/numeric_check.py`）：MedQC 的 F1 事实性
  规则原由 LLM 自评（同一模型既出题又判自己），剂量/阈值写错时可能被判「看似合理」而放行。
  现补一条**程序化硬核对**——题干与**正确选项**里的临床数值（mg/kg/ml/mmHg/mmol/℃/%/次每分…）
  必须能在该题的源切片里找到出处，找不到即 `fail`，由 MedFix 带着源切片去改，改不动才剔除；
  题干里的数值（病例可自行构造）只 `warn`。
  ⚠️ **设计要点：绝不核对干扰项**——选择题的干扰项**本来就是**错误数值（把 110 改成 150 才叫
  干扰项），把 `options` 整体纳入核对会让几乎所有数值型选择题被判 fail。首版实现正是如此，
  手工试跑立刻暴露，已固化为对照用例 `test_distractor_numbers_are_not_flagged`。
- **S1-2（部分）质检模型同档告警**：生成与质检使用同一模型档时，run.log 提示
  「同模型自查自纠，事实性判分的独立性打折」并建议为质检单独选档。**未做硬拦**——
  默认配置下 `model_qc` 会回落到 `model_gen`，硬拦会让存量用户直接无法生成；
  是否强制分档属产品决策（见 `docs/reviews/修复方案_R8+W_2026-09-17.md` B4）。
- **S1-3（R8+W P0）查重补「题↔源文本」通道**（`gates/dedup_check.py`）：原 `check_dup` 只比
  题与题之间，于是「把教材原文整段搬成题干」这类题既不重复也不违反选项规则，能一路通过所有
  门禁进入产物。现按 `sid` 取回源切片文本，用 n-gram **包含率**（而非 Jaccard——题短源长时
  Jaccard 必然很小）衡量题干有多少内容直接来自源文本，≥90% 判 `SRC_COPY` warn 进人工复核 /
  MedFix 改写。只 warn 不 fail：正常命题本就会复用教材术语，照抄是「可疑」而非「错误」。
- 两条通道均已接入**门禁①**（第 5 个子步骤「数值核验」，`sub_total` 4→5）与质检 BLOCKED 后的
  快速复核，其 fail/warn 与查重同路（MedFix 定向修复 + 人工复核留痕）。

### 断点续跑（Fixed）

- **S2-29（R8+W P1）损坏的断点文件不再静默全量重跑**：原 `_load_checkpoint` 的
  `except Exception: return set(), []` 会让「继续」按钮变成全量重出题——用户在**不知情**下
  把已付费的 token 再付一遍，且 run.log 里查不到任何痕迹。现按 `config.py` 既有范式先改名
  `checkpoint.json.corrupt-<ts>.bak` 留证 + 记 errors + run.log 明确告知，**再**从头跑；
  同时补类型校验（顶层非对象、`done_sids`/`questions` 非列表一律按损坏处理）。
- **S2-31（R8+W P1）空题切片不再被记入完成位**：原实现无条件 `done_sids.add(sid_r)`，
  某切片一次空返回即被永久记为「已完成」，续跑不再生成它 → 最终题数少于配额且用户难以察觉。
  现在串行路径、并发路径、以及「取消/中断回填」循环**三处**都不再记录空切片，并留 run.log 告警，
  续跑会重试该切片。
- **S2-30（R8+W P1）断点范围不再隐性**：发现断点时 run.log 补一行明示
  「**断点仅覆盖「出题」阶段：门禁①/质检/MedFix/渲染 无断点**」——若上次中断发生在这些阶段，
  续跑会重新执行它们（可能重复消耗 token）。原实现默默如此，用户无从得知。
- **S2-32（R8+W P1）删除项目的 TOCTOU 窗口关闭**：`routers/projects.py` 的 RUNNING 检查、
  `rmtree`、删后复核整体移入 `with RUN_LOCK:`，与启动端点（持 RUN_LOCK 后才置 RUNNING）串行化；
  原实现为「先检查、后删除」，中间存在「检查通过后管线刚启动、目录被删」的窗口。

### 健壮性（Changed）

- **RV4 同步 LLM 路由占满线程池**：`POST /api/trial` 与 `POST /api/projects/{pid}/regen`
  的 30~90 秒 LLM 阻塞原以同步 `def` 占住 FastAPI 线程池 worker（默认 40），并发请求会把
  其他接口一并拖入排队。现改 `async def` + `asyncio.to_thread`（regen 的 `_pid_lock`
  获取与 LLM 调用一并移入 worker，锁等待不落回事件循环）。
- **RV6 `batch_mistakes` 无长度上限**：与 batch-delete 的 `_valid_ids` 同口径限 500 条
  （超大列表长时间占 worker 并批量写库）。
- **RV5 押题卷答题卡键盘跳题错位**：`gridKeys` 原用 `parseInt(格子文本)-1` 反推下标，
  错题重练时格子显示原卷号（B-09 等），Enter 会跳错题甚至 NaN。现格子带 `data-i` 下标、
  键盘直接读下标。

### 测试（Added）

- 新增 7 个回归测试：RV1 源码级扫描守卫——`web/js/` 不得再出现「动态值进内联事件 JS」
  模式（`test_u_batch3_render_safety.py`）、RV2 并发项目隔离 / RV3 超时置位事件 +
  cancel 熔断（`test_pipeline_events.py`）、RV5 data-i（`test_s1_render.py`）、
  RV6 501 条拒收（`test_mistake_batch.py`）、RV7 损坏行过滤（`test_db.py`）。
  `_run_substep` 既有 4 用例随 `fn(ev)` 签名同步更新。全量 **580 passed**、覆盖率 83%（门槛 80%）。
- **S1-1（R8+W）另新增 5 例**（`tests/test_s1_medqc_unverified.py`）：异常批 → BLOCKED、
  异常批不计分、部分批失败仍整体 BLOCKED、聚合层承认批次级 BLOCKED、`QC_UNVERIFIED`
  不触发 MedFix 的 LLM 调用。两道守卫**各自**做过反向验证（注入即红）。
- **B2（R8+W）新增 8 例**（`tests/test_r8w_backup_chain.py`）：WAL 未合并时 copy2 主库读不回
  而快照读得回、**接线级**「迁移产出的一致性快照含 WAL 数据」、非 SQLite 文件被拒、备份失败中止
  迁移（并断言版本仍为 0 且未建表）、对照组迁移正常、清空遇占用如实回 `failed`、端到端回
  `ok=false`、核心备份含 `sessions/` 与回滚点且跳过 `.corrupt-*`/`-wal`。
  四处守卫**各自**做过反向验证（注入即红；其中接线级用例是补测——只测 helper 会漏掉
  「调用点被换回 copy2」这类回归）。
- **B3（R8+W）新增 7 例**（`tests/test_r8w_checkpoint_chain.py`）：损坏 checkpoint 留
  `.corrupt-<ts>.bak` 且 run.log 有告警、正常 checkpoint 照常读出、空题切片在串行/并发两路
  都不进 `done_sids`、续跑明示断点范围、异常中断检测留痕、删除项目在 RUN_LOCK 内完成
  （直接断言锁的持有状态，不用时序推断）。六处守卫**各自**做过反向验证（注入即红）。
- **B4（R8+W）新增 10 例**（`tests/test_r8w_numeric_anchor.py`）：错误剂量（正确选项）在**无任何
  LLM 介入**下被判 fail、**干扰项不误报**（对照用例）、病例题干数值仅 warn、X 型多答案逐项核对、
  无源文本跳过、逐字照抄源文本判 `SRC_COPY`、正常命题不误报、不传源文本时向后兼容、
  以及**接线级**用例（门禁① 真的调用了两条通道且会剔除未达标题）。
  四处注入**各自**做过反向验证（注入即红；其中「核对干扰项」的注入是防回归对照）。
- **B5（R8+W）新增 17 例**（`tests/test_lock_closure.py` 5 例 + `tests/test_check_package.py` 新增
  4 例 + 既有 8 例）：锁文件与运行时闭包强等、starlette ≥1.3.1、notices 条目数与 lock 一致、
  4 个 `uvicorn[standard]` extras 双处齐备、`--strict` 无产物即失败、未声明发行包可被识别。

## [0.10.3] - 2026-09-16

> **本版为何要 bump（又一次「同号不同物」）**：`dist-installer/` 里的 0.10.2 两件套构建于
> **09-15 20:37 / 20:38**（提交 `d0e6d10` 那一刻源码对齐），但**同一版本号下当天 22:51~23:49
> 又落了四笔实质提交**，产物再未重出：
>
> | 提交 | 时间 | 用户手上的 0.10.2 缺什么 |
> |---|---|---|
> | `beecdcf` | 22:51 | 前端 JS 缺陷修复——`learn.js` 调用全仓不存在的 `rexAnalyzeRender()`，运行必抛 TypeError（仅在「真题草稿 >200 条」分支触发） |
> | `1e8cb2a` | 23:18 | 数据管理区（`routers/data.py`）· 脏数据排除（U-22）· **日志脱敏 `RedactingFilter`**（U-24，安全）· **AGPL-3.0 `LICENSE` 随包分发**（合规） |
> | `b360f90` | 23:40 | `routers/library.py` 讲解流去重竞态修复（服务端正确性） |
> | `ced0f6d` | 23:49 | 打包纯净检查的 Windows 中文输出崩溃（工具链） |
>
> 取证：`dist/MedKit/_internal/medkit/web/js/learn.js` 中 `rexAnalyzeRender` 仍存在（=修复未进包）、
> `_internal/` 下**无 `LICENSE`**、`app.js` 无 `api/data/summary`。
> 旧 0.10.2 产物已改名隔离为 `*-pre-u17`（首个缺失修复的批次）；本版重出包，确保版本可区分。

### 性能（本轮实测驱动的三项治理）

#### Changed

- **V-02 概览/掌握度的读放大**：`/api/library/dashboard` 此前为「取一个数字」把整张 mistakes 表
  `SELECT *` + 逐行 `json.loads`（`total_mistakes`），并把 knowledge 表**整表读两遍**
  （`get_mastery_view` 一遍、`recent_activity` 又一遍）。现新增 `library.count_mistakes()`
  走 `COUNT(*)`（命中 U-14 补的 `mistakes(subject,…)` 索引），`recent_activity(kps=…)`
  复用调用方已加载的列表。1500 错题 / 800 知识点 / 600 复习卡合成库实测：
  `dashboard` 144.5 → **83.8ms**（−42%），`mastery` 50.1 → **29.7ms**，`recommend` 40.9 → **23.4ms**。
- **V-03 单行写放大**：`_store()` 退出时对脏表走 `replace_all`（`DELETE` 全表 + 逐行重插），
  成本 O(表内总行数)——3000 行库单次写实测 ~103ms（≈34µs/行，是定向 UPDATE 的千倍量级），
  而 `record_quiz` / `record_review` / `log_knowledge_event` / `add_mistake` / `update_mistake` /
  `mark_learned` 实际都只改一行。现引入：
  - `db.upsert_rows()`：按 id 集合增量 UPSERT，未列入的行原样保留；
  - `library._StoreView`：双表**惰性读**（只碰 knowledge 的路径不再顺带全表解 mistakes）；
  - `library._mark_kp_row()/_mark_m_row()`：调用方显式登记「只改这一行」。
  **缺省登记集为空 → 一律退回全表替换**，任何未显式登记的结构性改动（批量删/整组替换）
  行为与改动前完全一致（宁慢不丢）。实测 `record_quiz` 67.6 → **15.7ms**（−77%）。
  等价性由 `tests/test_v03_rowwise_write.py` 保证：增量写与全表替换的最终表内容逐行比对一致、
  未被触碰的行逐字节不变、无 `id` 的历史脏行必须退回全表替换。

- **V-09 单行路径「定向读 + 单事务」**：V-03 之后仍有两处读放大残留——① `record_quiz` /
  `record_review` / `log_knowledge_event` 仍要把**整张 knowledge 表**读进内存再线性查找
  （1500+800 行库实测 ~12ms/次）；② `add_mistake` 尾部对每个知识点名**各开一个事务**调
  `log_knowledge_event`（错题通常派生 2~3 个知识点 = 2~3 个额外事务）。而 `batch_add` /
  `sync_from_paper`（押题卷回流）/ `import_site_items`（站点导入）都是**逐条**走 `add_mistake`——
  100 条回流实测 **3.7s**，且随库变大线性劣化。现：
  - 新增 `db.find_row()` 与 `library._find_kp()` / `_find_mistake()`：按 name/id **定向取单行**
    （保留「表内确有 `name` 为空的历史行时才整表扫描」的兜底，避免凭空造出重复知识点）；
  - 新增 `library.log_knowledge_events()`：**一次事务**处理多个知识点（`log_knowledge_event` 变其单元素封装）；
  - `add_mistake` 不再整表读 mistakes（重复检测由主键 `INSERT OR REPLACE` 承担，序号改走 `COUNT(*)`）；
  - 回写判定改为「**表是否被整表加载过**」：未加载 = 不可能改到其它行 → 只 UPSERT 登记行；
    加载过 → 全表替换；并把登记行**并回列表**，避免「先登记、后整表加载」时被旧快照覆盖；
  - `_mark_kp_row` / `_mark_m_row` **登记即置脏**，消除「登记了却忘标脏 → 改动静默丢失」。
  - 实测：`add_mistake` 单条 **99.8 → 0.2ms** · `record_quiz` 15.7 → 5.1ms（最快 0.4ms）·
    **`batch_add` 100 条 3.7s → <0.05s**。
  - 守卫/等价性用例扩到 **10 例**（`tests/test_v03_rowwise_write.py`），含「同 id 覆盖不新增」
    「派生知识点必须落库」「先登记、后整表加载不得被旧快照覆盖」；**反向验证 3/3 有效**。

#### Changed

- **V-11 启动即建库 + JSON→SQLite 幂等补导（ADR-006「迁移路径 §3」）**：ADR-006 的
  **退役条件 1** 明确要求「`db.import_from_json()` 已在**启动路径**幂等执行」，而它此前从未接线；
  后果是库域（错题本/学习中心）自己从不建库，db 只由大纲/真题/记忆卡按需建立 ——
  **只用错题本的安装永远停在 JSON 轨**，每次单行写入都是整份 JSON 原子重写（含 fsync）。
  现把 `db.migrate()` + `db.import_from_json()` 放进 lifespan 启动路径（幂等、失败不阻断启动、
  失败即退回 JSON 轨），并把切换时机定死（不再取决于用户碰过哪个功能）。
  实测（真实 uvicorn HTTP 路径，批量入库 50 条）：**8190ms → 39ms（0.8ms/条，约 210×）**。
  这也让 V-02/V-03/V-09 的存储层优化真正落到所有用户身上（此前只对已建库的安装生效）。

#### Fixed

- **V-10（P0）错题本/掌握度会「界面全空」——双轨切换时 JSON 活数据没被补导**：库域
  （`mistakes`/`knowledge`）**从不调用 `dbs.migrate()`**，而 `medkit.db` 是由**别的域**
  （大纲管理 / 真题考频 / 记忆卡）按需建立的；库域的轨判定只看「db 是否存在」。于是这条
  **必现**路径：

  ```
  新装 → 用错题本（JSON 轨）→ 去点一次「大纲管理」（建库）
       → 库域改判 SQL 轨 → 错题本/掌握度读空库 = 界面全空
  ```

  实测：JSON 轨写 3 条错题 → `migrate()` → `list_mistakes()` 由 3 变 **0**，而磁盘上
  `mistakes.json` 仍有 3 条；此后新写入进 DB，形成真正的双轨分叉（旧数据永远读不到）。
  `db.import_from_json()`（按 id 幂等 + 导入后原 JSON 改名留档）本就是为这一步准备的
  ——其 docstring 明写「避免『JSON 活数据永远进不了 DB』的丢失风险」——但**从未被接线**。
  现把库域全部轨判定收敛到 `_sql_ready()`：**首次**判定要走 SQL 轨时先补导一次；
  补导**失败则退回 JSON 轨**（宁可暂时双轨，也不让用户数据隐身）。
  新增 `tests/test_v10_track_backfill.py`（6 例，含「补导失败必须退回 JSON 轨」与结构守卫），
  反向验证有效。
- **V-04 静默异常守卫从「预算式」改为「零容忍」**：`test_u15_silent_pass_converged` 原断言
  `n <= 5`，等于允许 5 处静默 `except Exception: pass` 回归而不被发现（反向验证实测：注入 1 处仍绿、
  注入 6 处才红）。现收敛为 `n == 0` 并输出具体 file:line。
- **V-05 `test_cleanup_stale_sessions` 时间敏感 flake**：用例按**列表下标**取「要变老的两场」
  （`sessions[1], sessions[2]`），而 `list_sessions()` 按 `updated_at` 降序排——三次
  `start_session` 跨了秒边界时下标含义就变了，会把**最新**的那场改成 60 天前删掉，断言随机翻红
  （全量跑实测复现）。现改为按 `kp_name` 精确定位，并**用递增时间戳确定性地复现跨秒条件**，
  把「靠运气才绿」变成稳定回归网。

#### Tests

- **V-12 浏览器层用例隔离（V-10 修复后暴露的测试缺陷）**：V-10 修好数据可见性后，浏览器层
  由 35 passed 变 **4 failed / 31 passed**。对照实验定位（关掉 V-11 仍红；回退到 V-09 之前的
  `library.py`/`main.py`/`db.py` 则 35 passed；单跑 `test_study_quiz.py` 通过 → 跨用例污染）：
  浏览器用例共享**一个后端进程 + 一个数据目录**，而 `test_gap_flow.py` **只播种不清理**
  （留下「儿科学 / 呼吸 / 测试错题？」），`test_mistakes_batch*` 断言「第一组就是我的科目」、
  `test_study_quiz*` 断言「只有 1 张到期卡」——**这些断言此前是靠 V-10 修掉的缺陷才绿的**
  （切轨不补导让遗留数据恰好读不到）。修复：`test_gap_flow` 补清理；`test_mistakes_batch*`
  改为按本用例科目定位分组；`test_study_quiz*` 改用专属科目 + 专属知识点名
  （kp 以名字为键，沿用通用名会命中早先用例的 kp）。浏览器层恢复 **35 passed** 且连跑 3 次稳定。

#### Changed

- **V-13 「定义了但没人调」全仓审计 → 双份真相接线 + 死代码清理 + 回滚入口**：用 AST 收集
  `medkit/**` 的模块级定义并统计引用次数，零引用者为候选（本轮两个 P0 正是这条线挖出来的——
  `db.import_from_json()` 自 S0 起零调用方）。15 个候选中 **10 个是「双份真相」**：常量放在 A 处、
  生效值却以字面量硬编码在 B 处，**改常量不生效**。现接线为单一真相：
  `core.explain.WEB_MATERIALS_LIMIT/WEB_SNIPPET_LIMIT`（原 `agents/medexplain` 写死 `4`/`[:240]`）、
  `websearch.EXTERNAL_BACKENDS/NO_SEARCH_BACKENDS`（原 `orchestrator` 写死 `"bocha"`/`"manual"`）、
  `review.CARD_STATES`（原 `stats()` 各档写死字面量）、`scheduler.SCHEDS`（原 `make_scheduler`
  **静默**回退默认 → 现校验 + 留痕）。纯死代码（应用/测试/前端三方零引用）删除四处：
  `tutor.QUESTION_LABELS`、`providers.PRICE_NOTES`、`medexplain.WEB_TIMEOUT`、`pagechrome.PRINT_BASE`。
  另**修正 ADR-006 的回滚方案**：原文写「`db.downgrade_to(0)` + 恢复 `.bak` 原文件名 → 回到 JSON 模式」，
  但 `_store_is_sql()` 判据是 `DB_FILE.exists()`——只 DROP 表**回不到 JSON 轨**（表空了、轨没变，
  界面又「全空」），且 `downgrade_to()` 全仓零调用方、无任何可执行入口。
  现新增 **`pack/rollback-json-track.py`**（默认 dry-run、`--yes` 执行；按「移走 db 文件 → 恢复 `.bak`
  → 回查是否真的回落 JSON 轨」的正确顺序；db 文件改名保留不删除），并勘误 ADR-006。
  新增 `tests/test_v13_single_source.py`（7 例：结构守卫 + 行为断言 + 「纯死代码不得回流」）。
  审计复扫零引用候选由 **15 → 5**（余下 5 个均为正当公开 API / 测试契约 / 待产品决策项）。

#### Tests

- **V-14 把「零 CDN / 零构建」从口头规矩变成机械闸门**：`borrow-rules` §1/§2 与 R6 判据 P7 都写明
  「零 CDN、零构建、产物可离线打开」，但**此前没有任何自动检查**（典型「已立规矩未入闸」：
  后人加一行 `<script src="https://cdn…">` 不会有任何反馈，而它会让「断网可用」的承诺静默失效）。
  现补**两道互补闸门**：
  - **静态**（`tests/test_v14_zero_cdn.py`，3 例）：扫描 `medkit/web/**` 与 `medkit/render/*.py`，
    禁止 `<script src>` / `<link href>` / `@import` / `url()` / 动态 `import()` / `fetch()` /
    `WebSocket|EventSource` / `<img src>` / `<iframe src>` 指向外部（`<a href>` 导航链接除外）；
    并断言前端 script/link 一律走本地 `/assets/`、`package.json` 无运行时依赖且 devDependency
    仅 eslint/globals（防止悄悄引入打包器）。
  - **运行时**（`tests/browser/test_zero_cdn.py`，2 例）：用 Playwright 拦截真实请求，走完
    「首屏 + 各主 tab + 学习中心全部子视图」，**断言所有请求主机都是回环地址** —— 这能抓住
    静态扫描看不见的**动态注入**（`document.createElement("script")` + CDN 地址等）。
  - **反向验证**：静态闸门注入 CDN `<script>` / 产物页注入外部字体 → 均变红；
    运行时闸门注入动态外部 script → 变红。
  - 两道闸门都落在既有 CI 步骤内（单测步 / browser job），无需改 CI 配置。

#### Fixed

- **V-15 附带修复：Anki 卡样预览只显示前 6 个选项**。拆分过程中把「前端源码扫描」类断言的
  扫描面从单文件扩到**全目录**后，立刻暴露一处长期漏检的违规：`ankiPreview()`（当时在 `learn.js`）
  里写死了 `const LETTERS = "ABCDEF"` —— **最多只显示 A~F**，而题库支持 A~J（10 个）。
  R3-16 早就立过「不应再存在 6 位硬编码字母表」的规矩，但其守卫**只扫 `review-desk.js`**，
  而这个函数在 `learn.js` → 规矩成立、检查面不足，缺陷静默存在。
  现改走全局 `letters()`（10 档）；同时把该守卫的扫描面改为**整个 `medkit/web/js/`**，
  同类漏检不会再发生。
  > 教训：**「源码扫描类断言」硬编码单个文件名 = 检查面会随重构漂移**。纯搬迁（拆文件）本不该
  > 让任何断言变色——它变色了，说明断言绑的是「文件」而不是「行为」。

#### Changed

- **V-15（U-17 剩余项）按域拆分超大前端脚本：`learn.js` 2401 行 / `review-desk.js` 2301 行 → 各 4 片，均 ≤800 行**
  —— 至此 R6-09/U-17 的验收判据「最大 JS 文件 ≤800 行」达标。**仍是经典脚本、零构建、零 CDN**
  （未做 ES Module 化，理由见下）。拆分方式为**纯搬迁、零逻辑改动**，并给出机械证据：
  各片去掉新增的 `/* exported */` 头后按序拼接，与原文件**逐字节相同**（两族 SHA256 均一致：
  `learn` `96c58024…` / `review-desk` `c32b36d1…`，2401 / 2301 行）。
  - 分片：`learn` → `learn` / `learn-study` / `learn-live` / `learn-review`（634 / 727 / 585 / 459 行）；
    `review-desk` → `review-desk` / `-materials` / `-project` / `-review`（542 / 556 / 490 / 717 行）。
  - **拆分的硬约束（已入闸门 `tests/test_v15_frontend_split.py`，4 例）**：
    ① 单文件 ≤800 行；② `index.html` **恰好一次**加载每个 JS 且分片族顺序不变（经典脚本顺序即契约）；
    ③ **加载期不得跨片前向引用**——某片加载期立即调用的函数必须在同片或更早片（函数提升只在同脚本内生效）；
    ④ 各片不得从函数体中间切开。为此 `learn.js` 首片保留了 `showLearnView` / `sylLoad` /
    `sseAbortAll` 与三个 IIFE（`initLearnView()` 加载期就会经 `showLearnView` 调到后两者）。
  - 验收：`node --check` 10 个文件全过；`npm run lint` **0 warning**（原文件的 `/* exported */` 清单
    344 / 315 个名字已按名字落位到各片）；**浏览器层 37 例全绿**；反向验证 3/3 有效
    （跨片前向引用 / 单文件超 800 行 / index.html 漏加载分片 → 均变红）。
  - **未做 ES Module 化**（U-17 的另一半）：会改 `index.html` 的加载语义（顶层 `const` 不再是全局
    词法绑定），收益是工程整洁、风险是整站静默失效；判据已用更低风险的纯搬迁达成，故留档不做。

#### Chore

- **V-06 工程卫生**：`.coverage`（二进制覆盖率数据）此前被版本库跟踪，每次跑测试都产生脏 diff
  → `git rm --cached` 并加入 `.gitignore`；删除空目录 `docs/design/`；
  对两份审查文档中 U-17 涉及的 `.eslintrc.json` 补勘误注（实际落地为 `eslint.config.js` flat config）。

## [0.10.2] - 2026-09-15

> **本版为何要 bump**：R5 批次的数据安全修复（含 R5-01「测试污染真实用户库」止血）此前只存在于
> 工作区，未进入任何发布口径——`dist-installer/MedKit-Setup-0.10.1.exe` 构建于 09-01 12:48，
> 早于修复提交 09-01 14:12，**同名同版本对应两个不同产物**，用户无法区分。本版把 R5 修复
> 与三报告整合后的改进一并纳入发布口径（R6-02 / 独立核查 B-1·B-2）。

### UX 与门禁（三报告整合 · U 批次 1）

> 依据同日三份报告去重合并的 `docs/reviews/优化清单与执行记录_2026-09-15.md`（U-01~U-24）。

#### Fixed

- **U-02 刷题「铺卡（薄弱点全部入队）」405 修复**：前端 `learn.js` 用 GET 调用了后端只注册
  POST 的路由（`routers/library.py` 的 `/api/library/review/queue-all`），批量入队按钮**完全不可用**；
  现补 `method:"POST"`。新增 `tests/test_u_batch1_fixes.py` 的**前后端方法一致性**守卫
  （全仓扫描前端 `api()` 调用，POST-only 路由必须显式 method），并做反例验证（回退后用例必红）。
- **U-03 医学记忆卡功能整体不可见**：`learn.js` 判断 `window.FEATURES`，而 `app.js` 用 `const FEATURES`
  声明（不挂 window）→ 判定恒为假，「🧠 生成记忆卡」入口与记忆卡面板**永久不渲染**（v0.8.1 功能
  静默失效）。现改为直接判断 `FEATURES`，并加守卫断言禁止 `window.FEATURES` 回归。
- **U-06 正式生成的网络检索必然失败（静默失败）**：`orchestrator` 只用 `web_search.api_key`，
  而内置后端（deepseek_tool/zhipu_tool/qwen_tool）实际需要**服务商 LLM Key**。现对齐
  `routers/search.py` 的回退口径（非 bocha 后端复用服务商 Key），并在无可用 Key 时**生成前告警**，
  不再「界面显示已开启、跑完 3 轮才写复核清单」。
- **U-07 查重告警的重复题仍进产物**：dup 为 warn 级，MedFix 两轮后仍重复的题原样进最终题库与
  押题卷（同一道题刷两遍）。现改为**不剔除但在产物页显示「⚠ 疑似重复」标记**并写入人工复核清单
  （避免过度剔除误伤题库规模），标记随题目字段流转。
- **U-08 题库页筛选区「重置」按钮显示为字面量 `null`**：`setCounts()` 遍历了所有 `.filters button`
  并读取 `data-label`，而 `#qreset` 无该属性 → `null + ''`。现限定只处理带 `data-t` 的按钮并做
  label 兜底；产物渲染守卫断言禁止旧写法回归。
- **U-19 押题卷回流错题的错因恒为「推理断链」**：`core/library.py` 缺字段时默认 `"reasoning"`，
  伪装成「AI 诊断了你的错因」。现缺省改为 `"unknown"`（显示「未标注」），并对未作答与选错区分
  （`unanswered` → 「未作答」）。

#### Changed

- **U-18 题库页题数口径统一**：头部「共 N 题」与筛选计数（按卡计）不一致（B1 选项组多道子题共享
  一张卡，19 题只显示 17）。现头部改为「共 N 题（M 张卡，其中组内子题 K 道）」。

### 架构与工程品质（三报告整合 · U 批次 2）

#### Fixed

- **U-05 异步端点内执行阻塞重活（单进程事件循环被整体冻结）**：`async def` 端点内同步执行
  秒级~分钟级操作——官方大纲导入**逐科同步调 LLM**（单科 20~60s × 最多 6 科）、教师重点文件
  pymupdf/docx 解析、错题批量导入、单图 200MB 落盘、真题全量词典匹配。期间**整站无响应**
  （静态资源 / `/api/health` / 其余 API 全部挂起），与 0.9.0 用户反馈「体感特别慢」同源。
  现全部改为 `await asyncio.to_thread(...)`（同仓库 `library.py`/`parse.py`/`ocr.py` 早有正确写法）。
- **U-15 静默吞异常无可观测面**：`medkit/` 内 31 处 `except Exception: pass`（用户表现为
  「按钮点了没反应」）全部改为经 `core/errors.py` **留痕 + 计数**；新增只读诊断端点
  `GET /api/diagnostics/errors`（计数 + 最近 50 条摘要），用户可自查失败原因。
- **U-15 / R6-19 错误体回显模型原始输出**：`core/llm.py` 的 JSON 解析失败与契约校验失败
  异常串内嵌模型输出片段（`t[:80]!r` / `ValidationError` 详情），会经错误体回显给用户；
  现原文**只写日志**，对外统一为中文可读提示。
- **U-15 / R6-20 日志与回显无敏感字段过滤器**：新增 `errors.redact()`（掩码 `sk-***` 与
  `Authorization/api_key: ***`，限制长度），`record()`、`_err_response()`、日志输出统一过一遍。

#### Changed

- **U-09 分层修复：消除 `core → routers` 反向依赖 + SQL 回归 core**
  - 新建 `core/projects.py`：`project_dir` / `subject_lock` / `read_meta` / `write_meta_atomic` /
    `create_project_record`（**建课能力下沉**，含 R3-08 幂等、F3 同秒不合并、F4 sid 重编号语义）；
  - `core/gap.py` 删除 `from ..routers._common` / `from ..routers.projects` 两行反向依赖
    （违反项目自定 P1「单向 routers → core」），改用 core 层原语；
  - **顺带修掉一个潜在 bug**：`core/gap.py` 内 `from ._common import _write_meta_atomic`
    ——`medkit/core/_common.py` 并不存在，「一键刷薄弱」走到该行必然 `ImportError`；
  - 路由层 `DELETE FROM syllabus_items` 下沉为 `syl.replace_teacher_chapters`；
    路由层 8 处 `dbs.migrate()` 删除（core 域函数本就按需迁移，另为 `list_subjects`/`coverage` 补齐）。
- **U-14 迁移 v7：v1 五张表补齐索引**：`mistakes(subject,state,learned)` /
  `knowledge(subject,state)` / `explains(subject)` / `review_cards(subject,due,state)` /
  `tutor_sessions(subject,state)`。v1 建表时数据量小未建索引，而 v2/v3/v5 新表都补了索引、
  旧表未回补 → 学习中心列表/掌握度/推荐按 subject/state/due 过滤时全表扫描。

#### Docs

- **U-11 新增 `docs/adr/ADR-006-retire-json-store.md`**：JSON/SQLite 双轨的**退役条件、步骤、
  回滚方案与影响文件清单**。ADR-001 早已决策 SQLite，但 JSON 回落分支长期无退役时间表，
  而「DB 不存在即回落 JSON 并原子写真实路径」正是 R5-01（测试污染真实用户库）的机制根因。

#### Tests

- 新增 `tests/test_u_batch2_engine.py`：AST 守卫 async 端点阻塞调用 / `core→routers` 反向依赖 /
  路由层直写 SQL 与迁移 / 静默 `pass` 收敛（≤5）/ 迁移 v7 升级回滚 / 脱敏与诊断端点 /
  LLM 异常不回显模型输出。

#### Changed

- **U-10 上帝函数拆分（超项目自定 400 行判据）**：AST 实测两个超限函数已全部拆到判据内。
  - `core/orchestrator.py::_run_project_impl` **727 → 342 行**，按阶段抽为 4 个函数：
    `_stage_websearch`（⓪ 多轮网络检索，98 行）· `_stage_generate`（① MedGen 出题：并发/串行
    两路 + 断点续跑 + 可取消，含原嵌套闭包 `gen_one`）· `_stage_gate1`（② 门禁① 修复循环，
    173 行）· `_stage_qc_fix`（④ 质检 BLOCKED 定向修复，59 行）。
    取消路径统一改为「阶段返回 `cancel_out` 标志、调用方单一出口」，不再在函数中部散落
    `return 管线结果字典`；阶段边界即回滚边界，可单独测试。
  - `render/qbank_html.py::export_paper_html` **440 → 35 行**，抽为 `_paper_head` /
    `_paper_js_state` / `_paper_js_grade` 三个片段函数；拆分经**逐字节等价校验**
    （14 个用例 × 2 个导出函数，拆分前后输出完全一致）。
  - 新增 `tests/test_u_batch2_engine.py` 两条 AST 守卫：**无 ≥400 行函数** + 四个阶段函数存在且各自 <400 行。

### 发布产物（U-01 重新出包）

- **U-01 发布产物重新出包（0.10.2 两件套）**：`pack/build.bat` 同款流程重跑——
  PyInstaller onedir 绿色版 → `pack/check-package.py` 纯净检查**通过** → Inno Setup 出安装包。
  产出 `dist-installer/MedKit-Setup-0.10.2.exe` 与 `MedKit-0.10.2-portable.zip`。
  **产物冒烟实测**（隔离 `USERPROFILE`，不触碰真实 `~/.medkit`）：
  `GET /api/health` → `{"ok":true,"version":"0.10.2","stage":"ready"}`（版本确为 0.10.2）；
  `GET /api/diagnostics/errors` → `{"counts":{},"total":0,"recent":[]}`（U-15 新端点在产物中生效）；
  静态资源 200。包内前端资源已核验含 U-02/U-03/U-19 修复。
  **U-10 拆分完成后已再次出包**（产物与最终源码一致，2026-09-15 20:37/20:38）。

### 依赖与合规（三报告整合 · U 批次 3）

#### Added

- **U-16 `requirements.lock`（38 项运行时依赖闭包，含 `uvicorn[standard]` 等 extras）**：
  此前 `requirements.txt` 10 项全为 `>=`（仅 `genanki` 精确固定），无锁文件 → 同一文件不同时间
  装出不同依赖树，构建不可复现。现 Windows（实际打包平台）按锁文件复现构建。
- **U-16 `THIRD_PARTY_NOTICES.md`**：列出全部 34+ 组件的版本与许可证，回答「安装包含哪些第三方
  组件、什么许可证」。**关键发现：`PyMuPDF` 为 AGPL-3.0 / Artifex 商业双授权（强传染性），
  `frozendict` 为 LGPL-3.0**——分发前需产品决策（购买商业授权 / 接受 AGPL 开源 / 替换实现）。
- **U-16 CI 依赖漏洞审计**：`pip-audit -r requirements.txt --strict`（发现已知漏洞即失败，
  并预留 `--ignore-vuln` 白名单登记位）。本地实跑结果：**无已知漏洞**。
- **U-16 `.github/dependabot.yml`**：pip 生态 weekly 自动更新（运行/开发依赖分组，PR 走既有总闸）。

#### Changed

- **U-16 CI 依赖安装**：Windows 改用 `requirements.lock`（复现构建）；Linux 仍用
  `requirements.txt`（锁文件由 Windows 环境生成，不含 `uvloop` 等仅非 Windows 生效的依赖）。
- **`requirements-dev.txt`** 增 `pip-audit`（并保留 `setuptools<81` 的 jieba/pkg_resources 锁定说明）。

#### 待产品决策（未执行）

- **`LICENSE` 暂缓写入**：项目当前无许可声明，但**若声明 MIT 会与「随产物分发 AGPL 的 PyMuPDF」
  自相矛盾**。许可选择与 PyMuPDF 的 AGPL 决策耦合，须先定案依赖策略再写 `LICENSE`，
  不宜由工程侧单方面选定（详见 `THIRD_PARTY_NOTICES.md` §需注意的许可证）。

### 前端静态防线（三报告整合 · U 批次 6）

#### Added

- **U-17（部分）ESLint 前端静态检查**：新增 `package.json`（仅 devDependency：`eslint` + `globals`）、
  `eslint.config.js`、`package-lock.json`，并接入 CI（`npm run lint`，`--max-warnings 0`）。
  **不引入打包器**——产物仍是零构建、零 CDN 的原生 `<script src>`，本配置只做只读检查。
  配置要点：因经典脚本共享全局作用域（`app.js`/`learn.js`/`review-desk.js` 靠「加载顺序即隐式契约」
  互相调用），**逐文件自动收集「其它文件的顶层声明 + `window.X =`/`global.X =` 挂载」**作为共享
  全局符号——`no-undef` 既能抓「拼错函数名」（原先只有运行时才炸），又不误报合法的跨文件调用；
  被外部引用的顶层声明在文件首行以 `/* exported … */` 标注（脚本模式下 `no-unused-vars` 靠它识别）。

#### Fixed

- **U-17 上线即抓到一处真实缺陷**：`learn.js` 调用了**全仓不存在的 `rexAnalyzeRender()`**——
  该分支在「真题草稿超 200 条、确认前 200 条后重渲染剩余」时才走到，长期未被发现，运行时必抛
  `TypeError`。现改为 `renderRexDrafts(REX_STATS)` 并新增模块级 `REX_STATS` 缓存分析统计。
- **U-17 清理 ESLint 实测的死代码**：孤儿函数 `tutorRowCore`（全仓无调用者）· 未使用的
  `REVIEW_SITE` 常量（同一 URL 已硬编码在 `index.html` 导航链接）· 未使用的局部量
  `my`（`loadTutorCtx`）· `chars`（旧内嵌成本公式删除后遗留）· `r`（`savePrompt`）。
  另标注 `sessionItem` 中「算了 `lv` 但模板从未使用」——疑似曾计划显示会话层级，已在注释中留痕。

#### 说明

- **U-17 的「ES Module 化 + 按域拆分超大 JS（learn.js 2402 / review-desk.js 2299 行）」未执行**：
  该步需改 `index.html` 的脚本加载方式，而**浏览器层回归网在当前环境不可用**
  （`agent-browser` 不支持 Windows；Playwright chromium 未安装）——无回归网改加载顺序风险过高
  （§4.4 自述「加载顺序即隐式契约」）。静态防线已就位，为后续该步提供了安全网。

### 测试可信度（三报告整合 · U 批次 4）

#### Added

- **U-12 覆盖率度量与门槛**：新增 `pytest-cov`（dev 依赖）与 `[tool.coverage.*]` 配置；
  CI Test 步加 `--cov=medkit --cov-report=term-missing --cov-fail-under=80`。
  **实测基线 82.45%（8558 语句 / 1502 未覆盖）**，门槛取 80%（留 2 点余量防抖动，只升不降）。
  此前仓库无任何覆盖率配置——489 个用例无法回答「改动是否被任何用例触达」。
- **U-12 结构性「防丢断言」`tests/test_stream_wiring.py`**：断言**实现结构**而非行为——
  讲解/提问两条流式接缝必须含 `cancel_ev = threading.Event()`、`dedupe.begin(` 在首帧之前、
  且 `dedupe.end(` 与 `cancel_ev.set()` 位于 `finally` 块内；去重键来自 `_explain_key`/`_tutor_key`。
  这是 R5-02 的直接教训（`CHANGELOG`/handoff/R4 报告三处记载「已落地」而代码从未提交、测试却全绿
  ——因为用例把缺陷接缝整个 mock 掉了）。**已做反向验证**：把 `dedupe.end` 移出 `finally` 后
  用例必红，恢复后复绿。
- **`docs/AGENT_HANDOFF.md` §5.1「本地总闸 与 CI 步骤对应表」**：7 步逐一对应，判据为
  「任一步两侧定义不一致即为分叉」，消除 R6-01 那类「CI 绿 / 本地红」的判定分叉。

#### Changed

- **U-21 协程驱动统一**：删除 `tests/test_r4_batch3.py` 自有的 `_run_async`（线程池实现）——
  同一问题两种写法（另一种是 `conftest.py` 的 `run_coro` fixture），正是本轮批评的「口径不一致」；
  两个调用点改走 `run_coro` fixture。
- **`docs/AGENT_HANDOFF.md` §4 新增第 15 条「先红后绿」纪律**：修复类改动必须先写出会失败的用例、
  跑红后再改到绿（verify-the-test-fails）。

### 安全（三报告整合 · U 批次 5）

#### Fixed

- **U-13 产物导出转义覆盖不均（R5-B-11）**：以**恶意输入探针**（把 XSS 载荷灌进每个会进产物的
  字段 + 属性逃逸载荷）实测四条导出路径 × 三种分组形态，定位并修复：
  - **Anki 导出泄漏 `id`**（该文件声明 `#html:true`）：卡面 `<b>Q{id}</b>` 的 `id` 未转义，
    题目 id 含标签时会注入用户卡组 → 改为 `_esc_anki(q.get('id'))`；
  - **Markdown 导出未做任何转义**（`_md_question` 直出原始字段）：新增 `_esc_md()` 对
    `&`/`<`/`>` 实体化（纵深防御——应用内 `md.js` 已转义，但 `.md` 常被第三方阅读器打开），
    覆盖 题库 MD 的单题/案例组/选项组三条路径。
  - 题库 HTML / 押题卷 HTML 实测**无泄漏**（题库走 `html_mod.escape`；押题卷走 JSON 数据岛
    + 前端 `esc()`，且数据岛已把 `</` 转义为 `<\\/` 阻断 `</script>` 逃逸）。
- **U-13 新增 `tests/test_u_batch3_render_safety.py`（17 条）**：把「转义覆盖不均」变成可机械
  核验的断言——2 种载荷 × 3 种分组形态 × 4 条导出路径 + 押题卷 JS 逐字段 `esc()` 校验 +
  数据岛 `</script>` 逃逸校验。**已做反向验证**：回退 Anki 的 `id` 转义后用例必红。

### 工程与闸门（2026-09-15，R6 审查批次 1·3）

#### Fixed

- **R6-01 质量总闸转绿（本地/CI 判定分叉修复）**：`verify.cmd` 与 CI 的单测步此前会连
  `tests/browser` 一起收集——session 级 Playwright 同步上下文（`sync_playwright()`）在整个会话
  期间占住主线程事件循环，导致随后任何 `asyncio.run()` 抛
  `RuntimeError: cannot be called from a running event loop`（本地 3 failed / CI 全绿）。
  现单测步显式 `--ignore=tests/browser`，浏览器层仍由第 [3/4] 步与独立 browser job 承担；
  `tests/conftest.py` 新增 `run_coro` fixture（在新线程驱动协程）并改接 3 个用例。
  另清零 4 处 ruff 错误（`ocr.py` 导入排序、`projects.py` 未用变量、`test_cards.py` 分号拆行、
  `test_wp04.py` 重复字典键）并修正 1 处 invalid `# noqa` 指令。
- **R6-11 打包纯净检查入总闸**：`borrow-rules` §5 要求的 `pack/check-package.py` 此前只在
  `pack/build.bat` 接线；现加入 `verify.cmd` 第 [4/4] 步与 CI verify job（未构建 `dist/` 时
  脚本自行跳过并返回 0）。正反用例已实测：注入 `dist/MedKit/**/tests/` → 失败退出 1，移除后恢复通过。

#### Changed

- **R6-15 README 版本口径对齐**：`已实现功能（v0.8.0）` → `（v0.10.1）`（此前落后 3 个次版本，
  与同文件已同步的 `MedKit-Setup-0.10.1.exe` 口径矛盾）；「开发里程碑」小节明确标注为
  **历史记录（截至 v0.8.1）**，与当前版本能力区分。
- **`docs/AGENT_HANDOFF.md` §4 陷阱清单新增 12/13/14 条**：未推送提交时禁 `git gc/prune/repack`、
  提交后立即 push、多会话并发时 git 写操作必须收敛单线（源自 2026-09-15 仓库事故）。

#### Docs

- 新增 `docs/工程审查改进指南_2026-09-15.md`（R6 六维审查：代码质量/架构/性能/安全/可维护性/
  依赖管理；阻塞 2 · 高 5 · 中 8 · 低 6 + 编号 R6-01~R6-22 + 依赖图与批次）。
- 新增 `docs/reviews/仓库恢复记录_2026-09-15.md`（`.git` 被删除后从回收站恢复导致的对象库损坏
  与 `master` 被重置的取证、恢复过程与收尾清单；内容零损失）。
- 复核撤注：R6-12（NX-06 提示词治理被违反）经逐版本核验为**误报**——`8e69642` 属 v0.9.0
  （该版有 `### Prompts` 小节），v0.9.0 之后无提示词改动，NX-06 合规。

### R5 全链路复核修复（2026-09-01，批次 0/1/2：数据安全 + 流式主路径 + 流程信任链）

#### Fixed

- **R5-01 测试套件不再污染真实 `~/.medkit`**：`tests/conftest.py` 的 autouse 隔离此前只重定向
  `DB_PATH/DB_FILE`——tmp db 不存在时各模块回落 JSON 路径，直接原子写真实
  `~/.medkit/library/*.json`（R5 实机两轮复现：mistakes 4→8 条、knowledge 被整体替换）。
  现把 `MISTAKES_FILE/KNOWLEDGE_FILE/REVIEW_QUEUE_FILE/EXPLAINS_FILE/SLICE_INDEX_FILE/
  TUTOR_SESSIONS_FILE/CARDS_FILE` 全部文件常量一并重定向到 tmp，并新增 session 级
  **防污染哨兵**（套件前后对真实家目录全量哈希，任何触碰即失败并列出变更文件）。
- **R5-04 用户学习数据恢复（取证修订）**：`pack/recover-user-data.py`——实机取证发现备份目录
  顶层 `mistakes.json`（113KB/182 行）与 `knowledge.json`（6.3KB/3 行）**全部为 08-27 20:43 后
  的测试垃圾**（subject 全空、题目=测试题甲/乙/肺通气题/心绞痛题），不能作恢复源；真实数据在
  **污染前**产物中：1 条错题（m_1787812464960，儿科学·支气管肺炎）+ 4 条知识点（含 DKA 等）+
  1 张复习卡 + 1 个提问会话，经脚本按「缺失才插入、id 幂等、绝不过覆盖」回库（先全量快照
  `~/.medkit` 可回退）。
- **R5-02 流式「在飞去重」真绑定流生命周期**：`routers/library.py` 两个流式端点
  `explain_stream`/`tutor_start_stream` 的 `dedupe.begin` 移入 `gen()` 首帧前、`end` 入
  `finally`（覆盖 done/error/canceled/断连 GeneratorExit）；Depends 守卫降级为
  `dedupe.is_active` 窥视式早拦截（实验证实本 FastAPI 版本 Depends teardown 在流完成后才执行，
  守卫持锁会与 gen 内锁同请求自锁）。并发双击/双标签同名请求：流进行中 409/error 帧，
  流结束锁即释放。
- **R5-03 服务端取消接线**：两流式端点创建 `cancel_ev` 并经
  `_explain_client(cancel=…)`/`_tutor_client(cancel=…)` 传入 client；`gen()` `finally`
  置位；`LLMClient.chat_stream` 在结束/取消/异常/断连（生成器被 close）时 `stream.close()`
  立即中止 provider 连接——前端「■ 停止生成」不再只断 fetch，服务端 token 真停。
- **R5-C-02 流式 usage 记账取消/异常补记**：`core/llm.py` `chat_stream` 改为「chunk 累计 →
  结束/取消/异常处一次落账（`UsageContext` 快照）」，取消/异常前已见的 token 不再整段漏记。
- **R5-A-01 项目模板键绑 pid**：`review-desk.js` 模板 localStorage 键 `medkit-tpl-project` →
  `medkit-tpl-<pid>`（新建课题表单用 `__new__` 作用域）——A 项目模板不再污染 B 项目表单。
- **R5-B-01 assets 累计容量上限**：`routers/projects.py` 新增 `_MAX_ASSETS_TOTAL`（600MB/项目），
  写入前统计 `assets/` 总字节，超限 400 并附「当前占用/上限」，超限零磁盘副作用。
- **R5-B-05/15 项目删除复核**：`delete_project` 删后 `base.exists()` 复核——rmtree 静默跳过的
  残留/异常显式 500 报错并提示手动清理，不再返回 `{"ok": true}` 假装成功；`list_projects`
  孤儿项标注「残留目录，可清理」。
- 测试：`tests/test_explain_stream.py` +6、`tests/test_tutor_stream.py` +2、
  `tests/test_dedupe.py`（`is_active`）、`tests/test_wp04.py` +2、`tests/test_r3_batch3.py`
  断言更新；离线全量基线不回归（见下）。

#### Changed

- **R4-05 产品决策定稿**：结构化大纲行为定为「**校验通过即自动入库**」——「AI 结构化」返回
  `source/added` 即幂等写入官方大纲（`source='seed'`），前端只展示统计预览，**无二步「预览→确认」**；
  如需人工把关请在结构化前自行核对粘贴原文（原文 sha1 存 `~/.medkit/outline_originals/` 可审计）。
  该行为自 R4-05 起稳定，本条目为其正式明示（对应 R5 审复核 §3.1 决策项，关闭）。
- `medkit` 修复类提交工程规则（AGENT_HANDOFF §4.11「提交自查三步」）：① 提交前
  `git diff --cached` 关键标识 grep；② CHANGELOG/handoff 条目引用「已验证的 file:line」；
  ③ 批次提交后 `git show HEAD` + 关键行抽查。

> **⚠️ 勘误（2026-09-01 · R5 复核）**：本文件上面「R4 全链路复核修复」中的 **R4-01/R4-02 条目
> 与提交不符**——`8e603a0` 实际只引入了 FastAPI Depends 守卫（`gen()` 内无 dedupe，路由未创建
> `cancel_ev`，服务端 `canceled` 分支为死代码），「已落地」为失实记载；两项已由本版 R5-02/R5-03
> 真落地（git show 关键行可验）。R4-03/R4-04 经核对确已落地，不受影响。

## [0.10.1] - 2026-09-01

### 网络检索后端连通性修复（2026-09-01）

#### Fixed

- **搜索超时 25s → 75s**（`core/websearch.py MAX_HTTP_TIMEOUT`）：实测 DeepSeek Responses web_search 单次 20~60s（两次检索词调用 + 推理），旧值必超——「测试后端」与出题管线网络检索频发 `ReadTimeout`，界面显示「连接超时」（用户实测「网络检索后端测试依然失败」的根因）。同步放宽全部检索后端单次超时。
- **DeepSeek 结果提取补强**（`search_deepseek`）：按 2026-09-01 实测响应结构解析——`web_search_call.action.url`（含 `#ws_call_id` 追踪片段，存储时去除）、`message` 的 `annotations[].url_citation` 与正文裸 URL，按「去片段」URL 去重合并；不再只依赖「首个 message 兜底」。
- **0 结果提示明确化**：`/api/search/test` 服务端已连通但未提取到结果时，msg 改为「已连通，但本次未提取到结果——可重试、换关键词，或改选其它后端」（此前「连通（0 条）」易被误认为失败）。
- 测试：`tests/test_websearch.py::test_search_deepseek_parses_real_responses_shape`（真实响应结构 mock：action.url / annotations / 正文去重）；实机验证 `POST /api/search/test` → `ok=True count=5~7`。

## [0.10.0] - 2026-08-31

### PR-1 开始页多场考试计划 + 红点来源可感知（2026-08-30）

#### Added

- **多场考试计划**（WP-1）：开始页“考试计划”支持添加/编辑/删除多场考试（名称/日期/标签/考前提醒 3·7·14·30 天），按日期升序展示、最近一场优先；旧单场键 `medkit-exam-date` 自动迁移为一场考试（`medkit/web/js/app.js` `renderExamPlans` 系列）。
- **红点来源可感知**（WP-7）：侧栏红点带 `title`/aria 说明（今日到期复习 N 张 / 进行中提问 M 场）与 `data-source`；学习中心概览新增“侧栏红点来源”说明条；子导航计数徽章带来源说明（`medkit/web/js/learn.js` `setNavTabBadge`/`updateLearnBadges`/`renderDashboard`）。
- 浏览器用例：`tests/browser/test_start_exams.py`（多场添加/排序/删除/迁移 + 红点 title 与学习中心说明条）。

#### Changed

- 开始页倒计时卡片文案更新为“考试计划 · 支持多场”。
- `medkit/web/css/base.css` 新增考试计划卡片/表单样式。

### PR-2 出题进度条修复（WP-2，2026-08-30）

#### Added

- **进度子步骤模型**：`progress.json` 增加 `sub/sub_done/sub_total`，`_set_progress` 支持子步骤粒度（`medkit/core/orchestrator.py`）；新增阶段序列常量 `PIPELINE_STAGES`。
- **门禁①四类检查逐项上报**：选项校验 / Bloom 校验 / 溯源回查 / 查重各占 1/4 子进度；QC 按批次、修复/汇总/复习/渲染按产物逐步上报。
- **前端 stepper 空进度修复**：`pct=0` 不再空白，显示“准备中…”；子步骤显示“选项校验 1/4”等（`medkit/web/js/review-desk.js` `renderStepper`）。
- 测试：`tests/test_orchestrator_progress.py`（进度字段/阶段序）、`tests/test_pipeline_offline.py::test_pipeline_progress_substeps`（离线全链路子步骤）、`tests/browser/test_pipeline_progress.py`（stepper 渲染）。

#### Changed

- 出题阶段/质检/修复/汇总/复习/渲染进度调用补充子步骤信息；`progress.json` 旧字段保持兼容。

### PR-3 门禁 subagent 分步可视化（WP-3，2026-08-30）

#### Added

- **子步骤事件流**：`{project}/substeps.jsonl` 每行一条 `{stage, step, label, status, detail, ts}`（status=pending/running/done/failed/retry），`_substep()` 追加写并保留最近 200 行；`_run_substep(ttl=60, retries=2)` 守护线程超时/重试包装，重试用尽降级写「人工复核清单.md」并继续（不中断管线）。
- **门禁/质检/修复逐步上报**：门禁① 图像引用 + 选项/Bloom/溯源/查重四类检查、QC 每批、MedFix 每条 issue、渲染前终检/复习手册/题库/押题卷/Anki 均写子步骤事件（`medkit/core/orchestrator.py`）。
- **状态接口**：`GET /api/projects/{pid}/status` 与新详情读项目均返回最近 50 条 `substeps`（`medkit/routers/projects.py` `_read_substeps`）。
- **前端子步骤面板**：`#pd_substeps` 与 stepper 并列，按当前阶段过滤；运行中高亮 + 呼吸图标、完成打勾、失败红标、重试提示、`<details>` 可展开详情（`medkit/web/js/review-desk.js` `renderSubsteps` + `medkit/web/css/base.css`）。
- 测试：`tests/test_pipeline_events.py`（事件裁剪/超时重试/重试成功/降级/路由读取与 status 返回）、`tests/test_pipeline_offline.py::test_pipeline_writes_substeps_e2e`、`tests/browser/test_substeps_panel.py`。

#### Changed

- R3S-03 记账兼容：`_run_substep` 线程内先取父线程 `contextvars.copy_context()` 再运行，QC/MedFix 子步骤不丢 token 账本。
- `docs/engineering/borrow-rules.md` 新增「已落地借鉴点（0.10.0 滚动记录）」表（DSH 事件流/子任务视图/重试）。

### PR-4 刷题科目切换与删除（WP-4，2026-08-30）

#### Added

- **科目卡片图标化**：首字徽章（本地渐变样式，无图片资源）+「全部科目」📚 卡片；点击过滤复习计划仍生效（`medkit/web/js/learn.js` `loadStudySubjects` + `medkit/web/css/learn.css`）。
- **科目管理弹层**：刷题页科目卡右上角「科目管理」入口，列出全部科目 + 错题/知识点/复习卡统计，每科可删除（`subjectMgrOpen`/`subjectDelete`）。
- **删除科目（自动备份）**：`medkit/core/library.py::delete_subject_with_backup(subject)` 删除前把该科错题、知识点、复习卡、记忆卡、提问会话、讲解产物导出 JSON 到 `~/.medkit/exports/subject_<safe>_<ts>.json`，再清理；SQL 模式单事务逐表删除，JSON 模式逐模块原子写。
- **删除端点**：`POST /api/library/subjects/delete`（Pydantic body `{subject}`），返回 `{ok, deleted:{mistakes,knowledge,review_cards,memory_cards,sessions,explains}, backup}`（`medkit/routers/library.py`）。
- 删除后刷新：`loadStudy()`（科目卡片 + 复习计划）+ `loadLibrary()`（概览/dashboard/badge）。
- 测试：`tests/test_subject_delete.py`（JSON/SQL 双态：备份存在、计数正确、另科保留、路由返回）、`tests/browser/test_study_subjects.py`（卡片点击过滤 + 弹层删除后卡片消失）。

### PR-5 错题本批量/分类/折叠（WP-5，2026-08-30）

#### Added

- **错题多选 + 工具栏**：每行 checkbox；全选可见 / 反选 / 清空 / 批量删除 / 标记已掌握 / 导出 JSON / 导出 MD（`learn.js` `mkSelected` + `mkToggleAllVisible`/`mkInvert`/`mkClearSel` 等）。
- **三级分组折叠**：科目 → 章节 → 标签（知识点）`<details open>` 分组，组头显示计数与「本组全选」；保留首屏 100 条分块 + 「加载全部」（`mkGroupHTML` + `learn.css`）。
- **批量删除（自动备份）**：`core/library.py::batch_delete_with_backup(ids)` 删除前导出 `~/.medkit/exports/mistakes_batch_<ts>.json`；`batch_mark_learned` 批量归档标记；`export_mistakes(ids, fmt)` 支持 json/md。
- **批量端点**：`POST /api/library/mistakes/batch-delete | batch-learn | batch-export`（ids ≤500，空 ids 400；`medkit/routers/library.py`）。
- **联动**：批量删除/标记后 `loadLibrary()` 刷新掌握度与近期活动；`renderLibrary` 数据刷新自动清空多选。
- 测试：`tests/test_mistake_batch.py`（备份/幂等/学习/导出/路由校验）、`tests/browser/test_mistakes_batch.py`（分组折叠 + 勾选 2 条批量删除 + 导出下载 + 已掌握过滤）。

### PR-6 网络检索修复与可信源（WP-6，2026-08-30）

#### Added

- **可信来源**：`websearch.py` 新增 `TRUSTED_SUFFIXES`（gov.cn/edu.cn/who.int/nih.gov 等）与 `TRUSTED_DOMAINS`（msdmanuals.cn/dayi.org.cn 等）；`trusted_filter` 打 `trusted` 标记 + 可信优先排序，`trusted_only=True` 过滤不可信；`digest_for_prompt` 标 `【可信】`。
- **配置**：`web_search.trusted_only`（默认关）+ `trusted_domains` 自定义域名列表（`routers/config.py` + `config.py` DEFAULTS）；设置页新增开关与域名输入框（`review-desk.js` + `index.html`）。
- **错误中文化**：`routers/search.py::_search_error_hint` 把 401/403/超时/网络不可达/参数 400 映射为可操作中文原因；内置后端复用服务商 Key，缺 Key 明确报错。
- **失败降级**：`orchestrator.py` 网络检索错误写 `run.log` + 追加「人工复核清单.md」（`_append_manual_section`），生成继续；项目级 `web_backend` 已由 `projects.py` 落 meta。
- 测试：`tests/test_websearch.py`（可信排序/过滤/自定义域名/多轮注入）、`tests/test_search_router.py`（四后端 mock 全绿 + 超时中文 + 配置往返）、`tests/browser/test_search_settings.py`（设置页可信开关往返 + manual 测试文案）。

### PR-7 沉浸式讲解/提问 + 流式（WP-8，2026-08-30）

#### Added

- **`LLMClient.chat_stream`**：OpenAI 兼容流式生成器，yield `{delta, usage, canceled}`，复用取消事件（`medkit/core/llm.py`）。
- **SSE 端点**：`POST /api/library/explain/stream`（meta → delta* → done/error，完成才落盘 explains）与 `POST /api/library/tutor/start/stream`（出第一问流式；成功才 seed 会话；取消/失败回滚会话）。判分（answer）保留 JSON 契约，保证状态机/掌握度稳定。
- **Agent 复用重构**：`medexplain.prepare_explain` / `medtutor.build_start_messages` / `build_score_messages`——流式与非流式共用同一 prompt 构造。
- **前端 SSE 消费**：`learn.js::consumeSSE`（ReadableStream 手工解析）+ 讲解/出题流式增量区（`#exp_live` / `#tu_live`）；`expGenerate`/`tutorStart` 优先流式、失败自动回退非流式端点。
- **取消/降级**：流式 canceled 事件停止并撤销未完成会话；非流式端点保留兼容旧客户端。
- 测试：`tests/test_explain_stream.py`（chat_stream mock + explain SSE 事件/落盘/错误不落盘）、`tests/test_tutor_stream.py`（tutor start SSE/会话 seed/错误回滚）、`tests/browser/test_tutor_immersive.py`（拦截 SSE 验证前端增量渲染）。

### PR-8 大纲管理重构（WP-10，2026-08-30）

#### Added

- **功能改名**：学习中心“大纲覆盖” → “大纲管理”；角色标签“教师重点（主要依据）/ 官方 306（补充）”；旧数据（seed/teacher）保留展示。
- **AI 结构化大纲**：`core/syllabus.py::structurize_outline`（LLM 契约抽取 + 完整性校验 ≥95% 条目 + **原文 sha1 双存储** `~/.medkit/outline_originals/`）；失败保留原文不替换。
- **端点**：`POST /api/syllabus/outline/structurize`（返回 `{ok, structured, stats, diff, original_path, note}`）。
- **出题角色**：项目 `official_quota`（0~30，默认 0 = 仅教师重点）；`orchestrator.py` 教师重点为主线 `source="teacher"` + 官方306 按配额补充；`chapter_items_text` 支持 source 过滤。
- **前端**：新建课题表单新增“官方 306 补充条目数”；大纲管理视图新增“AI 结构化预览”按钮（diff/原文路径展示）。
- 测试：`tests/test_syllabus_manage.py`（round-trip/失败保留原文/角色过滤）、`tests/browser/test_syllabus_manage_ui.py`（无“大纲覆盖”文案 + 角色标签）。

### PR-9 外部做题数据导入（WP-11，2026-08-30）

#### Added

- **站点导入 schema**：`{source, subject, chapter, topic, question, options, answer, user_answer, analysis, tags, occurred_at, extra}`（`core/library.py::import_site_items`）。
- **幂等去重**：sha1(norm(subject)|norm(chapter)|norm(question))；命中则更新答案/解析/选项/作答/标签，不重复新增；单条缺失/异常记入错误列表并跳过。
- **端点**：`POST /api/library/mistakes/import-export`（JSON body `{items:[...]}`；空 items 400），返回 `{ok, added, updated, skipped, errors}`。
- **前端**：错题本新增「站点数据(JSON)」按钮；`mkBatchFile` 对含 items 的 JSON 自动路由 import-export，其余 JSON 走旧 import-file；导入后 `loadLibrary()` 刷新。
- 测试：`tests/test_mistake_import_export.py`（幂等/更新/跳过/路由 400）、`tests/browser/test_site_import.py`（拦截端点验证前端导入流）。

### PR-10 视觉与富文本输出（WP-9，2026-08-30）

#### Added

- **本地 Markdown 渲染器**：新增 `medkit/web/js/md.js`（`window.mdRender` / `mdHighlight` / `mdKeywords`）——标题/列表/表格/代码/引用/加粗/分隔线，**先转义再解析**，XSS 安全，零 CDN。
- **统一富文本**：讲解 `expMd` 委托 `mdRender`；提问问题/判分反馈（`.tu-q`/`.tu-gap`）富文本展示；医学关键词高亮扩充（首选药/金标准/确诊/禁忌/一线/休克等）。
- **样式统一**：`.exp-article` / `.tu-q` / `.tu-gap` 表格、表头、分隔线样式；明暗主题沿用 CSS 变量。
- 测试：`tests/test_render_markdown.py`（node vm 执行 md.js：富文本 + XSS）、`tests/browser/test_richtext.py`（浏览器侧 mdRender/expMd）。

### PR-11 纯净安装包（WP-12，2026-08-30）

#### Added

- **spec 纯净**：`medkit.spec` datas 移除 `("medkit/data", …)` 与 `("data/syllabus_seed_306.json", …)`——示例素材、内置大纲种子**不进 dist**（仅仓库/CI 保留）。
- **示例降级**：`POST /api/sample` 缺示例返回 `{sample:False, available:False, error}`；前端 `probeSampleAvailability()` 禁用“载入示例”按钮并显示“示例仅开发版可用（纯净版请自备素材/上传官方大纲）”。
- **大纲降级**：`ensure_seed` 无种子返回“未内置大纲（纯净版）：可上传官方 306 大纲(md/txt) 或使用教师重点”；`sylEnsure` 文案同步。
- **纯净检查**：新增 `pack/check-package.py`（pathlib 扫描 `dist/MedKit/`，blacklist：`samples` / `syllabus_seed_306.json` / `tests` / `__pycache__` / `.pyc`）；`pack/build.bat` 构建后自动运行，失败即构建失败。
- **README**：绿色版说明改为“纯净版不含任何学科/题目/样例/测试数据，请自行上传教材/教师重点，官方 306 大纲在「大纲管理」一键导入”。
- 测试：`tests/test_check_package.py`（干净通过 / 残留失败 / 缺失跳过）、`tests/browser/test_sample_purity.py`（按钮降级）。

### R4 全链路复核修复（2026-08-31，批次 1/2：流式主路径 P0/P1 + 后端健壮性）

#### Fixed

- **R4-01 流式去重改绑流生命周期**：`dedupe.begin/end` 移入生成器 `finally`——覆盖 done/error/canceled/断连 GeneratorExit，不再「响应对象返回即释放锁」导致并发双跑双扣费（`routers/library.py`；测试 `tests/test_dedupe.py`）。
- **R4-02 流式取消全链路**：前端 `AbortController` +「■ 停止生成」按钮 + 切视图/页签即 `sseAbortAll()`；服务端 `cancel_ev` 传入 client，`StreamingResponse` 断开 `finally` 置位（`app.js`/`learn.js`/`library.py`；测试 `tests/test_explain_stream.py::test_explain_stream_canceled_no_save`）。
- **R4-03 断流不再自动回退非流式**：仅流式接口不可用（`streamed` 未置位）才降级——AbortError/断流/出错一律不二次请求，杜绝断流重生成双倍扣费。
- **R4-04 tutor 流空会话回收**：`seeded` 标志 + `finally`——未落定（未出第一问）的会话 `delete_session` 兜底删除（测试 `tests/test_tutor_stream.py::test_tutor_start_stream_canceled_session_cleaned`）。
- **R4-05 structurize 产物可回读**：完整性 ≥95% 即自动 `add_seed_items(outline_drafts())` 幂等落库为官方大纲（`source='seed'`，返回 `source`/`added`）；原文 sha1 存 `~/.medkit/outline_originals/` 可审计；不达标保留原文不替换（`core/syllabus.py`；测试 `test_structurize_roundtrip_and_original_store`）。
- **R4-06 资产上传硬上限**：`_MAX_ASSET_BYTES = 200MB`，读后即判，超限 400 且不写盘/不进切片索引（`routers/projects.py`；测试 `test_asset_upload_size_limit`）。
- **R4-07 config 原子写**：`config.save` 统一 `write_json_atomic`（唯一临时名 + Windows 共享冲突重试），与 FTS/状态文件同口径（测试 `test_config_save_atomic_roundtrip`）。
- **R4-08 syllabus 纯读事务**：`_rows`/`list_subjects` 改 `tx(write=False)`，不再 `BEGIN IMMEDIATE` 抢占写锁。
- **R4-12 配额超界统一 400**：`official_quota` 越界（0~30 外）显式 400，与 `web_ref_quota`/`bloom` 口径一致，`meta` 不再静默钳制（测试 `test_create_project_rejects_quota_out_of_range`）。
- 验证：离线 pytest **424 passed**，ruff 干净；批次 1/2 独立提交（`8e603a0`/`c55bb9d`/`9980a27`）。

### R4 发布前回归补丁（2026-08-31，浏览器层 CI 发现并修复）

#### Fixed

- **`_sseAbort` TDZ 击穿整个学习中心脚本**：`let _sseAbort` 声明在 `showLearnView` 之后——脚本求值期 `initLearnView` 恢复上次视图时调用 `showLearnView` → `sseAbortAll()` 触发「Cannot access '_sseAbort' before initialization」ReferenceError，learn.js 后续全部代码不执行（子导航/讲解/提问/大纲管理全挂）。修复：声明前移到文件头（`medkit/web/js/learn.js`）。回归：`test_learn_view_remembered_after_reload`（CI 首曝光）。
- **`sseStopUI` 先清后挂顺序错**：`expGenerate`/`tutorStart` 先 `_sseAbort = abort` 再调 `sseStopUI`（内部先 `sseAbortAll()`）——新 controller 被立即 abort，fetch 未发出即「已停止生成（未保存）」、流式永不建立（测试模拟 SSE 全量帧时 `exp_live` 无增量）。修复：先 `sseStopUI` 清上一处残留，再挂新 controller（两处）。回归：`test_explain_stream_live_display`。
- 两项均为基础层（learn.js）缺陷，整个浏览器层 34 用例重跑全绿。
- **测试文件重名冲突**：`tests/browser/test_syllabus_manage.py` 与 `tests/test_syllabus_manage.py` 同名——pytest 全量收集（`verify.cmd`/CI verify job）报 `import file mismatch` 收集错误。浏览器层重命名为 `test_syllabus_manage_ui.py`，验证口径恢复「全量收集无冲突」。

### R4 批次 3（打磨 · 2026-08-31）

#### Added

- **R4-24 一键清同名卡**：错题本「已掌握」行新增「清同名卡」显式入口（带确认，不静默删）——`POST /api/library/review/purge-same {subject,kp_name}` 移出同名复习卡/记忆卡并返回计数；`core/review.py`/`core/cards.py` 新增 `delete_by_kp`（分区口径：subject 精确匹配，空 subject 匹配未分类卡）。
- **R4-20 考前提醒真触达**：开始页考试计划进入「考前 N 天」窗口时卡片醒目提示「📌 考前 X 天 · 已进入「N 天」备考冲刺提醒」（`app.js examRemindInfo` + `.exam-remind.hot`）——`remind_days` 数据不再无任何触发。
- **R4-21 全选范围明示**：错题本「全选」按钮按实际选中范围显示「全选全部 / 全选已加载（100/共 N）」，未加载全部时不再误导。
- 测试：`tests/test_r4_batch3.py`（R4-09/13/14/15/16/17/18/24 后端侧 10 用例）+ 浏览器 `test_start_exam_reminder_window_active` / `test_mistakes_batch` 全选标签断言。

#### Fixed

- **R4-09 子步骤终态缺失**：在飞子步骤登记表（`_SUBSTEP_INFLIGHT`）；`_cancel_out`/`run_project` 异常出口统一补 `cancelled`/`failed` 终态——不再出现「子步骤面板永久运行中」（K8S Job 式状态机口径）。前端 `renderSubsteps` 支持 `cancelled` 图标/文案/样式。
- **R4-10 超时僵尸线程共享污染**：门禁① MedFix / MedQC / 质检 MedFix 三处输入改 `copy.deepcopy(questions)` 快照——超时放弃后僵尸 daemon 线程只能污染副本，不再与主流程共享 `questions`（继续烧 token 是已发出的请求，属已知边界）。
- **R4-13 图片导入无上限**：`/api/library/mistakes/import-image` 读后即判 `MAX_FILE_SIZE`（200MB）→ 400，不落盘/不进 OCR（与 `ocr_start` 口径一致）。
- **R4-14 meta 非 dict 500**：`_read_meta_checked` 增加 `isinstance(data, dict)`——`[]`/字符串等解析成功但类型异常 → 422（不再调用方 `.get` 崩 500）。
- **R4-15 异常串回显**：`/api/llm/models` 复用 `LLMClient._test_error_hint` 归一化——失败响应不再回显可能含 base_url/响应片段的原始异常串（与 `llm_test` 同口径）。
- **R4-16 未分类卡重复计数**：`rev.list_cards(subject)`/`cards.list_cards(subject)` 指定科目时不再混入 `subject=""` 未分类卡，`subjects()` 单科统计去除 `+ cards_by.get("",[])`——分区互斥，「未分类卡不重复计入每科」。
- **R4-17 记忆卡无 LLM 去重**：`cards_generate` 复用 R3-21 在飞去重（`cards:generate:{eid}`）——连点/双标签不再重复调用生成 LLM（双倍扣费），在飞期间第二次 409。
- **R4-18 短 Key 掩码泄露**：`mask_api_key` 对 `len<12` 一律只露前 2 后 2（旧逻辑 9~11 位只藏 1~3 位，中段几乎全露）。
- **R4-19 自评失败卡消失**：复习卡/记忆卡三按钮自评失败时（卡片已被出卡动效移除）→ `loadReviewCtx` 重渲恢复，不再「本次会话卡片消失且从未判分」。
- **R4-22 批处理连点竞态**：批删除/批已掌握/批导出在途互斥（`mkBatchBusy` + 工具栏按钮禁用），连点不再重复发请求。
- **R4-23 作用域标签错位**：切科目先 `renderLibraryCurrent()` 用缓存即时重渲染列表，再异步刷新概览——不再「标签已切、列表还是旧态」。
- **R4-25 delPreset 撇号击穿**：预设删除入口从行内 `onclick`（id 含 `'` 会击穿 JS）改为事件绑定 + DOM 挂载（`review-desk.js renderChips`）。
- **R4-26 code 内嵌套高亮**：`md.js` 行内渲染先提取保护 `` `代码` `` 段，关键词高亮/粗斜体不再嵌套进 `<code>`（代码内容只转义，XSS 安全不变）。
- **R4-11 冗余写已消除**：structurize 原文重复写为 R4-05 重构时顺手消除（当前实现仅一次 `write_text` + 双存储回读，经查证无需再次修改）。

## [0.9.0] - 2026-08-29

### R3 全链路 UX 审查修复（2026-08-29，批次0/1/2 合流：数据正确性 → 链路闭环 → 打磨）

#### Added

- **进程内幂等去重**（`medkit/core/dedupe.py`）：`begin/end` 在飞去重 + `try_acquire` per-subject 并发上限——讲解/提问/试出/建课题防双击与双标签重复生成、双倍扣费；`client_token` 幂等键保证建课题双击不重复建项目。
- **押题卷状态模型重构**：作答/旗标按题目 id + 卷面指纹（FP）存储，跨版本串题一次性失效提示；判分 judged 持久化、重开计时冻结（不再按首开时间戳累计超大读数、限时不再误触发）；X 型取消勾选后答案删除不再虚高；WRONG_POOL 携带 id/sid/case_stem/image_ref/data_table 回流错题本（后端按 question_id 去重）。
- **题库「打印全库」**：`@media print` 分页全显、筛选/翻页控件隐藏、答案展开；押题卷 noscript 静态兜底。
- **Anki .apkg 稳定 guid**（qbank 按 pid+题 id、记忆卡按卡 id）：同项目重导不重复新增。
- **OCR 任务持久化**：jobs.json 落盘、重启恢复、孤儿 tmp 清理；上传（300s）与轮询（60s）独立超时；上传前取消检查。
- **subject 安全文件名**（`fsutil.safe_filename`）：.apkg/记忆卡导出等四处落点统一，非法字符不再写盘失败。
- **手册教材侧摘要预算轮转**：6000 字预算按切片均摊，后段章节也进手册；B1 选项组按 group.id 分组、保持章节原序；案例子题图题渲染；手册相对路径图片占位提示。
- **学习中心**：错题本科目作用域指示 + 可切换下拉；教师重点文件「草稿→确认入库」两段式（preview 参数）；真题考频重复确认累加；真题标注短条目词边界匹配；近期活动补「入库错题/提问开始」事件。
- **反馈面**：成本预估失败显示「预估不可用（点击重试）」；试出结果范围说明条（不含网络/大纲/图片素材）；Anki 导出计数与错误反馈；tutor 24 轮上限前置提示（不再静默计分扣费）。

#### Changed

- **token 记账**：线程池提交用 `contextvars.copy_context().run` 包装——切片出题与 QC 质检不再漏记；取消/失败出口同样快照 usage。
- **取消全链路**：停止下透到 LLM 流式层（提前退出）；QC/修复/汇总/渲染各阶段 checkpoint；前端「正在取消中…」态。
- **审核台**：per-pid 锁互斥保存/重掷/重渲染；渲染失败回滚全部产物字节快照；答案归一化第三口径（B,D → BD）；只校验本批实际改动字段；B1 组「共享选项」单一入口；重掷剥离孤立案例字段并提示。
- **筛选**：题库 og/case 筛选命中独立 B1/A3/A4 单题；真题年份筛选。
- **性能**：syllabus 覆盖判定单遍加载、explain 索引按 mtime 缓存、错题列表分块渲染、subjects 单遍聚合。
- **设置壳层**：检索设置补 base_url 校验；切服务商不再覆盖手填模型名；连接测试中文报错 + 短超时；版本号窄屏可见；ESC 关确认框触发 onCancel；创建课题成功后清空表单与解析结果。

#### Fixed

- 新建/未跑完项目打开详情整页崩溃（usage 判空，R3S-01）；审核台重渲染后图题丢图（重建 image_index + 缺图占位，R3S-02）。
- 3 选项题答案 D 放行（按实际选项数校验，R3-06）；「全部剔除→保存」静默保留全部（keep=[] 明确拒绝 + 前端拦截，C-10/R3-14）。
- 限时到点未答完确认弹窗死循环（C-08）；ART_LABEL 三元组下标错位致产物网格视觉损坏（C-06）。
- 知识点含撇号击穿 onclick 按钮（data-* 重构，R3-02）；真题粘贴逐条跳过索引错位（R3-04）；大纲科目下拉滞后与概览刷新不回填（R3-26/D-08/D-28）。
- 快捷键误评未翻面卡（仅翻面不评分，D-10）；判分 retry 清空作答（保留回填，D-26）；「今日进度」跨天凭空显示（D-11）。
- 刷题自评双击双排期、复习卡评分不回写掌握度、无 Key 载入示例遮罩不关、切片跨页章节标题退化、QC payload 缺图/案例字段等 P1 群。
- UTF-8 BOM 导入静默过滤与官方 JSON 结构 422（D-06）；文本导入多字母答案截断（D-16）。
- README「六视图/Alt+1..6」按 v0.8.1 后 IA 重写（D-29）。
### 批次F（2026-08-29，RAG 无原文回退：说明 + 网络 + 模型知识输出）

#### Added

- **讲解无原文回退**：`/api/library/explain` 切片未命中时，注入「说明文案 + 网络补充素材（如有）」并要求模型结合医学知识输出完整讲解（不再要求「仅通用梳理、请补教材」）；产物新增 `grounded` 字段（False = 未命中教材原文），前端讲解卡片/生成结果展示「无教材原文 · 网络+模型知识」标签与说明。
- **提问无原文回退**：`/api/library/tutor/start|answer` 切片未命中时自动做 ≤1 轮联网补充检索（复用统一后端解析 `_resolve_search_fn`，错误隔离），素材与「未检索到原文」说明一并注入 MedTutor；响应新增 `grounded`/`note`，前端在首问与每轮判分处提示「未命中教材原文，基于网络素材与模型知识」。
- **复习卡「查看提示」无命中回退**：不再只提示「去讲解产物生成」，改为说明 + 一键「结合网络与模型知识生成提示」按钮（成本预估前置，复用讲解端点，产物同时沉淀到复习手册）。
- **讲解产物「查看教材切片原文」无命中回退**：说明内容可能基于网络素材与模型知识（指向「来源」清单），引导上传教材后重新生成。

#### Changed

- `medkit/agents/medexplain.py`：`explain_knowledge()` 返回新增 `grounded`；无切片时插入「先说明、再输出」引导段；`_web_digest()` 支持自定义 header（供 MedTutor 复用）。
- `medkit/agents/medtutor.py`：`start_applying()`/`score_answer()` 新增 `web_materials` 注入（无切片时与说明文案同通道注入）。
- `medkit/routers/library.py`：抽出 `_resolve_search_fn()`（讲解/提问共用）；`_tutor_grounding()` 替代 `_tutor_slices()`；`ExplainDoc` 契约新增 `grounded`。
- 旧产物兼容：无 `grounded` 字段的历史讲解按「纯教材/含 web 补充」旧逻辑展示，不报错。

### Prompts

- `medexplain.md`：无教材切片规约由「明说缺乏切片、仅作通用梳理」改为「**先说明**未检索到原文，再结合网络素材（标【网:】）与医学知识输出完整讲解；数值注明以最新指南为准；不得谎称出自教材」；内容护栏同步（不得把模型知识标注成【教材】）。
- `medtutor.md`：素材说明补充「网络补充素材（无教材切片时）」；引导规约由「切片不足时基于通用医学常识」改为「没有切片时结合网络补充素材（如有）与通用医学常识引导，避免编造具体数值/指南」。
- llm_cases：medexplain/medtutor 无 JSON 契约 fixture（自由 Markdown / 判分 JSON 由 `TutorTurn` 契约与注入文案单元测试覆盖），无需同步样本。

### 差距审查批次E（2026-08-29，真题来源标注全链：决策 4 本期交付）

#### Added

- **考频年份提取**：迁移 v6（`realexam_freq` 增 `year` 列，可空，幂等升级）；真题解析按「段落级年份继承 + 句子级年份覆盖」提取 `(19|20)xx 年`，草稿/确认记录带主导年份。
- **题目来源标注**（PRD 6.3.2 真题标记，零 LLM）：题干/章节命中**已确认**考频条目 → `source_type='真题'` + `source_year=主导年份`（未确认不标注，WP-02 红线）。三处接入：① 生成管线收尾写回 `questions_final.json`（新项目持久化）；② `/api/projects/{pid}/questions` 读取时兜底标注（老项目实时补齐，不写文件）；③ 审核台「保存并重渲染」前补齐。`QuestionItem` 契约新增可选 `source_type/source_year`（缺省空，不破坏旧产物）。
- **来源标签渲染**：题库 HTML（单题/案例/选项组摘要）、押题卷卡面、题库 MD、Anki .txt 前端、审核台题目卡统一显示「20XX 真题」标签（`.tag.src`，pagechrome 单源样式）。
- **年份筛选**：题库页新增「按真题年份过滤」（与题型/Bloom/关键词联动，localStorage 按项目隔离记忆）；审核台新增年份筛选下拉。

#### Changed

- `realexam_freq` 存储列扩展（`_RE_COLS` 含 year）；`test_old_db_autoupgrade_on_first_read` 升级断言改为「首读升级到最新版」。

### 差距审查批次D（2026-08-29，视觉统一：青绿主色 + 字号四级）

#### Changed

- **主色青绿**（PRD 7.2）：`--accent` 由中性灰/黑改为青绿——浅色 `#2A6B5A`（PRD 原值）、深色 `#3aa58c`（提亮保对比度）；主按钮/侧栏激活态/「开始学习」大按钮/焦点边框/科目卡片选中态随之整体换色。
- **正文色**（PRD 7.2）：浅色正文 `#2E3440`（原纯黑）；语义色取 PRD 色相并按 WCAG 加深（浅色 绿 `#4a8f45`≈#59A14F、红 `#d64545`≈#E15759、黄文字 `#a16207`——#EDC948 仅作图形色）。
- **字号四级收敛**（PRD 7.3）：页面标题 21px（已有）/ 卡片标题 16px（原 14px，`.card h2` 与 `.cardh h2`）/ 正文 body 14px（原默认 16px）/ 辅助文字 12px（已有）；按钮文字 14px。
- 验证：明暗双主题计算样式实测（accent/h2/body/hint/ptitle 均达标）+ 浏览器全量回归（含主题切换、窄屏无溢出用例）。

### 差距审查批次C（2026-08-29，卡片化刷题：翻转 + 三按钮 + 进度 + 科目卡片）

#### Added

- **卡片翻转刷题**（PRD 6.4.1）：复习卡（SM-2）与医学记忆卡（FSRS）改造为 3D 翻转卡——正面只显示知识点/卡面（先回忆），点击卡面翻面（`rotateY ≤300ms`）看提示/答案；按钮与折叠控件点击不误触发翻面。
- **底部三按钮**（PRD 6.4.2）：忘了（红 #ef4444）/ 模糊（黄 #f59e0b）/ 记住（绿 #10b981），映射现有档位（决策 3）：复习卡 忘=0·糊=2·记=4（0~5 六档）；记忆卡 忘=重来0·糊=困难2·记=良好3（FSRS 四档）；精确档位折叠在「精确自评」中保留。
- **快捷键 1/2/3**：刷题 tab 下对当前卡（已翻面优先）自评，未翻面自动翻面（输入框聚焦时不触发）。
- **今日进度条**（PRD 3.5）：`X/Y`（X=本次会话已评，Y=进入刷题时的今日到期数）+ 平滑进度动画；切换科目自动重置基数。
- **科目卡片**（PRD 3.6）：`/api/library/subjects` 新增每科 `stats`（错题数/知识点数/掌握率/复习总卡/今日到期，全本地零 LLM）→ 刷题页科目卡片网格（含「全部科目」），点击按科过滤。
- **解析关键词高亮**（PRD 6.4.1）：首选药/金标准/确诊/禁忌证/一线/不良反应等 12 个医学关键词加粗标红（先转义后替换，安全），应用于错题本解析、复习卡教材提示、记忆卡背面。
- 浏览器用例 `tests/browser/test_study_quiz.py`（铺卡→翻面→三按钮→进度 1/1；快捷键 3 自评，零 LLM）。

#### Changed

- 复习卡/记忆卡自评反馈文案区分三按钮（「已记录「记住」（4/5）…」）与精确档位；出卡增加缩放淡出微动效（PRD 6.4.3 即时反馈）。
- 后端 `test_router_subjects` 扩展断言 stats 字段。

### 差距审查批次B（2026-08-29，信息架构重组：5 Tab + 首页仪表盘）

#### Added

- **5 Tab 信息架构**（PRD 6.1/6.2 桌面形态落地）：侧栏重组为 **开始 / 刷题 / 题库 / 学习中心 / 我的**；默认落地「开始」。Ctrl/⌘+1..5 按新顺序，学习中心子导航 Alt+1..5（复习计划迁出后五视图）。
- **开始仪表盘**：今日待复习 / 新卡待学 / 已完成 / 掌握率 四统计卡 + 巨大「开始学习」主按钮（跳转刷题）+ **考试倒计时**（可设日期，localStorage 持久化，考前 3 天提示加大强度）+ 最近项目（点击直达项目详情）；数据复用 `/api/library/dashboard` + `/api/projects`，零新端点。
- **刷题 tab**：复习计划（SM-2 复习卡 + FSRS 记忆卡）自学习中心迁入 + 科目 chips 快速过滤。
- **题库 tab**：原「新建课题」与「我的项目」合并（创建课题成功后自动打开项目详情）。
- **我的 tab**：连接服务商 / 提示词与规则（含门禁速览）/ 数据管理收纳（P2 功能收口，PRD 6.5）。
- **侧栏待办徽章拆分**：刷题 tab = 今日到期复习数；学习中心 tab = 进行中提问会话数。
- 设计文档 `docs/design/2026-08-29-ia-restructure.md`（功能分级表 + 批次 B~E 范围与验收）。

#### Fixed

- **全局函数重名 renderReview**：学习中心复习卡渲染器与审核台渲染器同名（`2b572d1` 拆分时引入），后者后加载覆盖前者，导致「复习计划」列表静默不渲染（两轮审查与浏览器用例均未覆盖内容层）。更名 `renderSmReview`，hash 直达用例扩展为五 tab 逐一断言内容就绪（防此类回归）。
- 向导 / 试出题 / 创建课题等 8 处旧 tab 跳转（conn/proj/prompts）同步新架构；`showTab` 选择器收窄为 `nav button[data-tab]`（不再误重置学习中心子导航 aria 状态）。

### R2 全链路 UX 审查修复批次二（2026-08-28，P2 打磨全量落地）

#### Added

- **「仅重渲染」单产物**：项目详情新增「仅重渲染：题库 / 押题卷 / 复习手册 / Anki」按钮（后端 `POST /api/projects/{pid}/rerender`，复用审核渲染层、不改题库、无 token 消耗）。
- **近期活动时间线**：学习中心概览新增「近期活动」列表（生成讲解 / 复习打卡 / 提问作答，跨知识点按时间倒序；数据来自既有 knowledge.history，纯只读聚合）。
- **提问会话清理**：提问式学习新增「清理 30 天无活动会话」（后端 `/api/library/tutor/cleanup`，带确认；会话按最近活动排序）。
- **记忆卡面板新增入口**：「🧠 医学记忆卡」面板补「+ 生成记忆卡」直达按钮（跳转讲解视图；空态文案同步修正——FSRS 自动排期，删除「SM-2 可切」误导性说法）。

#### Changed

- **更新检查支持预览版**：版本比较纳入 alpha/beta/rc/dev 后缀（`0.8.0-rc.1` 不再被折叠成 `0.8.0`）；正式版 > 任何预览版，预览版之间按 dev<alpha<beta<rc 排序；「新预览版」在弹窗/提醒中单独标注（后端返回 `prerelease`）。
- **设置链完善**：接口地址（base_url）提交/测试/获取模型前客户端预校验（http/https，标红提示）；切换服务商时若手改过接口地址先确认再覆盖；主题未手动选择时跟随系统亮/暗切换（matchMedia）；外链（题库与手册站/官网注册/更新页）离线时拦截并提示；「写邮件」后附「未弹出客户端请复制手动发送」兜底提示。
- **建课体验**：配比/Bloom 合计实时显示「还差 X%（或超出请调低）」；「开始生成」与「解析」请求期间禁用按钮（防双击 409/重复解析）；项目详情「本次消耗」统一为「万 token」两位小数口径。
- **学习中心一致性**：生成讲解 / 提问判分后概览诊断自动刷新（失效缓存 + 重拉当前科目）；重新生成记忆卡后按「生成卡的讲解科目」刷新复习视图（原按复习过滤，新卡可能被过滤隐藏）；批量导入错题期间按钮禁用并显示「导入中…」；删除错题的确认文案明示「已生成的讲解/会话/复习卡/记忆卡将保留」。
- **产物页**：题库分页改为「按题目数」（每页 ≤50 题；案例/选项组整组归属单页不拆散），页眉注明口径；题库筛选记忆按项目隔离（`medkitQbFilter-{pid}`，跨项目不再串台）；押题卷无 pid（手工导出）时 localKey 按「路径+标题」指纹避免跨项目同名文件串卷；押题卷页顶明示「答案为页面内明文，请勿用于正式考试」；人工复核清单「QcVerdict/score=-1」黑话改写为自然语言。

#### Fixed

- 产物页图片：单图 >1.2MB 时用 PyMuPDF 降采样重编码（最长边 ≤1000px、JPEG q82），仍超限则给「回项目查看原图」占位提示（不再把数 MB 图整张塞进 HTML）；所有产物图 `loading="lazy"` 延迟解码。

### R2 全链路 UX 审查修复批次（2026-08-28，含 P0 数据正确性命门）

#### Added

- **审核台批量操作增强**：全选当前筛选可见项 / 反选按钮；「全部剔除」在启用筛选时只作用于筛选结果（不再误删被隐藏的题）。
- **审核台行内编辑增强**：补题型（A1/A2/A3/A4/B1/X 下拉）与章节/知识点（subtopic）输入；答案键按题型即时校验（X 型≥2 字母、单选单字母、字母范围检查），未通过禁止保存——前后端同口径校验。
- **记忆卡 Anki 导出入口**：学习中心「医学记忆卡」面板新增「导出 Anki（.apkg）」「导出 .txt」「导入指引」（后端新增 `/api/library/cards/export/{apkg,txt}`，现场生成独立「MedKit 记忆卡」牌组）。
- **押题卷防漂移**：新增 `paper_ids.json` 记录抽样结果，审核台「保存并重渲染」复用原抽样（题库变化导致不足时才重抽补足）——只改一题解析不再让押题卷 50 题「换一批」。
- **押题卷案例组原子抽样**：`_sample_paper` 以 case_id 为原子（组内子题同进同出），案例题干上下文不丢；渲染侧 casebar 按 case_id 首次出现渲染（拆散兜底）。
- **概览科目口径统一**：`/api/library/mastery`、`/api/library/recommend` 支持 `subject=` 过滤；概览顶部诊断/推荐/错题列表与「学习闭环总览」同科目范围。
- **提示词页补全 8 个**：`PROMPT_ROLES` 补 `medcards.md`（记忆卡）与 `syllabus_extract.md`（官方大纲抽取）——「查看全部提示词」名实相符。

#### Changed

- **审核台数据正确性**：单题重掷对 A3/A4 案例组 / B1 选项组 / 图（image_ref）/表（data_table）题禁止并引导行内编辑（前后端双重拦截）；`.apkg` B1 卡改用共享选项 `_effective_options`（与 anki_export.txt 同口径，补 B1 用例）；Anki A3/A4 标签改「案例单选」；Anki 图/表题卡面加「请回题库.html 查看原图/表格」占位提示。
- **押题卷体验**：计时器在「清空重做/重新作答/错题重练」后重置（限时模式不再被旧时长误触发）；下载到本地（file://）打开时判分同步给出明确离线提示（不弹「同步失败」）；缺选项题剔除时列出题号明细；页顶常驻「判分自动同步（在 MedKit 内打开时）」说明。
- **学习中心体验**：讲解 Markdown 渲染补 GFM 表格与围栏代码块（医学对比表/数值表可读）；讲解删除级联清理其派生的医学记忆卡（`delete_by_source`）；复习卡移出队列/记忆卡删除补确认弹窗；记忆卡自评图例与复习卡 0~5 档对照说明；薄弱组卷「全部科目」自动选科时明确提示所选科目；取消组卷确认后提示「已创建未运行项目」；大纲标准（教师重点/官方大纲）选择持久化；错题本新增「只看未掌握」筛选；切片原文「查看提示/讲解来源」支持展开全文。
- **其它**：提示词页补全；项目详情「产物：✓」改为「产物开关：」明确语义；Anki 导出按钮按「产物文件存在」判断（与后端一致）；配额标题截断加省略号；成本预估/就绪检查随题数/配比/Bloom/检索配额变化防抖刷新；试出一题携带 Bloom 配比（与正式生成同口径）；提示词页 X 型 ≤10% 与 A3/A4 配比口径明示；「开始生成」在 error/cancelled 后统一为「重试生成（从断点）」且重试前提示重跑范围与费用；生成失败/停止的 stepper 新增「网络检索」阶段、汇总阶段补进度写回。
- **复习手册消毒器**：href 白名单放行页内锚点与同源相对路径（仍拒 javascript:/data:/协议相对）；`img` 标签放行（http/https/相对 src + 强制 alt）；脚本块内的自闭合标签不再泄漏（加固）。
- **答案归一化口径统一**：门禁 R0 与渲染层 answersEqual 同用「去除空白/逗号/顿号」归一化（边缘「B, D」不再被判失败触发 MedFix）。
- **样式**：toasts z-index 提升到 300（弹窗/向导打开时错误提示可见）；主题初始化前置防首屏闪烁。

#### Fixed

- 素材上传：拖拽/选择时按白名单逐文件校验类型（对齐后端 TEXT_SUFFIXES，含 .bmp），单个 >200MB 文件跳过不阻断整组解析。
- 切片预览改为全部切片展示（原仅前 12 条）。
- 真题考频/大纲报告等按钮补错误兜底（失败 toast，不再静默）；文件草稿支持逐条跳过（与粘贴分析一致）。
- 提示词查看/编辑缺 medcards/syllabus_extract（修复为 8 个，见 Changed）。
- 讲解/提问失败时提问判分不再渲染「-1/3」负数（retry 分支显示「请围绕考点再答一次」）。

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

[0.9.0]: https://github.com/2710074390-cyber/medkit/releases/tag/v0.9.0
[0.8.0]: https://github.com/2710074390-cyber/medkit/releases/tag/v0.8.0
