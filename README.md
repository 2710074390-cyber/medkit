# MedKit · 医学题库工坊（MedAgentWork 桌面版）

> 授人以渔：医学生自备教材 + 教师重点（+ 可选自备真题 / 网络检索），自选服务商与 API Key，
> 本地一键生成**全新的**题库 / 押题卷 / 复习手册。不携带、不内置任何旧产物质料。

> **License**：本项目以 **[GNU AGPL-3.0](LICENSE)** 开源发布（修改/再分发须以 AGPL 公开源码）。
> 第三方组件的各自许可证见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；决策记录见
> [`docs/adr/ADR-007-license-agpl.md`](docs/adr/ADR-007-license-agpl.md)。

## 医学生快速上手（拿到安装包开始）

1. **安装**：双击 `MedKit-Setup-0.10.6.exe` → 一路下一步（可选桌面图标）。
   或绿色版：解压 `MedKit` 文件夹到任意位置，双击 `MedKit.exe`，浏览器自动打开（无需安装 Python）。
2. **首次启动**：会弹出 3 步欢迎向导——软件做什么、怎么拿 API Key、怎么开始，跟完即可。
3. **连接 AI**（只需一次）：推荐注册 [DeepSeek 开放平台](https://platform.deepseek.com) → 充值 ¥10 →
   「API Keys」页创建并复制 Key → 回到软件「① 连接服务商」选中 DeepSeek 卡片 → 粘贴 Key →
   「测试连接」通过后「保存配置」。整套题约 ¥1~5。
4. **出题**：「② 新建课题」→ 上传教材 PDF/Word + 老师重点 → 「解析并预览」→
   （可选：调整题型配比 / Bloom 层级 / 附加要求）→「创建课题 →」→「开始生成」。
   想先看效果？点「🎓 载入示例体验」不用上传任何文件。
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
│   ├── core/                   # config / providers / llm / cost / usage / extract / slice / quota / mineru(OCR) / db(SQLite·迁移) / syllabus / realexams / gap / scheduler(FSRS·SM-2) / cards / websearch / library / review / explain / tutor
│   │                           # ↑ 生成链                             ↑ 学习内核
│   │                           # EP-01 错题归因：core/errorpipe.py(五阶段编排) / core/kpid.py(知识点ID对齐) / core/metacog.py(纯函数统计) / core/error_events.py(流水) / core/vision.py(图像识别：原生视觉优先 + OCR 兜底) / core/apkg_import.py(Anki .apkg 导入) / core/errsearch.py(在自己的错题里检索) / render/notebook_md.py(Markdown 复盘笔记)
│   ├── agents/                 # medgen / medqc / medfix / medreview / agents/error_analysis.py(归因) / agents/socratic_review.py(苏格拉底)
│   ├── prompts/                # 从 MedAgentWork Prompt版本/ 模板化迁移（含 error_analysis.md / socratic_review.md）
│   ├── gates/                  # options_check / bloom_check / trace_check / dedup_check
│   ├── routers/                # 17 个既有路由域 + medkit/routers/errors.py（/api/errors/* · 30 端点）
│   └── web/                    # 零 CDN 单页 UI（learn-meta.js = 元认知视图）
├── pack/                       # build.bat / medkit.spec / check-release-consistency.py(五件套门禁) / smoke-run-isolated.py(隔离冒烟)
├── docs/                       # AGENT_HANDOFF.md(交接入口) / 错题归因流水线_EP-01_设计与实现.md / 考研错题分析专项重构方案_2026-09-27.md / 生成链剥离为插件_评估_D2_2026-10-01.md / 双轨退役_迁移演练_D6_2026-10-01.md
└── tests/                      # 1438 项（单元 1356 / 浏览器层 82，分进程跑）
```

## 已实现功能（v0.10.6）

- **错题归因流水线（EP-01 · v0.10.5 新增）**：把「考生自己记下的错题」变成**可纵向追踪的元认知档案**——记录的不只是「我选错了」，而是「我**为什么**选错、当时**有多确信**、这个错因**跨轮次怎么演变**」。
  - **五阶段管线**（`core/errorpipe.py`，可任意跳过子集）：`intake` 归一录入 → `kp_align` 知识点对齐 → `attribute` LLM 归因 → `persist` 落库 → `analyze` 统计。**P3 是唯一有外部依赖的阶段**，跳过即全程零 LLM（离线可用，医院/图书馆场景）。归因走 `agents/error_analysis.py` + `prompts/error_analysis.md`。
  - **录入闸门（刻意保留摩擦）**：`confidence`（1-5 事前自评）与 `my_reasoning`（原始推理）**必须当场填**，缺任一则 `gate_ok=False`——可入库但**禁止进入归因**。**不提供补填接口**（路由扫描测试钉死），因为事后补填会让校准曲线失真、数据资产作废。
  - **红线：AI 不判对错，正确答案由考生提供**。归因只做「解释与定位」（产出 `ai_error_tag` / `counterfactual` / `fix` / 建议回看章节），并与人工 `error_tag` 比对派生 `tag_match` ——**不一致的题是最高价值样本**。
  - **五组元认知统计**（`core/metacog.py`，**全纯函数、零 IO**）：校准曲线 + Brier 分数 + JOL 偏差 · 标签×科目热力图 · 跨轮次迁移矩阵（同 tag 三轮不变 → 标红「换方法」）· 人工/AI 归因一致率 · **减法清单**（「建议别看什么」，按 题量×正确率×**近三年真题频次** 加权，频次来自生成链留下的 `realexam_freq` 表）。
  - **知识点 ID 对齐**（`core/kpid.py`）：`sha1(subject|chapter|norm(topic))` 前缀 + 别名表，解决同一考点在三轮里的不同表述（「心输出量」/「心排出量」/「CO」）→ 跨轮次迁移矩阵可信的前提。**不做 embedding 自动聚类**（错归会污染不可逆档案，人工确认别名表成本更低）。
  - **纵向追踪走流水不走快照**：`core/error_events.py`（`error_events` 表 append-only，每轮每次作答一条）；`mistakes` 是**可编辑快照**。迁移矩阵从流水算——快照会被用户改，流水不会。
  - **苏格拉底复习**（`agents/socratic_review.py` + `prompts/socratic_review.md`）：复习同一题时不直接给解析，先问「你认为正确的是哪个，为什么」，答完才给。**不设默认**（单题耗时 3-5 倍，早鸟轮是负收益），仅对高价值题（`tag_match=0` 或「confidence=5 但做错」）启用。红线②在自由文本字段上有**出口机械剥离**（`_strip_answer_echo`）——提示词与契约两道防线挡不住模型把答案写进 `gap`/`next_question`。
  - **数据规模**：schema **v8** 迁移（`mistakes` +6 列 + `kp_alias` / `error_events` 两新表），全部可空、**幂等**（`PRAGMA table_info` 防御）、走 ADR-005 升级前自动备份。既有 17 个路由文件与 `/api/library/*` 行为不变。
  - **图像录入：原生视觉优先、OCR 兜底**（`core/vision.py` + `prompts/error_image_extract.md`）：拍一张错题图即可走完整管线（识别 → 归一 → 对齐 → 归因 → 落库，全程 SSE 流式）。**优先**用当前 `model_gen` 的原生视觉能力直接读图（一次调用同时认字与分字段，还能看出图上标了哪个选项）；模型名不带视觉标记时**自动**改走 MinerU OCR（`ocr_mode` 有 Token 用精准 API、无 Token 用免 Token 轻量 API），再用本地规则（零 LLM）拆字段。能力判定是**正面判据**（认不出即判不支持 → 走 OCR），`识别方式` 下拉可强制覆盖。**图里没有答案就留空**：识别结果里的 `answer` 只有带 `answer_from_image` 出处声明才被采用（`vision.sanitize` 强制），模型没有任何"编一个答案"的通道；`confidence` / `my_reasoning` 也**不在**识别契约里（`ErrorImageExtract`），只能由用户在识别前手填——图片入口不构成绕过闸门的通道。
  - **30 个端点** `medkit/routers/errors.py` 的 `/api/errors/*`：录入 `intake` · 图像录入 `intake/image`（SSE）· 已确认字段入库 `intake/stream`（SSE）· Anki 导入 `import/apkg` · **错题检索 `search`**· 图像识别 `image/{capability,extract,extract/stream}` · 归因 `cards/{id}/attribute` · 闸门 `cards/{id}/gate` · 统计 `stats/{calibration,heatmap,migration,agreement}` · 减法 `subtract` · 概览 `overview` · 知识点 `kp/{resolve,list,register,merge}` · 导入导出 `import|export/jsonl` · 苏格拉底 `socratic/{eligible,start,answer,{sid}}` · 轮次 `rounds` · 自检 `health`
  - **Markdown 复盘笔记**（`render/notebook_md.py` + `GET /api/errors/export/notebook`，输出层出口）：把五组统计渲染成一篇可读、可打印、可留档的复盘——回答「我的确信准不准 / 我总在哪类错因上栽 / 换个轮次改了没有 / 本周可以不排什么」。**零 LLM**（所有句子要么是常量映射，要么是统计量的直述，故不会退化成「要扎实基础」类废话），且**刻意不列题目、不给答案**（题目本体属于错题本）。元认知视图有「导出复盘笔记(.md)」按钮。
  - **在自己的错题里检索**（`core/errsearch.py` + `GET /api/errors/search`，EP-01 阶段 4）：**检索面严格限定在自己的数据内**（题干/想法/错因/修正/解析/科目章节），**不含教材正文**（《总纲》§2.4）——有 AST 守卫钉住。分词复用 `db.fts_tokens`（jieba + CJK 二元组）；排序**先覆盖率、再分数**；片段**不取自答案字段**。**不建 FTS 索引**（错题万级以下，实时扫一遍 ≈0 ms；建索引要动 schema 且引入「索引与真身漂移」这一新失效模式）。**已知边界**：缩写召回（「心衰」→「心力衰竭」）不在能力范围内，需别名表（待办 D4）。错题本有检索框。
  - **Anki `.apkg` 导入**（`core/apkg_import.py` + `POST /api/errors/import/apkg`）：`.apkg` 是 ZIP 包着 SQLite（`collection.anki2`）。**按字段名取值**（不同牌组字段顺序不同，按位置会错位）——MedKit 自己导出的包用 `题干/选项/答案/解析/溯源`，故**导出→导入往返逐字段对上**；通用英文牌组也认（`Question/Answer/Explanation`）。`dry_run=1` 先体检（牌组名、模型、字段映射、多少条不是题目）再决定要不要导。**落库走与 JSONL 相同的通道** ⇒ 导入不会绕过闸门（Anki 里没有「把握程度/当时的想法」，导入条目一律未过闸门、只入库不归因，且如实告知）。压缩炸弹/条数上限/坏包一律给可读原因。
  - **数据出口**：JSONL 是**唯一备份格式**（用户数据不可再生，需可携带）；`GET /api/errors/export/jsonl` 全量导出，`POST /api/errors/import/jsonl` 回灌。
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
- **引导与体验**：**首启引导（v0.10.5）**：开始页空状态「三步上手卡」（连接 AI → 上传教材与教师重点 → 载入示例体验，点过「先逛逛」本会话不再显示，有课题后自然消失）；首启 3 步欢迎向导（软件做什么 → 连接 AI → 选择开始方式）**不再以有无 API Key 为门槛**——首次使用均展示，已配 Key 用户第 2 步显示「已连接 ✓」并跳过配置动作；素材要求卡 / 一键示例 / 体检警告 / 就绪清单 / 成本预估；**拖拽上传 + 文件清单可移除**；**hash 路由（刷新保持 tab）**；**配比条可视化 + 色块间把手拖拽调比**（相邻两段间转移百分比，合计恒定；键盘 ←/→ ±5%，触屏 pointer 通用）；配比实时合计；亮/暗主题切换（含产物页，记忆偏好，隐私模式容错）；SVG 图标；toast 堆叠；自定义删除确认；轮询失败 3 次才停；全局 onerror/unhandledrejection 兜底
- **前端设计刷新（2026-08-27 全面审查落地 · v0.8.1 IA 校正）**：学习中心改「子导航 + 五视图」（概览/错题本/讲解产物/提问学习/大纲覆盖，一屏一任务，记忆上次视图；**复习计划已随 SM-2 复习卡 + FSRS 记忆卡迁入主「刷题」tab**，与卡片式复习合并）；页面标题层级提升（图标 + 21px）；卡片头组件替代 float 计数（窄屏不再挤压换行）；**错题行增强**（→讲解 / →提问 直达、详情展开、chip 化 meta）；**讲解产物默认折叠 + 重新生成（带确认）+ 复制反馈**；**触发 LLM 前展示成本预估**（讲解/提问，参考价；点击后展示）；侧栏学习中心红色徽章（今日到期复习卡 + 进行中提问会话）；**Ctrl/⌘+1..5 页签快捷键（开始/刷题/题库/学习中心/我的）+ Alt+1..5 学习中心子视图（复习计划在「刷题」tab）**；390px 窄屏溢出修复（学习中心全视图无横向滚动）；统一空状态组件（大图标 + 引导文案）
- **产物页优化（第二轮）**：题库 HTML 全题型过滤+计数、关键词搜索（含案例题干，索引不重复可见文案）；押题卷题型标签中文化、已答计数、判分 ✓/✗ 徽章、内联提示条替代原生 alert、打印样式完善；**学习库数据卫生**——乱码检测（dashboard 计数 + 横幅提醒）与一键修复（可逆 cp1252→utf-8 还原 + 不可逆记录标记 + 自动备份，不删数据）
- **审核台与加载优化（第三轮）**：逐题审核台新增搜索/题型/Bloom 过滤、批量保留/剔除（带确认）、单题重掷后其余编辑与剔除状态保留并自动定位；「刷新」对未保存修改弹确认；项目详情产物区卡片化（图标+中文名网格）；学习中心 subjects/mastery 30s 缓存减少重复请求
- **押题卷作答闭环（第四轮）**：未答确认（防漏答）、判分后锁定（防误改）与重新作答解锁、重开页续答归还提示（计时延续）、得分含用时、答题卡 tooltip；连接页 provider 卡片「已配置 Key ✓」角标 + 当前模型回显
- **复习场景优化（第五轮）**：复习手册阅读体验——字号调节（A−/A＋/默认，记忆偏好）、阅读进度条、目录吸顶、回顶部按钮；复习计划到期卡「查看提示」（懒加载教材原文切片，零 LLM，关键词 top-k）；薄弱点清单行内「讲解/提问/铺卡」直达；首启向导纳入学习中心
- **导出与回顾（第六轮）**：项目详情「预览 Anki 卡样」（弹窗看前 3 张卡正反面与标签，导出前心里有数）；题库页 Bloom 层级过滤 + 题型/Bloom/关键词过滤状态本地记忆（重开保持）+ 一键重置；押题卷**成绩留存**（最近 10 次，重开显示「上次/最佳 · 用时」）；页签快捷键 title 提示（Ctrl+1~5）
- **主题单源与审核效率（第七轮）**：新增 `render/pagechrome.py`——题库/押题卷/复习手册三套产物页主题（双主题变量/基础样式/明暗切换脚本）**单一来源**，改一处全生效、防漂移；审核台**批量操作**（多选勾选 → 批量改 Bloom / 批量剔除恢复，标题实时计数）+ 单题「复制」题面全文到剪贴板
- **质量**：**1438 项 pytest**（1438 passed；主分支全绿，无 skip；含冒烟 / 离线管线含断点续跑·取消·案例组 / API 层 TestClient 含 Key 存档闭环 / S1 回归四件套 / S2 重构契约 / S3 apkg·案例结构·素材会话 / v0.6 更新检查 mock / v0.7 学习闭环与讲解·复习·仪表盘 / 迁移与乱码修复 / 押题卷与题库回归 / **S0 存储底座**：db 迁移·回滚·备份·导入幂等、library/review/explain/tutor SQL 模式端到端、并发 100/100 无丢失 / **EP-01 错题归因**：`test_errorpipe` 54 + `test_socratic` 37 + `test_metacog` 统计 + **`test_errorpipe_image` 68**（图像录入：能力判定正面判据 + auto 路由 + 视觉失败降级 OCR + 无出处答案丢弃 + 流式/非流式结果一致 + 端点 AST 守卫）+ **`test_frontend_domain_coupling` 21**（**前端分域耦合守卫**：EP-01 片引用插件独有符号= **硬红线**（实测抓到 1 处真违规 `letters`，已修）；外壳 → 插件 8 处 / 插件 → EP-01 2 处走**登记制**（登记不得腐烂）；含元守卫自证检测器能命中，并覆盖两处误报源（函数内局部同名、对象字面量的键））+ **`test_errsearch` 18**（错题检索：二元组的真实作用是**部分重叠召回**（不是「词内查找」——连续子串已由子串匹配覆盖）/ 字段权重 / **覆盖率优先**排序 / 科目是作用域不是查询 / 空查询与单字查询回显 tokens（区分「没搜」与「没命中」）/ 片段不取自答案字段 / **结构守卫**「检索面只有自己的数据」）+ **`test_kp_aliases` 37**（**D4 别名表**：25 组等价组对称展开；结构守卫（同组不跨组重复 / 成员不得含同组其他成员为子串=长串冗余 / `_INDEX` 无冲突）+ 元守卫「遮蔽检测器真的能命中且与真实召回集一致」+ `expand`/`match_query` 契约 + **双向行为召回**（parametrize：每组任一成员都能召回只写另一成员的记录）+ 覆盖率不因别名稀释 + **钉住 `"心衰" not in "心力衰竭"`**——此前三处文档误判为子串，实测为 False）+ **`test_assertion_audit` 4**（**全仓零断言用例审计**：`test_*` 必须含实质断言（`ast.walk` 到循环体内、认 `raise AssertionError`、裸 `assert <Name>` 算数、`assert True` 不算）；断言在同文件辅助里时须**显式登记**，新增未登记即红、登记不得腐烂；含元守卫「**用内存构造的样本自证检测器真的能命中**」）+ **`test_skip_audit` 5**（**全仓 skip 审计**：每一处 skip 必须显式登记（含出现次数），登记不得腐烂，且「仅 CI 时跳过」的位点必须**真的在读 CI 环境变量**——AST 判据，docstring/说明文本里的「CI」不算；扫描面用 `git ls-files` 并配非空 + 下限）+ **`test_apkg_import` 16 + `test_render_options` 8**（Anki `.apkg` 导入：**导出→导入往返**逐字段对上 / 按字段名取值（顺序无关）/ 通用英文牌组 / 记忆卡包逐条跳过并说明 / 压缩炸弹与条数上限 / **导入不绕过闸门**（间谍断言）；选项渲染：**剥已有字母前缀**（曾渲染成 `A. A. 甲`）/ 多种分隔符 / 幂等 / 不误剥正文）+ **`test_notebook_md` 17**（复盘笔记：小节在场与顺序 / 数字等于输入 / 被排除条目如实标出 / 空态可读 / 表格转义 / **AST 结构守卫**「不得读题目答案字段」「不得依赖 LLM」/ 端点端到端**不夹带题干与答案**）+ 阶段 0 题样导入器 `test_stage0_export_cases` 14 + 题样预检器 `test_stage0_cases_check` 37 + 闸门预检 `test_stage0_eval_preflight` 12 + `test_stage0_cases_check` 37 / **守卫自锁防线** `test_explain_guard_no_selflock` 6 / **时序赌注守卫** `test_no_sleep_gambling` 5（AST 禁测试体顶层 `time.sleep` 赌调度；含「不许静默跳过」与「扫描面 == 目录」）/ **发布五件套一致性** `test_release_artifacts` 16 / **文档守卫** `test_docs_coverage` 62（含活跃文档的仓库内路径完整性；含元守卫「本文件不许用 `pytest.skip` 掩盖前提不成立」，AST 判据；含**目录树扫描面**三守卫（README「仓库结构」代码块里的 `N 项（单元 N / 浏览器层 M）`与 `/api/errors/* · N 端点` 此前在**所有守卫扫描面外**，实测陈旧到 1052/22 而无人报警；现三口径各自对真身 `pytest --collect-only` + 元守卫钉住扫描面不塌缩）；含元守卫「活跃文档扫描面非空」（`_active_docs()` 非空 + `>= 10` + 与 `git ls-files docs/` 索引核对，扣掉设计上排除的 `archive/` `reviews/` `0.10.0-*`）——旧版扫描面塌缩时 parametrize 收集 `[NOTSET]` 且 **SKIP**（比假绿更隐蔽，AST 禁 `pytest.skip` 拦不住）+ **打包配置守卫** `test_check_package` 25（**spec 数据收集**：`datas` / `excludes` 改走 AST 而非正则——旧版 `r'excludes\s*=\s*\[(.*?)\]'` 会被条目内的方括号提前截断，且 `'"LICENSE"' in src` 这类全文子串断言「删掉数据行、注释里留字样」即可绕过；现锁「`datas` 声明的本地路径真实存在」「`medkit/` 下每个数据目录都必须被 `datas` 覆盖或进豁免表（双向）」+ `test_lint_gate` 15（含「ruff 必须声明在依赖里」与「必须装的步骤不得被跳过分支包住」；含元守卫「跳过开关扫描面非空」（`_verify_skip_switches()` 与主守卫同源 + ① 非空 ② `>= 2` ③ 契约 `SKIP_BROWSER`/`SKIP_MYPY` 必须在场）——旧版 `switches` 是该用例**三个检查的唯一入口**，掏空后三个检查一个都不跑却报「通过」（R27 实测 `rc=0` 恒绿））+ `test_v14_zero_cdn` 4（**零 CDN 静态闸门**：含元守卫「扫描面非空且未写窄」（① 非空 + `>= 10` ② 按扩展名分四类**各自**非空 ③ 与 `git ls-files medkit/web/ medkit/render/` 索引核对）——旧版只有 `assert TARGETS`（仅非空），实测三种写窄注入**全部恒绿**：`*.js→*.jsx`（前端 JS 完全不受检）、删 `render/*.py` 行、`*.css→*.scss`；R27 修复后三类写窄各红）+ `test_ci_gate_parity` 12（**CI 闸门守卫**：现场解析 `.github/workflows/ci.yml` 而非手写常量，双向覆盖「CI 步骤 → 本地有无对应」「CI 关键闸门 → 是否仍在场含 `--strict`」，并拦 `continue-on-error` / `|| true` / 整段 `|| echo` 软化；旧版 3 组注入——删 CI 的 ruff step、去掉 CI 的 `--strict`、删整个 package job——**全部恒绿**）+ `test_v15_frontend_split` 5（含元守卫「扫描面不许为空」：旧版 `_immediate_calls` 只认带括号调用、且把注释行与函数内局部声明当顶层——漏检 4 种回调形态 + 3 处假红，已修为标识符引用扫描 + 顶层作用域判定 + 注释状态机）+ **二轮审计 W1/W2/W5/W7/W8 守卫**：`test_atomic_json_writes` 6（**原子写**：AST 判 slices.json/stage.json 等改走 `write_json_atomic`；注入回退 `write_text` 即红）+ `test_gate1_unverified` 9（**门禁① 三态**：失败分支不得静默置空列表，必须记入 `unverified` 并落 meta + 产物页 ⛔ 徽标）+ `test_ocr_restore_interrupt` 12（**OCR 重启任务态归一**：running/queued → interrupted，终态不改写，前端终态集合单源）+ `test_persistent_errors` 6（**持久错误清单**：落盘 JSONL + 模拟重启可查 + 去重 + 裁剪 + 脱敏 + 损坏行容错）+ `test_redact_single_source` 12（**脱敏单源**：AST 禁本地模式表，正面断言 mr-/JWT/Bearer/登记密钥均被掩码；实测修出 redact(limit=0) 返回空串与 Bearer 后 token 漏掩码两处真缺陷） + `test_create_project_async` 5（**W6 建项目不阻塞事件循环**：AST 判 async + await asyncio.to_thread 包住 create_project_record）+ `test_endpoint_ssrf_guard` 19（**W4 端点安全**：拦云元数据/链路本地；且不误伤 Ollama 127.0.0.1:11434 与内网 10.x/192.168.x——产品明确支持公司网关，一刀切封私网会把正当用法封死；结构判三处调用点真调了）+ `test_w9_w11_asset_guards` 6（**W9 页数闸失败留痕**「没量到」不得等同「没超限」/ **W11 删除预演** dry_run=1 只回报不 unlink，配真删对照）+ `test_usage_budget_guard` 7（**W10 token 硬上限**：越线抛 BudgetExceeded；默认 0=不限制；AST 判 run_project 真把配置传进 usage.activate） + `test_gate1_unverified` 的姊妹项 **W12 闸门探针**（`test_smoke.py::test_project_ratio_validation` 由「抛了 400」收紧为「**是配比这一闸**拦的」——`routers.projects._GATE_PROBE[0] == "ratio"`；0 次执行 ⇒ 探针 None ⇒ 红。该用例此前**长期假绿**：直接调 async handler 只拿到从未运行的协程，`except HTTPException` 因此 0 次执行仍计 PASS；三向注入（删探针调用点 / 回退不 await / 探针挪到更早闸门）均红） + `test_traversal_defense` 38（路由层与 core 层路径穿越防线：攻击向量表单一来源、`_safe_pid` 两层结构在场、`_safe_sid` 调用点真调了、两份白名单跨层一致）+ `test_r8w_p2_privacy` 12（POSIX 权限收紧分支现在**在 Windows 上也真跑**，不再是静默 skip） + **公式/算法数值守卫** `test_s2_refactor` 的 `test_cost_formula_matches_hand_computed_document`（手算常量锁成本公式，旧版「端点 == estimate_run」是恒真：改坏 `CHARS_PER_TOKEN` 也绿） + `test_s3_apkg` 的 `test_stable_id_algorithm_is_pinned_by_golden_values`（黄金值锁 apkg deck/model id 哈希算法，换 sha256→md5 会让历史用户重导重复建卡）/ **运维脚本破坏性安全** `test_recover_user_data` 11 + `test_rollback_json_track` 7（这两个脚本**直接写/移用户真实库**，此前零覆盖；现锁「默认 dry-run」「移走≠删除」「绝不覆盖活文件」「污染判定保守」「先快照再写库」；2026-10-01 把其中两条**文本位置判据改为 AST**——实测旧版对「变量改名 `live→conn`」「加一句含 "pass" 的注释」两种**等价改写假红**，现按调用形态/`try` 结构判定）+ **D6 双轨退役迁移演练** `test_json_track_retirement` 3（**数据级往返**：JSON 轨 → 迁移 → SQLite 轨 → 回滚 → JSON 轨。逐表逐行比对 `data` 列 == 原记录 / 二次导入全 `skip(no file)` 且行数不变 / 回滚后 `medkit.db` 被移走且 5 个 JSON **换行归一后逐字节相等** / 轨判定回落 JSON；含**防空转**判据：夹具表集合 == `db.IMPORT_MAP`（结构相等非字面数字）+ 每表非空）+ `test_db` 的 `test_reset_conn_releases_db_file_handle`（**句柄释放**：`reset_conn()` 后必须能给 `medkit.db` 改名——取**真实可观测行为**而非「源码有没有调 close」；旧实现只丢引用、连接在引用环里要等循环 GC，Windows 上句柄不放 ⇒ 回滚移不走 db）+ **浏览器层 82 项**（Playwright，独立进程；含 `test_photo_intake` 14：拍照录入卡片的闸门前置 / 能力横幅两种口径 / SSE 阶段与增量渲染 / 失败帧逐条展示 / **卡片只属于错题本视图**（曾错插到视图外导致每个子视图常驻）/ 刷新后停在错题本时能力横幅自加载 / **同视图有且仅有一个图片入口** / 旧入口改为引导 / 窄屏不溢出 / **XSS 守卫：识别结果（外部来源文本）必须转义后才进 innerHTML**——载荷按上下文选（`<textarea>` 是 RCDATA 需 `</textarea>` 闭合逃逸、`<input value>` 是属性上下文需 `"` 逃逸），只喂 `<img onerror>` 是**测不到**的）+ ruff 干净 + **mypy 基线 0 error**（84 源文件）+ **总闸 7 步覆 CI 阻断项**（mypy/eslint/pip-audit 齐）+ eslint `--max-warnings 0` + PyInstaller exe 冒烟

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
