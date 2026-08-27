# MedKit · 医学题库工坊（MedAgentWork 桌面版）

> 授人以渔：医学生自备教材 + 教师重点（+ 可选自备真题 / 网络检索），自选服务商与 API Key，
> 本地一键生成**全新的**题库 / 押题卷 / 复习手册。不携带、不内置任何旧产物质料。

## 医学生快速上手（拿到安装包开始）

1. **安装**：双击 `MedKit-Setup-0.8.0.exe` → 一路下一步（可选桌面图标）。
   或绿色版：解压 `MedKit` 文件夹到任意位置，双击 `MedKit.exe`，浏览器自动打开（无需安装 Python）。
2. **首次启动**：会弹出 3 步欢迎向导——软件做什么、怎么拿 API Key、怎么开始，跟完即可。
3. **连接 AI**（只需一次）：推荐注册 [DeepSeek 开放平台](https://platform.deepseek.com) → 充值 ¥10 →
   「API Keys」页创建并复制 Key → 回到软件「① 连接服务商」选中 DeepSeek 卡片 → 粘贴 Key →
   「测试连接」通过后「保存配置」。整套题约 ¥1~5。
4. **出题**：「② 新建课题」→ 上传教材 PDF/Word + 老师重点 → 「解析并预览」→
   （可选：调整题型配比 / Bloom 层级 / 附加要求）→「创建课题 →」→「开始生成」。
   想先看效果？点「🎓 载入示例体验」不用上传任何文件。
5. **拿产物**：生成完在「③ 我的项目」点开项目 → 下载 **题库 / 交互押题卷 / 复习手册 / Anki 卡片包**。
   押题卷支持计时答题、自动判分、错题重练，可打印。
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
- 自带资源：静态前端 / 八个提示词（MedGen·MedQC·MedFix·MedReview·MedExplain·MedTutor·MedCards·SyllabusExtract）/ 示例素材 / **内置西综306 大纲种子**（打包路径已验证）；**无需安装 Python**

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
│   ├── core/                   # config / providers / llm / cost / usage / extract / slice / quota / mineru(OCR) / db(SQLite·迁移) / syllabus / realexams / gap / scheduler(FSRS·SM-2) / cards / websearch / library / review / explain / tutor
│   ├── agents/                 # medgen / medqc / medfix / medreview
│   ├── prompts/                # 从 MedAgentWork Prompt版本/ 模板化迁移
│   ├── gates/                  # options_check / bloom_check / trace_check / dedup_check
│   └── web/                    # 零 CDN 单页 UI
└── tests/                      # test_smoke / test_pipeline_offline / test_api（TestClient）
```

## 已实现功能（v0.8.0）

- **服务商 BYOK**：DeepSeek / 智谱 GLM / 通义千问 / Kimi（月之暗面）预置（卡片带官网注册跳转）+ 自定义 OpenAI 兼容端点；双模型档（下拉选择，获取模型列表后默认选最新，支持手动输入）；测试连接（30s 超时）；**保存配置空 Key = 保留原值**；**Key 落盘 DPAPI 加密**（Windows，ctypes 零依赖；旧明文自动升级）；**多服务商 Key 存档**（切换服务商自动归档旧 Key，切回免重填；「API Key 管理」卡片统一查看掩码/切换/删除，仿 Cherry Studio）
- **素材解析**：PDF(文本层)/DOCX/MD/TXT/图片；章节切片；教师重点词频配额加权；线程池执行不阻塞
- **素材库复用（S3）**：解析结果可「保存为素材会话」（`~/.medkit/sessions/`），**跨项目复用**；多个会话**合并载入为教材**（多教材合并出题，quota 跨 session 按章加权）；项目**配置模板**一键存/取（科目/题型配比/Bloom/旋钮/附加要求）
- **扫描件 OCR（MinerU · 任务制）**：精准 API（≤200MB/≤600 页，每日 2000 页高优先级额度，2026-08 官方现行）/ 免 Token 轻量 API（≤10MB）；进度轮询 + 取消 + 自动加入输入；**UI 明示上传云端**
- **出题管线（五阶段，后台线程 + 实时日志）**：
  - ① MedGen：按切片配额并发（≤3）出题（A1/A2/X + **B1 真组题（共享选项组）**；A3/A4 案例组题随配额按需输出、无独立配比入口）；HC 命题规则 + [源:切片] 溯源；**题量不足自动补足 ≤2 轮 + 超发截断**；**全文仅在 system 注入一次**（输入成本约 -40%）；模板占位符一次性替换（防教材文本二次注入）
  - ② 门禁①：选项质量（R 规则子集）+ Bloom 30/40/25/5 + 溯源回查 + **n-gram 查重（Jaccard>0.8 → MedFix 改写；案例组/选项组内跳过）**，自动修复 ≤2 轮
  - ③ MedQC：LLM-as-judge 并行分批质检，score + gate_decision（浮点/None score 容错）
  - ④ MedFix：按 issue 定向修复（**合并策略保留溯源/案例/组结构字段**）
  - ⑤ MedReview：分层复习手册（考点速记/易混淆/临床路径/数值速查/背诵清单）
  - ⑥ 渲染：题库 MD+HTML（**案例/选项组按组折叠**）/ **交互押题卷（X 型 checkbox+集合判分 / localStorage 续答 / 答题卡 / 计时断点恢复 / 错题重练 / 案例组分组呈现+分组判分 / 打印）** / 复习手册 MD+HTML / **Anki 导出（.txt + S3 .apkg 真包：项目名稳定哈希，标准卡+X 型自评卡，标签=题型/Bloom/章节）**
  - **渲染前终检（D2）**：修复轮用尽仍超限/缺字段的题剔除出产物 + 写入人工复核清单，绝不强行渲染
- **长任务体验（U1/U2/U3/I1）**：**管线可取消**（停止按钮带确认，保留断点）+ **断点续跑**（逐切片 checkpoint）+ 三线程并发 + 六阶段 stepper（出题/门禁/质检/修复/汇总/产物）+ 百分比进度 + 阶段明细（质检按批回写）+ 日志着色高亮
- **成本透明（U5）**：解析/创建前显示「预计 X 万 token · 约 ¥Y（参考价，以官网为准）」——**项目创建走 `/api/cost/estimate`（`core/cost.py` 单源公式）**；学习中心讲解/提问为前端粗估（显示「参考价」）；跑完写实际 usage + 折算成本到项目 meta；run/trial/regen 按次上下文独立记账
- **安全加固**：Host/Origin 校验中间件（含 IPv6 `[::1]`）；pid 路径消毒（含预设删除）；损坏 meta.json 容错（422）；产物 HTML 全量转义 + 复习手册白名单消毒（href 仅 http/https）；`javascript:` 剥纯文本；Key 不进 URL；配置深拷贝防默认值污染
- **工程化（S2）**：`routers/*` 十模块 + `state.py`；lifespan；统一异常体（LLM/Search/MinerU/PipelineError）；`~/.medkit/logs/` RotatingFileHandler；**版本单源** `medkit/__init__.py`；`verify.cmd` 一键验证 + GitHub CI 工作流
- **在线入口与反馈（v0.6）**：侧栏「题库与手册站」外链（[med-review-site.pages.dev](https://med-review-site.pages.dev/#reviews)，题库/押题卷/复习手册在线合集）；邮件反馈弹窗（复制邮箱 + `mailto:` 自动附版本/系统信息，2710074390@qq.com）
- **内置更新检查（v0.6）**：`GET /api/update/check` 请求 GitHub Releases（`core/update.py`，纯标准库比较逻辑）；启动 4s 静默检查 + 侧栏版本号红点 + 手动点击检查；仅提醒 + 跳转下载页；无网/无 Release 优雅降级不报错
- **新图标（v0.6）**：与 med-review-site 网站图标同构（圆角方块 + MW 字标），青绿渐变 + Segoe UI 字体风格区分（`pack/make_icon.py` 逐尺寸原生绘制）
- **引导与体验**：**首启 3 步欢迎向导**（软件做什么 → 怎么拿 Key → 两种开始方式；老用户不打扰）；素材要求卡 / 一键示例 / 体检警告 / 就绪清单 / 成本预估；**拖拽上传 + 文件清单可移除**；**hash 路由（刷新保持 tab）**；**配比条可视化 + 色块间把手拖拽调比**（相邻两段间转移百分比，合计恒定；键盘 ←/→ ±5%，触屏 pointer 通用）；配比实时合计；亮/暗主题切换（含产物页，记忆偏好，隐私模式容错）；SVG 图标；toast 堆叠；自定义删除确认；轮询失败 3 次才停；全局 onerror/unhandledrejection 兜底
- **前端设计刷新（2026-08-27 全面审查落地）**：学习中心改「子导航 + 六视图」（概览/错题本/讲解产物/提问学习/复习计划/大纲覆盖，一屏一任务，记忆上次视图）；页面标题层级提升（图标 + 21px）；卡片头组件替代 float 计数（窄屏不再挤压换行）；**错题行增强**（→讲解 / →提问 直达、详情展开、chip 化 meta）；**讲解产物默认折叠 + 重新生成（带确认）+ 复制反馈**；**触发 LLM 前展示成本预估**（讲解/提问，参考价；点击后展示）；侧栏学习中心红色徽章（今日到期复习卡 + 进行中提问会话）；**Ctrl/⌘+1..5 页签快捷键 + Alt+1..6 学习中心子视图**；390px 窄屏溢出修复（学习中心全视图无横向滚动）；统一空状态组件（大图标 + 引导文案）
- **产物页优化（第二轮）**：题库 HTML 全题型过滤+计数、关键词搜索（含案例题干，索引不重复可见文案）；押题卷题型标签中文化、已答计数、判分 ✓/✗ 徽章、内联提示条替代原生 alert、打印样式完善；**学习库数据卫生**——乱码检测（dashboard 计数 + 横幅提醒）与一键修复（可逆 cp1252→utf-8 还原 + 不可逆记录标记 + 自动备份，不删数据）
- **审核台与加载优化（第三轮）**：逐题审核台新增搜索/题型/Bloom 过滤、批量保留/剔除（带确认）、单题重掷后其余编辑与剔除状态保留并自动定位；「刷新」对未保存修改弹确认；项目详情产物区卡片化（图标+中文名网格）；学习中心 subjects/mastery 30s 缓存减少重复请求
- **押题卷作答闭环（第四轮）**：未答确认（防漏答）、判分后锁定（防误改）与重新作答解锁、重开页续答归还提示（计时延续）、得分含用时、答题卡 tooltip；连接页 provider 卡片「已配置 Key ✓」角标 + 当前模型回显
- **复习场景优化（第五轮）**：复习手册阅读体验——字号调节（A−/A＋/默认，记忆偏好）、阅读进度条、目录吸顶、回顶部按钮；复习计划到期卡「查看提示」（懒加载教材原文切片，零 LLM，关键词 top-k）；薄弱点清单行内「讲解/提问/铺卡」直达；首启向导纳入学习中心
- **导出与回顾（第六轮）**：项目详情「预览 Anki 卡样」（弹窗看前 3 张卡正反面与标签，导出前心里有数）；题库页 Bloom 层级过滤 + 题型/Bloom/关键词过滤状态本地记忆（重开保持）+ 一键重置；押题卷**成绩留存**（最近 10 次，重开显示「上次/最佳 · 用时」）；页签快捷键 title 提示（Ctrl+1~5）
- **主题单源与审核效率（第七轮）**：新增 `render/pagechrome.py`——题库/押题卷/复习手册三套产物页主题（双主题变量/基础样式/明暗切换脚本）**单一来源**，改一处全生效、防漂移；审核台**批量操作**（多选勾选 → 批量改 Bloom / 批量剔除恢复，标题实时计数）+ 单题「复制」题面全文到剪贴板
- **质量**：**192 项 pytest**（冒烟 / 离线管线含断点续跑·取消·案例组 / API 层 TestClient 含 Key 存档闭环 / S1 回归四件套 / S2 重构契约 / S3 apkg·案例结构·素材会话 / v0.6 更新检查 mock / v0.7 学习闭环与讲解·复习·仪表盘 / 迁移与乱码修复 / 押题卷与题库回归 / **S0 存储底座**：db 迁移·回滚·备份·导入幂等、library/review/explain/tutor SQL 模式端到端、并发 100/100 无丢失）+ ruff 干净 + PyInstaller exe 冒烟

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

## 开发里程碑（设计文档见 `docs/archive/design-specs/`，S1 审查全套见 `docs/archive/reviews/s1-2026-08-27/`）

- ✅ P1 迭代1：设置页 + 素材解析预览 + 课题创建
- ✅ P1 迭代2：五阶段管线 + 产物渲染（离线全链路测试通过）
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
