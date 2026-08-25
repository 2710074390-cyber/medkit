# MedKit · 医学题库工坊（MedAgentWork 桌面版）

> 授人以渔：医学生自备教材 + 教师重点（+ 可选自备真题 / 网络检索），自选服务商与 API Key，
> 本地一键生成**全新的**题库 / 押题卷 / 复习手册。不携带、不内置任何旧产物质料。

## 运行（开发模式）

```powershell
# 依赖（国内镜像）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
# 启动
python run_medkit.py        # 或双击 start.bat
# 浏览器打开 http://127.0.0.1:4880（4880 被占时自动回退 4881~4889）
```

## 绿色免安装版（P3）

```powershell
pip install pyinstaller -i https://mirrors.aliyun.com/pypi/simple/
pack\build.bat              # 或：python -m PyInstaller --noconfirm --clean medkit.spec
```

- 产物：`dist\MedKit\`（约 87 MB，含 `MedKit.exe` + `_internal\`）
- 使用：**复制整个 `MedKit` 文件夹**到任意位置 → 双击 `MedKit.exe` → 自动打开浏览器
- 自带资源：静态前端 / 四个提示词（MedGen·MedQC·MedFix·MedReview）/ 示例素材（打包路径已验证）；**无需安装 Python**

## 安装包（Inno Setup，可选）

```powershell
# Inno Setup 7.1（已装则跳过；注意 jrsoftware 官网/Aliyun 源不可用时走 ghproxy）
# 下载：https://ghproxy.net/https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe
# 安装后 ISCC.exe 位于 %LOCALAPPDATA%\Programs\Inno\ISCC.exe
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
│   ├── core/                   # config / providers / llm / cost / usage / extract / slice / quota / mineru(OCR)
│   ├── agents/                 # medgen / medqc / medfix / medreview
│   ├── prompts/                # 从 MedAgentWork Prompt版本/ 模板化迁移
│   ├── gates/                  # options_check / bloom_check / trace_check / dedup_check
│   └── web/                    # 零 CDN 单页 UI
└── tests/                      # test_smoke / test_pipeline_offline / test_api（TestClient）
```

## 已实现功能（v0.5.0 · S0~S3 全部落地）

- **服务商 BYOK**：DeepSeek / 智谱 GLM / 通义千问 预置（卡片带官网注册跳转）+ 自定义 OpenAI 兼容端点；双模型档（下拉选择，获取模型列表后默认选最新，支持手动输入）；测试连接（30s 超时）；**保存配置空 Key = 保留原值**；**Key 落盘 DPAPI 加密**（Windows，ctypes 零依赖；旧明文自动升级）
- **素材解析**：PDF(文本层)/DOCX/MD/TXT/图片；章节切片；教师重点词频配额加权；线程池执行不阻塞
- **素材库复用（S3）**：解析结果可「保存为素材会话」（`~/.medkit/sessions/`），**跨项目复用**；多个会话**合并载入为教材**（多教材合并出题，quota 跨 session 按章加权）；项目**配置模板**一键存/取（科目/题型配比/Bloom/旋钮/附加要求）
- **扫描件 OCR（MinerU · 任务制）**：精准 API（≤200MB/≤600 页，每日 2000 页高优先级额度，2026-08 官方现行）/ 免 Token 轻量 API（≤10MB）；进度轮询 + 取消 + 自动加入输入；**UI 明示上传云端**
- **出题管线（五阶段，后台线程 + 实时日志）**：
  - ① MedGen：按切片配额并发（≤3）出题（A1/A2/X；**S3：A3/A4 案例组题（3~5 子题共用案例题干）+ B1 真组题（共享选项组）**）；HC 命题规则 + [源:切片] 溯源；**题量不足自动补足 ≤2 轮 + 超发截断**；**全文仅在 system 注入一次**（输入成本约 -40%）；模板占位符一次性替换（防教材文本二次注入）
  - ② 门禁①：选项质量（R 规则子集）+ Bloom 30/40/25/5 + 溯源回查 + **n-gram 查重（Jaccard>0.8 → MedFix 改写；案例组/选项组内跳过）**，自动修复 ≤2 轮
  - ③ MedQC：LLM-as-judge 并行分批质检，score + gate_decision（浮点/None score 容错）
  - ④ MedFix：按 issue 定向修复（**合并策略保留溯源/案例/组结构字段**）
  - ⑤ MedReview：分层复习手册（考点速记/易混淆/临床路径/数值速查/背诵清单）
  - ⑥ 渲染：题库 MD+HTML（**案例/选项组按组折叠**）/ **交互押题卷（X 型 checkbox+集合判分 / localStorage 续答 / 答题卡 / 计时断点恢复 / 错题重练 / 案例组分组呈现+分组判分 / 打印）** / 复习手册 MD+HTML / **Anki 导出（.txt + S3 .apkg 真包：项目名稳定哈希，标准卡+X 型自评卡，标签=题型/Bloom/章节）**
  - **渲染前终检（D2）**：修复轮用尽仍超限/缺字段的题剔除出产物 + 写入人工复核清单，绝不强行渲染
- **长任务体验（U1/U2/U3/I1）**：**管线可取消**（停止按钮，保留断点）+ **断点续跑**（逐切片 checkpoint）+ 三线程并发 + 六阶段 stepper + 百分比进度
- **成本透明（U5）**：解析/创建前显示「预计 X 万 token · 约 ¥Y（参考价，以官网为准）」——**公式前后端单源**（`/api/cost/estimate` ← `core/cost.py`）；跑完写实际 usage + 折算成本到项目 meta；run/trial/regen 按次上下文独立记账
- **安全加固**：Host/Origin 校验中间件（含 IPv6 `[::1]`）；pid 路径消毒（含预设删除）；损坏 meta.json 容错（422）；产物 HTML 全量转义 + 复习手册白名单消毒（href 仅 http/https）；`javascript:` 剥纯文本；Key 不进 URL；配置深拷贝防默认值污染
- **工程化（S2）**：`routers/*` 九模块 + `state.py`；lifespan；统一异常体（LLM/Search/MinerU/PipelineError）；`~/.medkit/logs/` RotatingFileHandler；**版本单源** `medkit/__init__.py`；`verify.cmd` 一键验证 + GitHub CI 工作流
- **引导与体验**：素材要求卡 / 一键示例 / 体检警告 / 就绪清单 / 成本预估；**拖拽上传 + 文件清单可移除**；**hash 路由（刷新保持 tab）**；配比实时合计；亮/暗主题切换（含产物页，记忆偏好，隐私模式容错）；SVG 图标；toast 堆叠；自定义删除确认；轮询失败 3 次才停；全局 onerror/unhandledrejection 兜底
- **质量**：**89 项 pytest**（冒烟 / 离线管线含断点续跑·取消·案例组 / API 层 TestClient / S1 回归四件套 / S2 重构契约 / S3 apkg·案例结构·素材会话）+ ruff 干净 + PyInstaller exe 冒烟

## 服务商与模型（2026-08 官方信息核查版）

| 服务商 | 默认模型（2026-08 核查） | 联网搜索 | 单价参考（元/百万 token，估算，以官网为准） |
|---|---|---|---|
| DeepSeek | `deepseek-v4-flash`（官方现行：v4-flash / v4-pro / v4-flash-vision-exp；1M 上下文） | ✅ **自带**（Responses API `web_search` 工具） | 3.0 / 9.0（高峰；空闲减半，缓存命中 0.05~0.30） |
| 智谱 GLM | `glm-5.3`（现行主力；另有 5-Turbo / 4.7） | ✅ **自带**（Web Search API，检索按次计费） | 8.0 / 28.0（缓存命中 2.0） |
| 通义千问 | `qwen-plus`（现行代际至 Qwen3.8 Max/Plus/Flash；qwen3-max 系列已支持联网） | ✅ **自带**（enable_search；qwen3-max 系列及以上） | 2.4 / 9.6（百炼华北2北京） |
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

## 开发里程碑（见 docs/桌面版实现方案_细化设计_v1.md）

- ✅ P1 迭代1：设置页 + 素材解析预览 + 课题创建
- ✅ P1 迭代2：五阶段管线 + 产物渲染（离线全链路测试通过）
- ✅ 全面审查修复（P0 全部 + P1 长任务闭环 + P2 路线图：Anki / 押题卷练习化 / 查重门禁 / 主题与图标 / 端口回退 / ruff+TestClient）
- ✅ v0.5 S0~S3：安全网（git 基线 · verify.cmd）→ 正确性修复+数据刷新 → 工程化重构 → .apkg 导出 / A3·A4 案例题 / B1 组题 / 素材库复用
- 🔲 后续可选：网络检索更多后端、自备真题引用配额滑杆、跨项目错题本 + 遗忘曲线复习计划
