# MedKit · 医学题库工坊（MedAgentWork 桌面版）

> 用自己的教材和老师划的重点，在本地生成题库、押题卷和复习手册；
> 也可以直接导入现成题库刷题、记错题、按记忆曲线复习。全程离线，数据不出本机。

**适合**：临床 / 基础医学的期末、考研西综、执业医师备考。
**不适合**：想直接下载现成题库的人——本软件不内置任何题目。

**当前版本**：v0.23.0（更新日志见 [`CHANGELOG.md`](CHANGELOG.md)）。
**安装包状态**：v0.23.0 的安装包尚在构建中，暂未发布——想现在就用可以直接跑源码（`python run_medkit.py`），或装上一版安装包 v0.22.1。

> **License**：本项目以 **[GNU AGPL-3.0](LICENSE)** 开源发布（修改/再分发须以 AGPL 公开源码）。
> 第三方组件的各自许可证见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；决策记录见
> [`docs/adr/ADR-007-license-agpl.md`](docs/adr/ADR-007-license-agpl.md)。

## 医学生快速上手（拿到安装包开始）

1. **安装**：双击 `MedKit-Setup-0.22.1.exe` → 一路下一步（可选桌面图标；v0.23.0 安装包构建中）。
   或绿色版：解压 `MedKit` 文件夹到任意位置，双击 `MedKit.exe`，浏览器自动打开（无需安装 Python）。
2. **首次启动**：直接进入「开始」页（无引导弹层）——点「去刷题」导入你自己的题库即可开练；想用 AI 从教材出题，再去「题库」页。
3. **连接 AI**（只需一次）：推荐注册 [DeepSeek 开放平台](https://platform.deepseek.com) → 充值 ¥10 →
   「API Keys」页创建并复制 Key → 回到软件「① 连接服务商」选中 DeepSeek 卡片 → 粘贴 Key →
   「测试连接」通过后「保存配置」。整套题约 ¥1~5。
4. **出题**：「② 新建课题」→ 上传教材 PDF/Word + 老师重点 → 「解析并预览」→
   （可选：调整题型配比 / Bloom 层级 / 附加要求）→「创建课题 →」→「开始生成」。
   想先看效果？点「载入示例体验」不用上传任何文件。
5. **拿产物**：生成完在「③ 我的项目」点开项目 → 下载 **题库 / 交互押题卷 / 复习手册 / Anki 卡片包**。
   押题卷支持计时答题、自动判分、错题重练（判分后自动同步错题到学习中心，需在 MedKit 内打开），
   可打印；押题卷为按章节+Bloom 分层抽样的 ≤50 题子集（非全量，可多次生成换一批）。
6. **在线复习**：侧栏「题库与手册站」直达 [med-review-site.pages.dev](https://med-review-site.pages.dev/#reviews)
   ——押题卷在线刷、题库 PDF 下载、复习手册分层背，与本软件产物同源同规格。
7. **保持最新**：软件启动时自动检查 [GitHub Releases](https://github.com/2710074390-cyber/medkit/releases/latest)
   新版本（仅提醒 + 跳转下载页）；遇到问题用侧栏信封按钮邮件反馈（自动附版本信息）。

> 数据安全：素材与产物全部保存在本机 `~/.medkit/`；API Key 加密存储；除你自己的 AI 服务商外不上传任何数据。

<details>
<summary>常见问题（点开）</summary>

- **端口被占用？** 自动回退 4881~4889，无需处理。
- **生成可以中途停吗？** 可以，「停止」保留进度，重新「开始生成」断点续跑。
- **题目不满意？** 生成完进入逐题审核台：剔除 / 行内编辑 / 单题重掷，再「保存并重渲染」。
- **想改提示词？** 「④ 提示词与规则」可查看与编辑全部内置提示词（影子副本，随时恢复默认）。
- **成本怎么算？** 创建前有费用预估，生成后写入实际用量；只花你自己的 Key，明明白白。

</details>

## 开发者：运行（开发模式）

```powershell
# 依赖（国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 启动
python run_medkit.py        # 或双击 start.bat
# 浏览器打开 http://127.0.0.1:4880（4880 被占时自动回退 4881~4889）
```

> 浏览器测试（Playwright，`verify.cmd` 第 3 步）：`pip install -r requirements-dev.txt` 后需再
> `playwright install chromium`；无浏览器环境可设 `SKIP_BROWSER=1` 旁路该层。

> **Prompt 版本治理（NX-06）**：凡改动 `medkit/prompts/*.md`，必须同步 ① `tests/fixtures/llm_cases/`
> 对应样本（如有契约输出）与 ② `CHANGELOG.md` 当版新增「`### Prompts`」小节（列改动与影响）；
> 二者缺一视为未完成。提示词契约字段以 `prompts/*.md` 为准（见 `medkit/core/schema.py` 头注）。

## 绿色免安装版（P3）

```powershell
pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
pack\build.bat              # 或：python -m PyInstaller --noconfirm --clean medkit.spec
```

- 产物：`dist\MedKit\`（约 87 MB，含 `MedKit.exe` + `_internal\`）
- 使用：**复制整个 `MedKit` 文件夹**到任意位置 → 双击 `MedKit.exe` → 自动打开浏览器
- 自带资源：静态前端 / 八个提示词（MedGen·MedQC·MedFix·MedReview·MedExplain·MedTutor·MedCards·SyllabusExtract）；**纯净版不含任何学科/题目/样例/测试数据**——首次使用请自行上传教材、教师重点，官方 306 大纲可在「大纲管理」一键导入（md/txt）；**无需安装 Python**
- 纯净检查：`pack\check-package.py`（构建后自动运行；也可手动 `python pack\check-package.py` 对 `dist\MedKit` 断言无样例/种子/测试/字节码）

## 安装包（Inno Setup，可选）

```powershell
# Inno Setup 7.1（已装则跳过；注意 jrsoftware 官网/Aliyun 源不可用时走 ghproxy）
# 下载：https://ghproxy.net/https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe
# 安装后 ISCC.exe 位于 %LOCALAPPDATA%\Programs\Inno\ISCC.exe
# 注意：medkit.iss 使用 ArchitecturesInstallIn64BitMode=x64compatible，需 Inno Setup 6.3+（推荐 7.x）
pack\build.bat              # 已在末尾自动检测 ISCC 并构建安装包
```

- 产物：`dist-installer\MedKit-Setup-{version}.exe`（约 38 MB；版本号单源：`medkit/__init__.py` `__version__` → `pack/build.bat` 生成 `pack/version.iss`）
- 特性：安装向导（中文/英文）· 开始菜单快捷方式 · 可选桌面图标 · 标准卸载（控制面板）· 安装后可选启动

## 目录

```
medkit/
├── run_medkit.py / start.bat   # 入口（端口 4880~4889 自动探测）
├── medkit/
│   ├── main.py                 # FastAPI + 静态前端（Host/Origin 守卫）
│   ├── core/                   # config / providers / modelcatalog(模型目录·三层能力解析) / llm / cost / usage / extract / slice / quota / mineru(OCR) / db(SQLite·迁移) / syllabus / realexams / gap / websearch / library / kc(知识点图谱·学习者模型)
│   │                           # ↑ 生成链                             ↑ 学习内核（含 bank 题库域 / bank_iq 导入解析 /
│   │                           # vault 错题文件层 / vault_index 可弃索引 / scheduler 复习调度 FSRS·SM-2）
│   │                           # 错题归因域（EP-01）与讲解·提问·记忆卡·复习卡四域已于 v0.15 整体移除，
│   │                           # 详见 README「已实现功能」的移除记录
│   ├── agents/                 # medgen / medqc / medfix / medreview / agents/error_analysis.py(归因) / agents/socratic_review.py(苏格拉底)
│   ├── prompts/                # 从 MedAgentWork Prompt版本/ 模板化迁移（含 error_analysis.md / socratic_review.md）
│   ├── gates/                  # options_check / bloom_check / trace_check / dedup_check
│   ├── routers/                # 21 个既有路由域（含 medkit/routers/bank.py = /api/bank/*、medkit/routers/banksync.py = /api/bank/sync/*、medkit/routers/kc.py = /api/kc/*、medkit/routers/vault.py = /api/vault/*、medkit/routers/palette.py = /api/palette/*）
│   └── web/                    # 零 CDN 单页 UI（learn-vault.js = 本地错题视图；bank*.js = 题库刷题）
├── pack/                       # build.bat / medkit.spec / check-release-consistency.py(五件套门禁) / smoke-run-isolated.py(隔离冒烟) / qa-experience-walkthrough.py(医学生视角体验走查·截图取证)
├── docs/                       # features.md(功能与守卫清单·README 的展开版) / 前端AI味调研与改法_2026-10-28.md(文案与搜索) / 体验质检_医学生视角_2026-10-10.md(体验质检·含截图) / AGENT_HANDOFF.md(交接入口) / / 生成链剥离为插件_评估_D2_2026-10-01.md / 双轨退役_迁移演练_D6_2026-10-01.md / research/(4 篇调研 + 1 份 HTML 看板)
└── tests/                      # 1435 项（单元 1366 / 浏览器层 69，分进程跑）
```

## 已实现功能

> 只写「用户能得到什么」。**技术细节、边界条件与逐文件测试项数见
> [`docs/features.md`](docs/features.md)**——那是给维护者的展开版，不必在首屏读完。

- **刷题**：导入 md / txt / json / csv 题库，点选项即判分，判错自动进错题本；
  答题卡跳题、背题模式、键盘操作、收藏与个人笔记。
- **题库同步**：从 MedAgentWork 公开仓库按批次拉取题库（带科目与学期标签），
  也可从出题项目导入；重复同步就地更新，作答记录不丢。
- **错题本**：一道错题一个 markdown 文件，可同步盘、可手改、可整体拷走；
  关键词检索、薄弱项表、按记忆曲线复习。
- **错题助手（可选）**：只依据你自己的错题本作答，结论带 `[来源: 路径#小节]`；
  查不到就说不知道，不拿通识补全。
- **复习排期**：FSRS-6（默认）或 SM-2（随时可切）；记得越牢间隔越长，忘了当天重来。
- **知识点掌握**：题目挂到稳定考点上，答对答错都计入掌握度并据此排「今天先学什么」；
  相似的考点要你点确认，避免掌握度算错。
- **搜索**：Ctrl/⌘+K 一个入口搜动作、题目、错题、考点；「我的题库」与「题目管理」各有本地过滤框。
- **出题工坊（可选 · 默认关）**：用教材 + 教师重点生成题库 / 押题卷 / 复习手册 / Anki 卡包；
  五阶段管线 + 自动检查 + 人工复核清单。
- **产物**：题库 MD+HTML（可搜索、可按题型与层级筛选）、交互押题卷（计时 / 判分 / 错题重练 /
  可打印）、复习手册（分层背、字号可调）、Anki 卡包。
- **本地与隐私**：素材与产物都放在 `~/.medkit/`，API Key 加密存储，服务只监听本机；
  OCR 与网络检索会把对应内容发到你所选的服务（界面上已逐处说明）。
- **成本透明**：创建前给预估，跑完写实际用量；只花你自己的 Key。

**质量**：**1435 项 pytest**（主分支全绿、无 skip；签名相关 3 项需要签名工具链，缺工具时**直接失败不跳过**）
——每个测试文件守什么、逐文件项数多少，见 [`docs/features.md`](docs/features.md)。

## 服务商与模型（2026-08 官方信息核查版）

| 服务商 | 默认模型（2026-08 核查） | 联网搜索 | 单价参考（元/百万 token，估算，以官网为准） |
|---|---|---|---|
| DeepSeek | `deepseek-v4-flash`（官方现行：v4-flash / v4-pro / v4-flash-vision-exp；1M 上下文） | ✅ **自带**（Responses API `web_search` 工具） | 3.0 / 9.0（高峰；空闲减半，缓存命中 0.05~0.30） |
| 智谱 GLM | `glm-5.3`（现行主力；另有 5-Turbo / 4.7） | ✅ **自带**（Web Search API，检索按次计费） | 8.0 / 28.0（缓存命中 2.0） |
| 通义千问 | `qwen-plus`（现行代际至 Qwen3.8 Max/Plus/Flash；qwen3-max 系列已支持联网） | ✅ **自带**（enable_search；qwen3-max 系列及以上） | 2.4 / 9.6（百炼华北2北京） |
| Kimi（月之暗面） | `kimi-k2-thinking`（K2 系列，262K 上下文；另有 turbo 高速档） | 🔴 需外部（博查/手动；境外端点 api.moonshot.ai/v1） | 4.0 / 16.0（缓存命中 1.0） |
| 自定义端点 | 用户自填 | 🔴 需外部（博查/手动） | 以端点官网为准 |

> 说明：DeepSeek 2026-08 官方启用「峰谷定价」（高峰=周一至周五 9:00-12:00、14:00-18:00；周末全天低谷价）；应用内的预估一律显示「参考价，以官网为准」。

- **多轮网络检索**（设计文档 §5.4）：`core/websearch.py` 可插拔后端，**自带/需外部能力实测核查（2026-08 官方文档）**：
  - 🟢 **DeepSeek 内置联网搜索**（自带）—— 官方 `POST /api.deepseek.com/responses` + `web_search` 工具（服务端托管，无需第三方 Key；仅 deepseek-v4 系列）
  - 🟢 **智谱 GLM**（自带）—— 官方专用 Web Search API `POST /open.bigmodel.cn/api/paas/v4/web_search`（`search_result[{title,content,link}]`）
  - 🟢 **通义千问**（自带）—— DashScope `enable_search` + `search_options.enable_source`（`output.search_info.search_results`；2026-08 官方：**qwen3-max 系列已支持联网**，现行代际至 Qwen3.8 Max/Plus/Flash）
  - 🔴 **博查 AI**（需外部，独立计费，官方 `api.bochaai.com/v1/web-search`，响应 `data.webPages.value[{name,url,snippet,summary}]`）
  - ⚪ **手动粘贴**（兜底，无在线检索）
  - LLM 驱动 3 轮循环（考纲·真题·指南 → 缺口补充 ≤2 → 与教材切片冲突核查）；URL 去重 + 视频/社交站过滤 + 同项目缓存 + 单后端错误隔离；`网络参考素材.json` 落盘、MedGen 注入（`[源:网 URL]`，引用配额 0~30% 默认 0）、conflict 条目进 `人工复核清单.md` **绝不自动改写**；「① 服务商」卡片与「② 检索设置」**明示哪个模型自带/需外部**（`/api/search/backends` 数据源）
- **试玩三件套（迭代1）**：①附加生成要求（≤500 字，system 末尾注入，可叠加旋钮）②**试出一题** `/api/trial`（不落项目/不跑管线，随机切片，门禁即检，答案默认隐藏）③提示词查看器（「④ 提示词与规则」tab：四提示词全文 + 占位符高亮 + 门禁规则速览）
- **结构化旋钮 + Bloom 自定义 + 预设（迭代2）**：难度/解析风格/题干风格三旋钮（KNOB_FRAGMENTS 同通道注入）；Bloom 配比四输入 + 实时合计 + 门禁按自定义配比校验；配置预设（内置「期末速通/考研西综强化/执医冲刺」+ 用户自建 CRUD + 导出/导入 JSON 分享）
- **提示词编辑器（迭代3）**：影子副本 `~/.medkit/prompts/`（打包安装目录零写入）；保存占位符校验（缺 `{slice_text}` 等 → 400 列明）；base_hash 漂移检测（升级后「官方已更新」+ 双栏 diff）；恢复默认一键回滚
- **逐题审核台（迭代4）**：项目详情内嵌题目卡片（✓保留/✗剔除/✎行内编辑/🎲单题重掷），保存后重渲染全部产物（题库/押题卷/复习手册/Anki）

## 开发里程碑（历史记录 · 截至 v0.8.1；设计文档见 `docs/archive/design-specs/`，S1 审查全套见 `docs/archive/reviews/s1-2026-08-27/`）

> 本小节为**历史里程碑留档**（v0.8.x 及更早）。各版本完整变更以 `CHANGELOG.md` 为准，
> 当前版本能力见上一节「已实现功能」。

- ✅ P1 迭代1：设置页 + 素材解析预览 + 课题创建
- **P1 迭代2**：五阶段管线 + 产物渲染（离线全链路测试通过）
- ✅ R2 批次二（剩余 P2 打磨全量落地）——「仅重渲染」单产物（题库/押题卷/手册/Anki，无 token 消耗）· 概览「近期活动」时间线 · 提问会话「清理 30 天无活动」 · 更新检查支持预览版后缀（0.8.0-rc.1 不再折叠）· base_url 客户端校验 + 切服务商覆盖前确认 · 主题跟随系统（未手动选择时）· 外链离线拦截提示 · 配比「还差 X%」实时提示 · 生成/解析按钮防双击 · 项目消耗「万 token」口径统一 · 讲解/判分后概览自动刷新 · 批量导入进度 · 记忆卡面板新增入口 · 题库分页按题数（组不拆散）+ 筛选按项目隔离 · 押题卷答案明文声明 · 大图降采样+懒加载（单文件产物不膨胀）
- ✅ P0 全部修复（第二轮全链路审查）——押题卷抽样防漂移/案例组原子（`paper_ids.json` 复用；押题卷 = 分层抽样 ≤50 题）· 审核台答案键按题型校验 + 案例/选项组题禁止重掷 + 全选/反选与筛选作用域 · .apkg B1 共享选项修复 · 提示词页补全 8 个（含 medcards/syllabus_extract）· 记忆卡 Anki 导出入口（.apkg/.txt）· 概览科目口径统一（mastery/recommend 支持 subject）· 讲解 Markdown 表格/代码块 · Anki 图/表题占位提示 · 押题卷计时重置/离线同步提示 · 复习手册锚点/图片白名单 · 答案归一化口径统一（D19）
- ✅ 全面审查修复（P0 全部 + P1 长任务闭环 + P2 路线图：Anki / 押题卷练习化 / 查重门禁 / 主题与图标 / 端口回退 / ruff+TestClient）
- ✅ v0.5 S0~S3：安全网（git 基线 · verify.cmd）→ 正确性修复+数据刷新 → 工程化重构 → .apkg 导出 / A3·A4 案例题 / B1 组题 / 素材库复用
- ✅ v0.6：题库与手册站入口 + 邮件反馈 + GitHub Releases 内置更新检查 + 品牌新图标；开源至 [github.com/2710074390-cyber/medkit](https://github.com/2710074390-cyber/medkit)
- ✅ v0.7：学习闭环 M1~M5（错题本 / 掌握度诊断 / 教材切片讲解(联网补充) / 提问式学习 / SM-2 复习计划）+ 前端全面审查七轮落地（子导航五视图、错题直达讲解/提问、成本预估前置、产物页主题单源、审核台批量编辑）——代码基线已提交，待打包发布
- ✅ S0 技术底座（v0.8 先行）：`core/db.py`（SQLite + WAL + user_version 迁移 + 升级前备份 + JSON→SQLite 幂等导入，JSON 原文件改名 `*.pre-db-*.bak` 可回滚）；学习库四域模块（library/review/explain/tutor）事务化——外签名零改动、routers 零改动，**并发读-改-写丢失更新根治**（K5 复现：JSON 丢 49/41 次 → SQLite 0 偏差）；SPIKE K1/K2/K4/K5 通过（FTS5+jieba 检索、py-fsrs、图片内嵌基准、并发写）；ADR×5 落 `docs/adr/`；K3（306 大纲 MinerU 抽取）待用户提供 PDF
- ✅ WP-01 大纲覆盖度引擎（v0.8·考试锚定）：`core/syllabus.py` + 迁移 v2（`syllabus_items`）+ `/api/syllabus/*`（ensure/parse/confirm/coverage/report，parse 本地规则零 LLM、confirm 人工确认门）+ 学习中心第 6 视图「大纲覆盖」（统计卡 + 章树 + 状态 chip + 粘贴导入 + 导出 md）+ medgen 大纲锚定注入（≤800 字）；种子 1291 条/10 科（GoldenSet 真题 + 知识库素材教材元数据构建，GS 真题计数供 WP-02 考频）；pytest 203 全绿
- ✅ 四项体验升级：① 网络检索「测试后端」修复——内置后端（DeepSeek/智谱/千问）复用服务商 LLM Key，博查缺 Key 时明确提示（原 bug：把博查 Key 槽位传给内置后端 → 永远「未配置 api key」）；② 错题导入多格式——批量导入支持 **.json / .csv / .md / .txt**（CSV 表头别名 + A~F 列、MD/TXT 按题号切块、JSON 兼容 stem/options[{label,text}] 官方结构，全部本地解析零 LLM）；③ 厂商信息去时效化——注记不再固化模型代际/版本断言，统一「以官方最新为准」引导 +「获取模型列表」动态拉取（检索后端说明同步精简）；④ **以教师重点为纲**——大纲覆盖视图默认标准 =「教师重点」：自动扫描所有项目的教师重点切片 → 考点条目（幂等同步），错题/掌握度按教师重点标准判定覆盖；「官方大纲 / 全部」标准可切换（备用）
- ✅ WP-02 真题考频 + WP-03 薄弱组卷（v0.8·考试锚定闭环）：`core/realexams.py` + 迁移 v3（`realexam_freq`）——粘贴/上传自备真题 → 本地词典匹配计数（零 LLM）→ **人工确认门**（未确认不进任何权重）→ 章节×频次热力表 + 导出（**不展示真题原文**）；`/api/library/realexams/*` + 学习中心「大纲覆盖」内集成「真题考频」卡；`core/gap.py`——`plan()` 纯本地配题（priority×考频×未覆盖，单知识点≤3题）+ 复用课题创建通道（薄弱点清单注入 + scope=gap + 成本预估前置 + 24h 幂等）+ 概览「⚡一键刷薄弱组卷」；pytest 210 全绿
- ✅ WP-04 医学图像/表格题（v0.8·结构性补齐）：项目详情「图片素材」上传（教材图/心电图/血常规截图 → `assets/fig_N` + image 切片）→ 出题注入「至少 1 题引用 + 题干写『如图所示』」（image_ref 门禁硬校验，不匹配剔除）→ 产物渲染 base64 内嵌 `<figure>`（单文件可移动）+ Markdown 表格 → `<table>`（XSS 白名单 + 打印防跨页）→ 错题随图回流（学习中心可看图）；pytest 217 全绿
- 🔲 后续可选：网络检索更多后端、自备真题引用配额滑杆、v0.8 收尾（每日学习计划 WP-08 / 数据可携带 WP-09，见 `docs/archive/reviews/s1-2026-08-27/结构化执行方案_2026-08-27.md`）
