# MedKit 专属审查提示词（总任务书）

> 审查对象：**MedKit · 医学题库工坊**（MedAgentWork 桌面版）
> 基线版本：v0.10.3（仓库 `C:\Users\38063\Desktop\medkit`，HEAD）
> 定位：面向即将考试的医学生，本地一键生成题库 / 押题卷 / 复习手册 / Anki 卡包的备考软件
> 运行形态：FastAPI 本地回环服务（127.0.0.1:4880~4889）+ 零构建原生前端，PyInstaller 打包桌面应用
> 许可：AGPL-3.0（`LICENSE`），第三方许可见 `THIRD_PARTY_NOTICES.md`
> 数据面：全部在本机 `~/.medkit/`（项目 / 素材会话 / 提示词影子副本 / 日志 / SQLite `medkit.db`）；BYOK 自选 LLM 服务商，Key 落盘 DPAPI 加密

---

## 〇、使用说明

本提示词是**派发给审查 Agent 的总任务书**，按「5 路并行 + 1 路汇总」组织。可直接将对应章节裁剪成每个 Agent 的独立任务提示词；汇总 Agent 持本全文收口。

**执行前提（先读后审）**：
1. `README.md`、`docs/AGENT_HANDOFF.md`（交接与历史踩坑）、`docs/adr/`（重点 ADR-001/003/005/006/007）、`docs/engineering/borrow-rules.md`
2. `docs/工程审查改进指南_2026-09-15.md`、`docs/验收与改进报告_2026-09-16_R7.md`、`docs/reviews/` 历史审查报告——**避免把已修复/已验收项当作新发现重复上报**
3. 审查基线是当前 HEAD（v0.10.3）；结论一律引用 HEAD 的 `file:line`，不得引用旧产物（`build-old-*` / `dist-old-*`）

---

## 一、审查目标与运行方式

**总目标**：以「医学生考前冲刺场景」为第一视角，找出会让备考学生**学错内容、丢数据、泄露隐私、被恶意网页烧掉 API Key 费用**以及**法律合规风险**的问题，产出可复现、可修复的缺陷清单。

**分路**（每路独立闭环：范围确认 → 采集 → 分析 → 验证 → 问题清单；共享只读代码库，互不等待）：

| 路 | 名称 | 核心职责 | 关键代码面 |
|---|---|---|---|
| 路 1 | 前端与产物渲染安全 | 页面与产物 HTML 的注入面、押题卷交互逻辑、前端安全 | `medkit/web/`、`medkit/render/`、`medkit/core/cards.py` |
| 路 2 | 服务端与业务逻辑 | 本地服务边界、API 与管线、付费滥用面、并发状态 | `medkit/main.py`、`medkit/routers/*`、`core/orchestrator.py`、`core/llm.py`、`core/websearch.py`、`core/mineru.py` |
| 路 3 | 医学内容质量与合规（领域专属，最高价值） | 生成内容正确性、门禁有效性、溯源、版权/真题/考试伦理 | `medkit/prompts/*`、`medkit/agents/*`、`medkit/gates/*`、`core/schema.py` |
| 路 4 | 数据、隐私与密钥 | API Key 全生命周期、本地数据面、PIPL 合规、日志脱敏、可靠性 | `core/config.py`、`core/db.py`、`core/sessions.py`、`core/usage.py`、`logging_setup.py`、`routers/data.py`、`routers/ocr.py` |
| 路 5 | 供应链、构建与发布 | 依赖漏洞、打包纯净性、CI/CD、AGPL 合规、更新链路 | `requirements*.txt`、`package*.json`、`medkit.spec`、`medkit.iss`、`pack/*`、`.github/workflows/ci.yml`、`LICENSE` |
| 汇总 | 整合收口（不并行） | 去重、攻击链串联、覆盖度核对、风险定级、总报告 | 全部路的问题清单 |

---

## 二、通用规则（所有路强制）

### 2.1 只读与安全边界
- **只读审查**：不修改任何源码 / 配置 / 数据文件；发现疑似缺陷只记录，不"顺手修复"。
- **禁止**：调用真实 LLM / OCR / 联网检索 API（会产生用户费用）；运行 `run_project`、`parse`、`ocr_start` 等消费性管线；向真实 `~/.medkit/` 写入任何数据。
- 允许的验证动作：离线 pytest（`--ignore=tests/browser`）、`ruff check .`、`npm run lint`、`python pack/check-package.py`（只读断言）、静态代码走读、`git log/diff` 取证、用临时目录 + mock 的纯本地单测。
- 若确需启动服务观察行为，使用 `MEDKIT_PORT=4999 MEDKIT_NO_BROWSER=1` 且不得使用真实 HOME（可用临时环境变量指向空目录后再删除）。

### 2.2 证据规范
- 每条发现必须给出：`文件:行号` + 关键代码/调用链 + 最小复现或推理步骤。
- 明确区分「已验证可利用」与「理论风险」；两者都允许上报，但必须标注。
- 外部事实（法规、CVE、官方文档）须给出来源链接；**不得用搜索结果代替代码事实**。
- 同一问题跨多处出现时，报告所有位置并给主入口。

### 2.3 严重性分级
- **S0 阻断**：用户 API Key / 素材 / 产物被窃取或恶意耗尽；隐私数据非预期外传；错误医学内容系统性流出（可能误导备考）。
- **S1 高**：特定输入可稳定利用（XSS / 注入 / 路径穿越 / 越权读取产物）；内容质量门禁可系统性绕过；关键数据可丢失。
- **S2 中**：加固不足、边界未覆盖、竞态/可靠性风险、合规缺失。
- **S3 低**：体验、文案、文档、规范性问题。

### 2.4 问题条目模板（每路输出 `问题清单_<路名>.md`）
```
| 编号 | 标题 | 路径(file:line) | 证据/复现 | 影响 | 严重性 | 修复建议 | 状态(已验证/理论) |
```
编号规则：`M<路号>-<序号>`，如 `M3-07`。

---

## 三、路 1 · 前端与产物渲染安全

**必读**：`medkit/web/index.html`、`medkit/web/js/*`（含 V-15 分片 learn/learn-study/learn-live/learn-review、review-desk 系）、`medkit/render/qbank_html.py`（59KB，题库 HTML）、`medkit/render/review_html.py`（复习手册）、`medkit/render/pagechrome.py`（三套产物主题单源）、`medkit/render/apkg.py`、`tests/browser/*`、`tests/test_v15_frontend_split.py`、`tests/test_render_markdown.py`。

**检查清单**：
1. **产物 HTML 注入面（S0/S1 重点）**：题库 / 押题卷 / 复习手册渲染是否对题干、选项、解析、溯源（`[源:切片]` / `[源:网 URL]`）、章节名、教师重点条目等**全部用户/LLM 可控字段**做转义；复习手册白名单消毒（href 仅 http/https）的绕过路径——`data:`、`javascript:`（含大小写/实体编码/`&#x6a;`）、`vbscript:`、协议相对 `//`、`<img src=x onerror=...>`、SVG `onload`、事件属性、Markdown 链接/图片注入、表格单元格注入；`md.js`「先转义再解析」的顺序是否可被构造输入破坏。
2. **押题卷交互与本地存储**：判分 / 答题卡 / 续答（localStorage）逻辑——localStorage 数据是否被信任并渲染进 DOM（构造恶意 localStorage → XSS 链）；X 型集合判分是否存在篡改得分的绕过；错题重练写学习中心接口是否校验来源。
3. **前端鉴权与成本面**：token / Key 在前端的存在形态与掩码（前2后2）是否所有回显点一致；成本预估（前端粗估「参考价」）是否可被脚本篡改误导用户；`api()` 封装与自定义端点 `base_url` 客户端校验是否可绕过（指向内网 / 非法 scheme）。
4. **经典脚本全局作用域**：V-15 分片加载顺序契约与 `/* exported */` 声明；`window.X` / `global.X` 挂载是否存在 **DOM clobbering**（第三方注入或产物 HTML 可覆盖全局函数）。
5. **CSP 与安全头**：本地服务响应与产物 HTML 是否设置 CSP / 安全头；零 CDN 约束下内联脚本无法被 CSP 豁免时的实际风险面。
6. **下载与导出面**：`project_file` / `export_anki` 的下载路径与文件名处理；Anki `.apkg` 生成（zip 结构）是否存在 zip slip / 解压炸弹；产物单文件内嵌 base64 图片（WP-04 `<figure>`）的尺寸上限是否失控（600MB 资产上限的产物侧影响）。

**输出物**：`问题清单_前端与产物渲染.md` + 一张「用户可控字段 → 渲染出口 → 消毒手段」对照表（覆盖度一眼可见）。

---

## 四、路 2 · 服务端与业务逻辑

**必读**：`medkit/main.py`（Host/Origin 守卫、异常兜底）、`medkit/routers/*`（config/parse/projects/pipeline/prompts/presets/search/review/ocr/data/gap/library/realexams/syllabus/update/diagnostics）、`medkit/core/orchestrator.py`（76KB 五阶段管线）、`core/llm.py`、`core/websearch.py`、`core/mineru.py`、`core/cost.py`、`core/quota.py`、`core/providers.py`、`core/sessions.py`、`core/state.py`、`tests/`（API 层与管线层）。

**检查清单**：
1. **本地服务边界（S0 重点）**：`main.py` Host 守卫的绕过面——Host 头解析（IPv6 `[::1]`、端口、大小写、尾点 `localhost.`、重复 Host、编码变体）；Origin 校验仅对非 GET/HEAD/OPTIONS 生效：**枚举全部 GET 端点并逐一判断副作用**（`/api/update/check` 外呼 GitHub、`/api/projects/.../file` 产物下载、`export_anki`、`sample_materials`、`diagnostics`、`/api/health`），评估跨站简单请求（`<img>`/`<script>`/表单）可触发哪些；`_allowed_origins` 随端口变化是否覆盖全部回退端口（4881~4889）；DNS rebinding 缓解是否完备。
2. **付费 API 滥用面（S0 重点，烧钱）**：`trial` / `run_project` / `regen_question` / `explain` / `tutor` / `gap` / `search` 端点是否有频率/并发/配额限制；跨站或恶意脚本能否反复触发消耗用户 Key 余额；`dedupe.begin/end` 去重锁是否覆盖流式取消 / 断连（GeneratorExit）路径（防双扣费）；断流是否错误回退到非流式导致双计费；cost estimate 与实际 usage 记账口径（`core/cost.py` 单源公式）是否一致。
3. **文件上传与解析**：`parse`（PDF/DOCX/MD/TXT/图片）、`import-image`（200MB）、assets（600MB 上限）、教师重点文件（20MB）、错题导入（JSON/CSV/MD/TXT）——文件类型魔数校验、解压炸弹、图片解码 DoS、文件名/`_safe_pid` 路径消毒、临时文件清理；MinerU OCR 任务（≤200MB/≤600 页、jobs.json 恢复、孤儿 tmp 清理、取消路径）。
4. **注入与输出**：SQLite / FTS5 查询参数化；无命令执行面确认（subprocess / eval 检索）；错误响应 `errs.redact` 脱敏的绕过（异常消息含 Key / 路径 / 教材片段时）；日志注入（换行 / 控制字符 / 伪造条目）。
5. **业务逻辑**：syllabus 覆盖判定（零 LLM 边界是否被破坏）、realexams 考频人工确认门、gap 配题 24h 幂等、presets 导出/导入 JSON 校验边界、prompts 编辑器（占位符校验、base_hash 漂移、影子副本路径是否可穿越）、`meta.json` 解析（非 dict → 422）容错；项目删除是否级联清理（assets/sessions/exports/OCR tmp）。
6. **并发与状态**：`state.py` 运行锁、三线程管线、断点续跑 checkpoint、取消路径（`cancel_ev` 上传 client + gen finally 置位）、SQLite WAL 并发读写、`write_json_atomic` 原子写——重点找竞态窗口（两个端点同时操作同一项目 / 同一错题）。

**输出物**：`问题清单_服务端与业务逻辑.md` + API 面清单（端点 × 方法 × 副作用 × 防护现状）。

---

## 五、路 3 · 医学内容质量与合规（领域专属，最高价值）

**必读**：`medkit/prompts/*.md`（medgen/medqc/medfix/medreview/medexplain/medtutor/medcards/syllabus_extract 共 8 个）、`medkit/agents/*`、`medkit/gates/*`（options/bloom/trace/dedup）、`medkit/core/schema.py`（契约与门禁）、`core/orchestrator.py` 中门禁调用段、`tests/fixtures/llm_cases/`、`docs/adr/ADR-003`、`docs/engineering/borrow-rules.md`。

> 本路以「医学生拿着生成产物备考」为验收视角：**内容错了 = 学生学错 = 最高后果**。

**检查清单**：
1. **出题正确性防线**：medgen.md 是否硬性要求「以教材为唯一事实源、禁止臆造数值/剂量/参考值、不确定即不出题」；HC 命题规则；A3/A4 案例组、B1 真组题（共享选项组）的题目-选项-答案一致性约束；模板占位符一次性替换（防教材文本二次注入）是否真实生效。
2. **门禁有效性（不是存在性，S0/S1 重点）**：用**反例设计**验证——构造「选项重复 / 无唯一答案 / 答案与题干矛盾 / 溯源缺失 / 与教材数值冲突」的坏题，判断 `options_check`（R 规则子集）、`bloom_check`、`trace_check`、`dedup_check`（Jaccard>0.8）能否拦截；`medqc` 的 LLM-as-judge 与出题同模型——评分标准是否可能自洽放行（judge prompt 的评分锚点、gate_decision 阈值、None/浮点 score 容错是否被滥用）。
3. **D2 渲染前终检与人工复核闭环**：修复轮用尽仍超限 / 缺字段的题是否**真被剔除出产物**而非带病渲染；`人工复核清单.md` 是否完整记录（题号 / 原因 / 处置）；**验证「网络检索 conflict 条目绝不自动改写、只进人工复核清单」这一声明**（websearch → 管线 → MedFix 链路上是否存在自动采纳路径）。
4. **溯源与防幻觉**：每题是否硬性携带 `[源:切片]`（slice 编号与教材切片实际匹配）；Web 检索引用（`[源:网 URL]`）配额 0~30% 默认 0 是否被绕过；切片与题目知识点错位风险。
5. **医学高危内容（S0 重点）**：数值速查 / 临床路径 / 药物剂量 / 参考值类内容（复习手册、讲解、提问、记忆卡）生成指令是否要求复核与标注「以教材/指南为准」；medexplain / medtutor 是否可能输出**直接诊疗建议**（如「你应该服用X」）而无免责声明；产品是否有医学免责声明（首启向导「体检警告」等）——缺失即发现。
6. **版权与来源合规（S1）**：产物是否可能**逐字复制**用户上传的教材 / 教师重点 / 真题原文（n-gram 查重是否只查内部去重、不查与源教材的重复）；`realexams` 真题考频「不展示真题原文」声明是否属实（含前端展示、导出、讲解注入面）；押题卷 / 题库是否标注「AI 生成、非官方真题」；上传教材/真题的版权边界在 UI 与文档中是否有说明；AGPL 再分发义务（`LICENSE`、`THIRD_PARTY_NOTICES.md`）是否完整。
7. **考试伦理与误导**：押题卷文案 /「押题」表述是否可能误导为官方押题；「答案明文声明」「参考价以官网为准」等声明是否存在相反实现。
8. **提示词治理（NX-06）**：prompts/*.md 与 `tests/fixtures/llm_cases/` 契约样本是否同步（改 prompt 未改样本 = 契约漂移）；占位符校验是否覆盖全部内置提示词；影子副本「恢复默认」是否可靠。

**输出物**：`问题清单_医学内容与合规.md` + 一张「门禁输入 → 规则 → 反例验证结果」表（每条门禁标注：可拦截 / 可绕过 / 未覆盖）。

---

## 六、路 4 · 数据、隐私与密钥

**必读**：`medkit/core/config.py`（DPAPI 加密）、`core/db.py`（21KB，SQLite + WAL + 迁移 + 备份）、`core/library.py`（61KB 学习库）、`core/sessions.py`、`core/usage.py`、`core/errors.py`、`logging_setup.py`、`routers/data.py`、`routers/ocr.py`、`docs/adr/ADR-001/005/006`、`.gitignore`、`pack/recover-user-data.py`。

**检查清单**：
1. **API Key 全生命周期（S0 重点）**：DPAPI 加密实现（`ctypes` 调用 CryptProtectData 的参数、失败处理、旧明文自动升级路径）——明文是否在任何中间态落盘；Key 出现在日志 / 错误响应 / 诊断接口 / 前端回显的全部出口（掩码前2后2 是否一致）；Key 是否进入 URL / query（fetch 调用面核对）；配置深拷贝防默认值污染是否防住 Key 泄漏；多服务商 Key 存档的删除 / 切换路径。
2. **本地数据面**：`~/.medkit/` 目录与文件权限（Windows 下其他账户 / 备份工具可读性）；JSON 与 SQLite 双轨一致性（`import_from_json` 幂等、`*.pre-db-import-*.bak` 备份、`user_version` 迁移与升级前备份回滚）；FTS5 索引与 jieba 词典数据完整性。
3. **隐私合规（PIPL 视角）**：处理哪些个人信息（学习记录 / 错题 / 掌握度 / 上传教材与真题 / 网络检索素材）——**是否存在任何隐式外传**（更新检查请求 GitHub 的请求头与参数、mailto 反馈附带的系统信息、产物 HTML 中的外链引用）；OCR 云端上传（MinerU）的明示、最小化、可取消；数据删除完整性（项目 / 科目删除是否清理 assets、OCR tmp、exports 备份、session）；数据导出（可携带性）可用性。
4. **日志与脱敏**：`~/.medkit/logs/medkit.log` 内容抽样——是否含 API Key、教材全文、真题原文、个人标识；RotatingFileHandler 滚动策略；`errs.redact` 实现与绕过（异常链中嵌套 Key 时）。
5. **原子性与崩溃恢复**：`write_json_atomic`、SQLite WAL、并发 100/100 无丢失声明（tests）的验证路径——进程被杀（kill -9）后文件一致性、损坏 `meta.json`（422）容错、断点续跑 checkpoint 的持久化点。

**输出物**：`问题清单_数据隐私与密钥.md` + 一张「敏感数据 → 存储位置 → 加密/脱敏 → 外传出口」清单。

---

## 七、路 5 · 供应链、构建与发布

**必读**：`requirements.txt` / `requirements-dev.txt` / `requirements.lock`、`package.json` / `package-lock.json` / `eslint.config.js`、`medkit.spec`、`medkit.iss`、`pack/*`（build.bat、check-package.py、recover-user-data.py、make_icon.py）、`pyproject.toml`、`.github/workflows/ci.yml`、`.github/dependabot.yml`、`LICENSE`、`THIRD_PARTY_NOTICES.md`、`medkit/__init__.py`、`CHANGELOG.md`（版本与 Prompts 小节）。

**检查清单**：
1. **依赖漏洞**：Python 依赖（重点 FastAPI / uvicorn / pydantic / jieba / py-fsrs / requests）与 npm 依赖的已知 CVE；`requirements.lock` 是否真锁定可复现；PyInstaller 打包内嵌依赖版本可溯源性。
2. **打包与纯净性**：`pack/check-package.py` 断言（无样例 / 种子 / 测试 / 字节码）覆盖哪些路径、能否被绕过（`data/`、`docs/`、`tests/` 混入产物）；`medkit.iss` 安装脚本的目录与权限设置；版本单源（`__init__.py → build.bat → version.iss`）一致性。
3. **CI/CD 安全**：`ci.yml` 中 GitHub Actions 版本是否 pin 完整 SHA、secrets 是否在 PR 触发工作流中暴露、上传 artifact 是否含敏感文件（.coverage、日志、Key 掩码样本）；`dependabot.yml` 配置。
4. **许可合规**：AGPL-3.0 完整性；`THIRD_PARTY_NOTICES.md` 是否覆盖全部运行时第三方（py-fsrs、jieba、MinerU 通道、pyinstaller 及内嵌）；ADR-007 决策是否与 `LICENSE` 一致；与在线站（med-review-site）的同源同规格声明是否合规。
5. **更新与反馈链路**：`core/update.py` 版本比较（pre-release 后缀处理）、GitHub Releases 下载 URL 构造（供应链误导 / 重定向风险）；邮件反馈 `mailto:` 附带的版本/系统信息是否过度。

**输出物**：`问题清单_供应链构建发布.md` + SBOM 快照（运行依赖清单 + 版本 + 风险标注）。

---

## 八、汇总整合 Agent 任务书

**输入**：五路问题清单 + 各自对照表。

**职责**：
1. **去重合并**：跨路重复发现统一编号（保留最完整的证据位置），合并到主条目。
2. **攻击链串联**：把单路看不全的跨层链拼成完整攻击路径，例如：
   - 前端产物 XSS → 本地 API 调用 → 读取/删除项目或触发付费管线（烧 Key）
   - 恶意网页跨站触发 GET/表单端点 → 外呼 / 产物下载 / OCR 上传
   - 检索素材注入 → 渲染进产物 HTML → XSS；检索素材注入 → 出题内容污染（医学错误）
   - 上传解析（DOCX/图片）→ 路径穿越/解压炸弹 → 读 `~/.medkit` 或写任意文件
3. **覆盖度核对**：按资产清单逐项打勾——16 个 router、8 个 prompts、4 个 gates、7 个 agents、4 个 render 模块、web 前端分片、pack 脚本、CI 工作流、数据面（db/JSON/日志/导出）——**重点排查分路边界盲区**：WebSocket（无）、后台线程/定时任务、回调接口、静态资源桶、诊断接口、导出文件再导入链路。
4. **风险定级与总报告**：
   - `审查报告_<日期>.md`：执行摘要（S0/S1 计数与一句话结论）→ 按严重性排序的发现明细（含完整证据）→ 按路的修复优先级建议（可执行、可验证、含回归测试建议）→ 复测清单。
   - 风险矩阵表：| 发现 | 触发条件 | 后果 | 可利用性 | 影响面 | 建议优先级 |
5. **与历史对照**：标注每条发现是否与既有报告（R4~R7 / 工程审查改进指南）重叠、是否为新增。

---

## 九、验证命令（只读 / 离线，供各路自检使用）

```powershell
# 代码规范（只读）
ruff check .
npm run lint                          # 前端 ESLint，--max-warnings 0

# 离线测试子集（不触发浏览器；不调用真实 LLM/OCR/检索）
python -m pytest tests -q --ignore=tests/browser

# 纯净包断言（只读）
python pack/check-package.py

# 取证（只读）
git status ; git log --oneline -10
```

> ⚠️ 禁止运行会消费用户费用的动作：`run_project` / `parse`（OCR）/ `ocr_start` / `search_test` / 真实 API Key 下的任何管线。需要验证管线逻辑时，用 `tests/` 中已有的 mock（`tests/fixtures/llm_cases/`）或纯本地构造。

---

## 十、已知加固点（只验证，不重复上报为「新发现」）

以下为本仓库已声明并有多轮测试覆盖的加固措施。审查任务是**验证其真实性与可绕过性**，找到缺陷才上报；把「已存在防护」当成新发现属于无效输出：

- Host/Origin 守卫中间件（含 IPv6 `[::1]`，封 DNS rebinding 与跨站 CSRF）
- API Key DPAPI 加密落盘、旧明文自动升级、Key 不进 URL、错误响应脱敏（`errs.redact`）
- 产物 HTML 全量转义 + 复习手册 href 白名单（仅 http/https）+ `javascript:` 剥离 + md.js 先转义再解析
- 迁移前自动备份、JSON→SQLite 幂等补导（`*.pre-db-import-*.bak` 可回滚）、原子写
- D2 渲染前终检：超限/缺字段题剔除出产物 + 写入人工复核清单
- 网络检索 conflict 条目「绝不自动改写」（只进人工复核清单）
- 真题考频「不展示真题原文」；押题卷「答案明文声明」
- 成本预估前置（创建前显示、参考价标注）；流式断线不自动降级非流式（防双扣费）
- 纯净包检查（pack/check-package.py）与 npm lint 入 CI；端口 4880~4889 自动回退

---

*本任务书基于仓库 HEAD（v0.10.3）生成；审查结论一律以 HEAD 代码为准。*
