# 自研 MCP Server 方案可行性调研报告

> 用途：为 MedKit（本地医学学习软件，Python/FastAPI + SQLite，本地 4880 端口 HTTP 服务，含错题本 / 出题 / 元认知统计）的 A/B 方案决策提供中立、可查证的依据。
>
> - **方案 A**：给 MedKit 自研一个 MCP Server，把它接入 Cherry Studio 等 MCP 客户端。
> - **方案 B**：不写代码，纯用 Cherry Studio 的 Agent + 知识库 + Skills。
>
> 调研时间：2026-10-02。所有结论均标注来源 URL；可信度用 `【已查证】`（官方文档 / 一手仓库）与 `【推测/宣传】`（二手博客 / 厂商软文 / 无法交叉验证）区分。

---

## 0. 一句话结论（先给决策层）

- **桌面端（Windows 本机）方案 A 完全可行且工作量小**：MedKit 已经有本地 HTTP API，自研 MCP Server 本质是给已有 API 套一层 MCP 协议外壳（FastMCP 装饰器级），不需要重写业务逻辑。`【已查证】`
- **移动端想用上 MedKit 数据，方案 A 绕不开「远程 HTTP + 鉴权」**：手机跑不了本地 stdio 进程，必须把 MedKit 通过 Streamable HTTP + Bearer Token 暴露出去，这才是真正的代价与隐私风险所在。`【已查证】`
- **方案 B（纯知识库 + Skills）拿不到「可计算、可写回、统计严谨」的错题本与元认知数据**，适合做被动知识问答，不适合做"基于真实错题统计的自适应复习"。
- **建议**：若核心诉求是「让 AI 基于我真实的错题/统计做交互式复习」→ 选 A（先做桌面 stdio 版，远程 HTTP 版作为可选第二阶段）；若只是「把教材/笔记丢进去做问答」→ B 已够用。

---

## 1. MCP Server 的成熟架构模式

### 1.1 协议层：三种"传输"的真实定位

需要先澄清一个常见误解：MCP 现行规范里**标准传输只有两种**，所谓"三种"是把已废弃的旧版也算上。

| 传输 | 规范状态 | 架构特征 | 适用场景 |
|---|---|---|---|
| **stdio** | 现行标准（推荐客户端尽量支持） | 客户端拉起子进程，JSON-RPC 走标准输入/输出，换行分隔；**无网络开销、同机最快** | 本地集成、命令行、桌面客户端（Claude Desktop / Cherry Studio 桌面） |
| **Streamable HTTP** | 现行标准（2025-03-26 起取代旧版） | 客户端向**单一 MCP 端点**发 HTTP POST；回复可以是一个 JSON 对象，或一个请求级 SSE 流；天然支持远程 | 远程服务器、跨机/跨端、需要标准 HTTP 鉴权的场景 |
| **HTTP + SSE（旧版）** | **已废弃（deprecated）** | 两个端点（SSE + POST） | 旧客户端兼容用；新实现不应再采用，官方明确建议迁移到 Streamable HTTP |

`【已查证】` 以上事实来自官方规范：
- Transports 总览（现行规范仅 stdio + Streamable HTTP 两种标准传输）：https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- Streamable HTTP 页明确写明旧 HTTP+SSE 已废弃、"新实现不应采用、应迁移"：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- 架构概览（stdio 同机零网络开销；Streamable HTTP 支持远程、支持 bearer token / API key / 自定义 header，并推荐用 OAuth 取 token）：https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture
- 旧版（2024-11-05）仍写着"HTTP with SSE"，可作为对比佐证：https://modelcontextprotocol.io/specification/2024-11-05/basic/transports

> 对 MedKit 的直接含义：
> - **桌面端**：用 stdio 最省事——Cherry Studio 直接拉起 MCP 进程，无需开端口、无需鉴权。
> - **移动端/远程**：必须走 Streamable HTTP（旧 SSE 端点别新建），且要带鉴权。

### 1.2 实现框架现状（成熟度 / 维护状态 / 版本）

`【已查证】`

| 框架 | 维护方 | 现状（截至 2026-10） | 来源 |
|---|---|---|---|
| **`mcp`（官方 Python SDK）** | modelcontextprotocol 官方 | 持续发布；PyPI 上 `mcp 1.17.0`（2025-10-10 发布，要求 Python ≥3.10）。注意 PyPI 页仍标注 maturity 为 "4 - Beta"。 | https://pypi.org/project/mcp/1.17.0/ |
| **FastMCP（独立项目，后并入官方 SDK 路线）** | 社区驱动，已并入官方 Python SDK 体系 | 非常活跃；2.0（2025-04）后成为"全生态平台"，官方宣布 **FastMCP 1.0 将并入官方 MCP Python SDK**；2026-05 已到 v3.3.x。是 Python 侧事实标准、上手最快的高层框架。 | https://gofastmcp.com/v2/updates ；https://gofastmcp.com/updates |
| **官方 SDK v2（对应 2026-07-28 规范 RC）** | 官方 | 2026-06 发布 Beta SDK；Python 侧 `FastMCP` 改名 `MCPServer`、装饰器 API 保留；HTTP app 支持无状态（stateless）。**说明协议仍在快速演进、有 breaking change**。 | https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/ |
| **TypeScript / Node SDK** | 官方 | `@modelcontextprotocol/sdk`，生态最丰富（官方 servers 仓库与多数生产 server 用 TS）。 | https://github.com/modelcontextprotocol/servers |

**成熟度判断**：Python 侧（FastMCP）对"包一层已有 HTTP API"这类需求完全够用、文档齐全；但要接受**协议仍在快速迭代**（2025-11-25、2026-07-28 连续出新规范，SDK 有 breaking change）。对个人项目，建议锁定一个已验证可用的 SDK 小版本，不要盲目追新。`【推测/宣传】`中"production-ready、开箱即用"的说法普遍偏乐观，**官方 Python SDK 至今仍自标 Beta** 这一点要纳入风险。

**轻量实现思路**（对 MedKit 最关键）：不需要重写业务。MedKit 的 FastAPI 已经在 4880 暴露了出题、错题、统计接口；MCP Server 只需要把这些 HTTP 端点"翻译"成 MCP tool——每个 tool 函数内部就是一次 `requests`/`httpx` 调用 `localhost:4880/...`。这是业界把已有 REST 服务接 MCP 的标准做法（见第 2 节 PubMed/Anki 项目均为"桥接已有 API"模式）。

### 1.3 工具（tool）设计规范

`【已查证】` 官方最佳实践：
- 提供清晰、描述性的**工具名与描述**；
- 用详细的 **JSON Schema** 定义参数；
- 在描述里给**示例**，告诉模型怎么用；
- 实现正确的**错误处理与参数校验**；
- 长操作用进度上报。
- 来源：https://modelcontextprotocol.io/legacy/concepts/tools

社区共识（`【推测/宣传】`为主，但被多篇独立指南交叉印证，可作为工程经验采纳）：
- **命名**：`snake_case`、动词开头（`get_due_cards`、`add_wrong_question`，而非名词）；文档首句用一句话说清这个工具干嘛——这句就是模型决定要不要调它的主要依据。来源：https://aiclaude.codeguides.io/mcp-core-concepts/defining-callable-tools-in-an-mcp-server/
- **参数**：尽量用扁平的基本类型与 enum，避免复杂嵌套对象；只把真正必填的标 `required`；固定取值集合用 `enum` 防幻觉。来源：https://goclaw.sh/blog/mcp-server-best-practices ；https://www.raftlabs.com/blog/mcp-server-development-guide
- **错误处理（关键细节）**：工具执行错误建议放在 result 里以 `isError: true` + 可读消息返回，**不要**抛成 JSON-RPC 协议层错误——因为协议层错误模型看不到具体内容，无法自我纠正重试。来源：https://kansei-link.com/en/insights/mcp-tool-schema-design-guide-2026.html ；https://modelcontextprotocol.io/specification/draft/server/tools
- AWS Labs 官方设计指南：动词-名词命名、用 try/except、分类 client/server 错误。来源：https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md

> 对 MedKit：错题本/出题/统计天然是"动词+资源"（list_wrong、add_wrong、get_due_quiz、record_answer、get_meta_stats），粒度建议**一个 tool 对应一个已有 HTTP 端点**，不要把多个操作塞进一个 tool。

### 1.4 认证与部署模式对比

`【已查证】` 鉴权规范（现行）：
- MCP 跟随 **OAuth 2.1** 的资源请求方式：客户端每个 HTTP 请求都必须带 `Authorization: Bearer <access-token>`；**access token 禁止放在 URL query string**；失败返回 `401` + `WWW-Authenticate`。
- 支持动态客户端注册（DCR）。
- 来源：https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization ；教程 https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization

| 部署形态 | 传输 | 鉴权 | 适用 | 对 MedKit 的意义 |
|---|---|---|---|---|
| **本地 stdio（桌面）** | stdio | 无需（同机子进程） | 个人桌面首选 | 桌面端零鉴权成本，Cherry Studio 拉起即用 |
| **局域网 HTTP** | Streamable HTTP | 可加 Bearer Token | 家里多设备 | 手机连家里电脑时的折中 |
| **轻量云 / VPS 常驻进程** | Streamable HTTP + HTTPS | Bearer Token / OAuth | 外出用手机访问 | 需把数据同步/复制到云上，隐私边界要重新画 |
| **Serverless 函数** | Streamable HTTP（无状态） | Bearer Token | 纯无状态、读多写少 | MedKit 是 SQLite 本地有状态，**不适合无状态 Serverless**（除非把 SQLite 换成云库） |
| **常驻进程（本机 + 内网穿透）** | Streamable HTTP | Bearer Token | 用 ngrok 类隧道把本机暴露出去 | Anki 社区就是这么干的（见 2.1），但等同把本地数据库暴露到公网 |

> **现状澄清**：OAuth 全流程（授权码 + DCR）是规范推荐的"正规军"路线；但对个人单用户场景，业界普遍直接用 **静态 API Key / Bearer Token 放自定义 header**（Cherry Studio 客户端支持自定义 headers，见 1.5 与第 4 节）。规范本身也明确 Streamable HTTP "supports standard HTTP authentication methods including bearer tokens, API keys, and custom headers"。`【已查证】`

### 1.5 数据持久化惯例

`【已查证】/【社区共识】`
- MCP Server 本身**不规定持久化方式**；惯例是"server 自己管存储"。官方 memory reference server 用**本地 JSONL 文件 + 知识图谱**（entities / relations / observations）。来源：https://github.com/modelcontextprotocol/servers/tree/main/src/memory
- 社区成熟做法普遍直接用 **SQLite**：如 `@memorygraph/server` 用 `better-sqlite3 + WAL` 并发读、`sqlite-vec` 做向量检索；`memdb` 用 Node 原生 `node:sqlite` + FTS5 全文检索。来源：https://www.npmjs.com/package/@memorygraph/server ；https://himcp.ai/server/memdb
- 对 MedKit 这是**最顺的一点**：MedKit 本来就是 SQLite，MCP Server 直接读写同一个库（或同一套 HTTP API）即可，无需新建存储层。注意 SQLite 多进程读写要开 WAL 模式避免锁冲突。`【推测/工程经验】`

### 1.6 日志与排错惯例

`【社区共识，可采纳】`
- stdio 传输下，**日志必须写到 stderr**（stdout 是协议通道，写日志会污染 JSON-RPC 帧）——这是 stdio MCP 最常见的踩坑点。`【工程经验，多篇部署文档隐含印证】`
- 工具内部用 try/except 捕获、带上下文记录异常；用 MCP context 的错误上报；给模型可读的错误消息（见 1.3）。来源：https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md
- 长操作用进度上报；参数严格按 JSON Schema 校验。来源：https://modelcontextprotocol.io/legacy/concepts/tools

---

## 2. 开源 MCP Server 项目调研（学习 / 闪卡 / 错题 / 记忆 / 医学类）

> 成熟度评级口径：★ 官方/高活跃可参考；☆ 社区项目、能跑但需自担维护风险。

### 2.1 Anki 类（与"错题本+闪卡"最像，最值得抄作业）

| 项目 | 核心机制 | 技术栈 | 成熟度 | 可借鉴点 | 来源 |
|---|---|---|---|---|---|
| **ankimcp / anki-mcp-server**（含 AnkiWeb 插件版） | 在 Anki 内部起 MCP server，桥接已有 Anki 牌组；支持建卡、按到期出题、评级、复习分析；细粒度权限、自动生命周期 | Node/TS，npm 包；本地 SSE `localhost:4473` | ☆ 活跃、有商业化官网与文档，但属社区项目 | **"在已有应用内嵌一个 MCP server"正是 MedKit 该走的路**；Web 模式需 ngrok 隧道把本机暴露，是移动端远程访问的现成参考 | https://ankimcp.com/docs ；https://ankimcp.ai/docs/installation/web/ |
| **mcp-server-anki**（npm） | 通过 AnkiConnect 插件（Anki 端 HTTP）桥接，MCP 再包一层 | Node ≥16 | ☆ | 经典"已有 REST(AnkiConnect) → MCP 桥"模式，与 MedKit"已有 4880 HTTP → MCP 桥"完全同构 | https://www.npmjs.com/package/mcp-server-anki |
| **anki-connect-server**（PyPI） | 无 Anki 桌面也能跑，直接读 `.anki21` 收藏库；提供 MCP 工具 | Python，`uvx anki-connect-server mcp` | ☆（v0.2.0，很早期） | **Python 侧参考实现**：直接读 SQLite 收藏库暴露 MCP，与 MedKit 的 SQLite 架构最接近 | https://pypi.org/project/anki-connect-server/ |

**可复用性判断**：这些都不能直接装来管 MedKit（它们绑死 Anki 格式），但**架构模式可直接照搬**——尤其是"已有本地 HTTP/SQLite 数据 → 包一层 MCP tool"。`【已查证】`

### 2.2 记忆 / 知识图谱类（持久化做法的范本）

| 项目 | 核心机制 | 技术栈 | 成熟度 | 可借鉴点 | 来源 |
|---|---|---|---|---|---|
| **官方 Memory reference server**（`@modelcontextprotocol/server-memory`） | 知识图谱持久记忆：entities / relations / observations，本地 JSONL 存储；既是可用 server 也是"教你自己造 server 的模板" | TS | ★ 官方 reference | 官方钦定的"怎么组织持久化记忆"范本；说明 MCP 侧做持久记忆是被官方背书的一等场景 | https://github.com/modelcontextprotocol/servers/tree/main/src/memory ；https://blog.mcpservers.org/posts/mcp-memory-servers |
| **mcp-memory-server（SQLite 衍生版）** | 实体/观察/关系存 SQLite，带向量语义检索与时间版本化、游标分页 | SQLite | ☆ | SQLite 持久化 + 向量检索的现代做法 | https://himcp.ai/server/mcp-memory-server-rir |
| **@memorygraph/server** | MCP SDK + better-sqlite3(WAL) + sqlite-vec 向量检索 + 本地 embedding（all-MiniLM），零外部依赖 | TS/Node | ☆ | **单文件 SQLite + 本地向量、零运维**的部署范本，适合个人项目抄 | https://www.npmjs.com/package/@memorygraph/server |

### 2.3 医学 / 文献类

| 项目 | 核心机制 | 技术栈 | 成熟度 | 可借鉴点 | 来源 |
|---|---|---|---|---|---|
| **@cyanheads/pubmed-mcp-server** | 通过 NCBI E-utilities 把 PubMed/PubMed Central 文献检索、取全文、分析暴露成 MCP 工具；支持 stdio 与 HTTP，可配 NCBI_API_KEY | TypeScript（Bun/Node），Apache-2.0 | ☆ 较活跃（v2.10.x），但 star 数很小 | 医学文献检索 MCP 的成熟模板；MedKit 可**把它作为"外部知识源"接入**（错题里不懂的机制让 AI 现查 PubMed），而不是自己重写 | https://github.com/cyanheads/pubmed-mcp-server ；https://chat.mcp.so/server/pubmed-mcp-server/cyanheads |
| **medadapt-content-server** | 面向 Claude Desktop 的医学学习 server，聚合 PubMed、NCBI Bookshelf 与用户文档，做 AI 辅助医学学习 | Python，`python content_server.py` | ☆ 个人项目、早期 | **"医学学习 + MCP"的直接先例**：证明该场景成立；但其数据是"拉外部文献"，和 MedKit"管个人错题/统计"互补而非竞争 | https://github.com/ryoureddy/medadapt-content-server |

### 2.4 学习 / 出题 / 错题类

`【调研结论】` 公开生态里**没有找到专门做"西医综合错题本 + 元认知统计"的成熟 MCP server**；最接近的是 Anki 系（2.1，做闪卡/到期复习）和官方 memory 系（2.2，做持久记忆）。这意味着：
- **没有可直接安装复用的现成 MedKit MCP**——方案 A 必须自研，但自研是"抄 Anki 桥接模式"而非从零发明；
- 反过来也说明：这个细分场景**尚无拥挤的开源轮子**，自研的边际价值更真实。`【推测，基于"未检索到"的负面证据，非绝对结论】`

---

## 3. MCP 生态与商业采用现状

### 3.1 官方 Registry 与客户端支持广度

`【已查证】`
- **官方 MCP Registry** 已上线（`registry.modelcontextprotocol.io`），支持发布/收录 server；GitHub 也在 2025-09-18 推出 **GitHub MCP Registry**，可在 VS Code 内一键安装、按 GitHub star 与社区活跃度排序。来源：https://github.blog/ai-and-ml/github-copilot/meet-the-github-mcp-registry-the-fastest-way-to-discover-mcp-servers/ ；发布流程 https://modelcontextprotocol.io/registry/quickstart
- **官方客户端清单**（clients.md）：Claude.ai、Claude Desktop、Cursor、VS Code、Cline 等均为官方收录客户端，支持 tools/resources/prompts，部分支持 OAuth。来源：https://modelcontextprotocol.io/clients.md
- **客户端支持广度（第三方横向对比，2026）**：Claude Desktop / Claude Code 为一等公民（协议发明者）；Cursor、Windsurf、VS Code+Copilot 原生强支持；**Cherry Studio 与 ChatGPT 一样属于"支持但需开关/特定版本"那一档**。来源：https://contextbolt.com/blog/ai-tools-mcp-support/ ；https://mcpverdict.com/mcp/clients/
- **官方 servers 仓库规模**：第三方审计站称 `modelcontextprotocol/servers` 约 90k star。`【推测/二手，数量级可信但精确值以仓库页为准】` 来源：https://checkmcp.dev/best/most-popular-mcp-servers

**Cherry Studio 侧（来自 Cherry Studio 官方文档，2026-10 时效，委托人已核实）**：
- 桌面端 MCP 支持 stdio / SSE / Streamable HTTP，路径：设置 → MCP → 添加服务器，可绑定到指定 Agent；
- 内置 `@cherry/python`（Pyodide 沙盒）、`@cherry/memory`（本地知识图谱持久记忆）、`@cherry/fetch`、`@cherry/sequentialthinking` 等；
- **移动端 v0.1 公测版**支持智能体/插件/MCP，但**本地 stdio 进程无法在手机运行，移动端必须走远程 HTTP/SSE**；
- Cherry Studio 数据默认本地存储、无官方云同步、支持 WebDAV/S3 备份。
- 旁证（Cherry Studio 实际配置写法，含 `type: streamableHttp` + `Authorization: Bearer` header）：https://developer.mescius.com/mcp/docs/configure-your-ai-client/desktop-and-chat-clients/cherry-studio

### 3.2 教育 / 垂直行业采用（严格区分已查证 vs 宣传）

**已查证（官方一手来源）**：
- **Syracuse University** 部署 Claude 到全体师生，校方明确把"Anthropic 开放发布 MCP"列为选型理由之一。这是"高校在真实决策中认可 MCP 开放性"的一手案例。来源：https://claude.com/customers/syracuse
- **Claude for Education**（2025-04 发布）：Northeastern University（首个大学设计伙伴，约 5 万人、13 校区）、LSE、Champlain College 等校园级部署。**注意：这是"高校用 Claude"，不等于"高校在用 MCP server"**——二者不能混淆。来源：https://www.anthropic.com/news/introducing-claude-for-education

**宣传 / 二手转述（未交叉验证，仅供参考）**：
- 有行业博客称 EduBase 发布了 edtech 领域首批官方 MCP server 之一、淡江大学（TKU）做了社区 MCP server 自动化课程监控。`【推测/宣传】` 来源：https://www.digitalkin.com/en/learn/strategie-adoption-mcp —— **未在官方渠道找到对应一手公告，决策时不要当作已证实事实引用**。
- 有个人开发者做的 "eClass (UoA) MCP Server"（雅典大学 LMS 桥接），属个人项目而非机构官方采用。来源：https://skywork.ai/skypage/en/ai-academia-eclass-mcp-server/1981910943517478912

**结论**：MCP 在**教育垂直行业的"生产级、机构级"落地案例目前仍很少且多为厂商/个人宣传**；真正扎实的信号是"协议本身已被主流客户端广泛支持、被高校在选型中认可其开放性"，而不是"已有大量成熟教育 MCP 产品"。

### 3.3 生态成熟度信号

`【已查证】`
- 协议版本在持续快速演进：2024-11-05 → 2025-03-26 → 2025-06-18 → 2025-11-25 → 2026-07-28（RC）。来源：https://gofastmcp.com/more/faq ；https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/
- 企业侧：GitHub、Microsoft(Playwright)、AWS、Google 等均有官方/正式 MCP server 与工具包。来源：https://mcpserver.space/mcp/awesome-mcp-collection/
- **双刃剑**：生态热、客户端广 = 写一次 server 能接多个客户端；但**版本迭代快、官方 Python SDK 仍标 Beta** = 个人项目要承担跟随升级的维护成本。

---

## 4. 可行性结论（供 A/B 决策）

### 4.1 方案 A 相对"纯配置方案 B"的**增量价值**

| 增量价值 | 说明 | 依据 |
|---|---|---|
| **真持久化（可写回）** | 方案 B 的知识库是"只读语料"，AI 答题/订正无法回写你的错题本；方案 A 让 AI 能 `add_wrong` / `record_answer`，错题与统计随复习**真实增长** | 官方 memory server 把"持久记忆"列为一等场景；MedKit 本就 SQLite |
| **可计算、统计严谨** | 元认知统计（掌握度、遗忘曲线、正确率）必须由代码确定性计算，不能靠 LLM 从文本"估"；MCP tool 直接查 SQLite 返回精确数字 | SQLite 持久化为业界惯例（1.5）；LLM 不擅长精确计数 |
| **多端数据统一** | 一份 SQLite 真相源，桌面/手机都通过同一个 MCP 读写，避免"知识库副本各自漂移" | Cherry Studio 无官方云同步（委托人已核实） |
| **低延迟本地调用** | stdio 同机零网络开销；即使 HTTP 也是 localhost:4880 级别延迟，远快于把整个题库塞进上下文 | 官方架构概览（stdio 无网络开销）https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture |
| **工具可组合** | MedKit 的"出题/错题/统计"工具可与 `@cherry/memory`、PubMed MCP 等**自由组合**，而不是被锁死在某一家知识库 | 跨客户端互通是 MCP 核心价值 |

### 4.2 方案 A 的**代价**

| 代价项 | 评估 | 依据/量级 |
|---|---|---|
| **开发量** | 小到中。MedKit 已有 FastAPI + SQLite，MCP Server ≈ 给现有 HTTP 端点套 FastMCP 工具函数（每个 tool 内部调 localhost:4880）。**不需要重写业务**。`【推测，量级估计：熟练者约 1–3 个工作日搭出可用版】` | Anki 桥接模式同构（2.1）；FastMCP 装饰器级开发 |
| **部署（桌面）** | 极简。stdio：Cherry Studio 配置里一条命令拉起，无需开端口、无鉴权 | stdio 规范（1.1/1.4） |
| **维护** | 中。协议快速演进、官方 Python SDK 仍 Beta，可能要跟随升级；建议**锁版本、最小化接口面** | SDK Beta 标注 + 频繁 breaking change（3.3） |
| **鉴权（仅远程时）** | 桌面 stdio 无需；远程 HTTP 需 Bearer Token（Cherry Studio 支持自定义 header 配置 `Authorization: Bearer`） | 授权规范（1.4）；Cherry 配置旁证（3.1） |

### 4.3 移动端连接远程 MCP 的可行性与风险（重点）

`【已查证】`
- **可行性**：技术上成立——手机上的 Cherry Studio 移动端走远程 **Streamable HTTP/SSE + Bearer Token** 即可调用。官方支持该鉴权方式，Cherry Studio 移动端支持 MCP。
- **硬约束**：手机**无法跑本地 stdio 进程**，因此移动端 = 必须远程暴露，没有"本地零配置"选项。
- **主要风险**：
  1. **隐私风险（最高）**：错题本/元认知数据是高度个人化的学习画像（暴露你的薄弱考点、备考节奏）。一旦走远程，等于把本地数据库经公网暴露——必须 HTTPS + 强随机 Token + 最小权限（建议先只暴露读工具，写工具后开）。
  2. **暴露面**：Anki 社区远程方案依赖 ngrok 类隧道，意味着**你的本机对互联网可达**；哪怕有 Token，也扩大了攻击面。
  3. **数据边界被打破**：MedKit 数据本来"纯本地、无云同步"；远程 MCP 后，数据要经过公网（甚至云 VPS），隐私边界从"不出本机"变成"出本机"，这是与方案 B（数据同样本地）在隐私模型上的**本质差异**。
  4. **成熟度风险**：Cherry Studio 移动端还是 v0.1 公测，MCP 在移动端的形态（能否自定义 header、能否绑 Agent）需以实际版本验证，不要假设与桌面端完全一致。`【委托人已核实其支持形态】`

### 4.4 A/B 决策矩阵

| 维度 | 方案 A：自研 MCP Server | 方案 B：纯 Agent+知识库+Skills |
|---|---|---|
| 适合的核心诉求 | 基于**真实错题/统计**的交互式自适应复习、可写回、可计算 | 把教材/笔记/PDF 丢进去做**被动问答与讲解** |
| 错题本可写回 | ✅ 真实增长、跨端一致 | ❌ 知识库只读，订正无法回写 |
| 元认知统计精度 | ✅ 代码确定性计算 SQLite | ❌ LLM 估算，不可靠、会漂移 |
| 开发成本 | 小到中（套壳现有 API，约 1–3 天量级）`【推测】` | 零代码 |
| 桌面端可用度 | ✅ stdio 零配置即用 | ✅ 开箱即用 |
| 移动端可用度 | ⚠️ 可行但须远程 HTTP+Token，有隐私/暴露风险 | ⚠️ 知识库随端同步受限于 Cherry 无云同步 |
| 隐私边界 | 远程后数据出本机，需 HTTPS+Token+最小权限 | 数据留本地，边界清晰 |
| 维护成本 | 中（跟协议升级） | 低 |
| 外部知识扩展 | ✅ 可与 PubMed MCP 等组合 | ✅ 也可，但缺"个人数据"这一层 |
| 一句话定位 | **主动、可计算、可积累的个人学习引擎** | **被动、只读的教材问答助手** |

### 4.5 建议路径（中立建议，供取舍）

1. **先做桌面 stdio 版（方案 A 的最小闭环）**：给 MedKit 的 4880 API 套一层 FastMCP，桌面 Cherry Studio 直接用。成本低、隐私零暴露、立刻获得"AI 基于真实错题互动复习"的增量价值。
2. **远程/移动端列为第二阶段、且默认只读**：确有手机复习需求时，再加 Streamable HTTP + Bearer Token + HTTPS，先只暴露 `list_wrong / get_due_quiz / get_stats` 等读工具，写工具（add_wrong / record_answer）经评估后再开。
3. **方案 B 不冲突、可并存**：用 Cherry 知识库放教材/讲义做问答，用 MCP 管个人错题数据——两者互补，不必二选一。
4. **若上述第 1 步做完觉得"AI 帮不上"**：说明你的核心诉求其实是被动问答，那就退回方案 B，沉没成本仅套壳那 1–3 天。`【推测】`

---

## 5. 来源 URL 汇总

**协议与规范（一手）**
- Transports 总览：https://modelcontextprotocol.io/specification/2025-03-26/basic/transports
- Streamable HTTP（旧 SSE 已废弃）：https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http
- 架构概览（stdio / 鉴权）：https://modelcontextprotocol.io/docs/2026-07-28/learn/architecture
- 授权（OAuth 2.1 / Bearer / 不放 query string）：https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization
- 授权教程：https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/authorization
- 工具最佳实践：https://modelcontextprotocol.io/legacy/concepts/tools
- 工具错误处理（spec）：https://modelcontextprotocol.io/specification/draft/server/tools
- SDK Beta（2026-07-28）：https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/

**实现框架（一手）**
- mcp 官方 Python SDK（PyPI，1.17.0 / Beta）：https://pypi.org/project/mcp/1.17.0/
- FastMCP（并入官方 SDK / 更新日志）：https://gofastmcp.com/v2/updates ；https://gofastmcp.com/updates
- AWS Labs MCP 设计指南：https://github.com/awslabs/mcp/blob/main/DESIGN_GUIDELINES.md

**开源项目**
- 官方 Memory reference server：https://github.com/modelcontextprotocol/servers/tree/main/src/memory
- Memory 服务器综述：https://blog.mcpservers.org/posts/mcp-memory-servers
- @memorygraph/server（SQLite+WAL+向量）：https://www.npmjs.com/package/@memorygraph/server
- memdb（node:sqlite+FTS5）：https://himcp.ai/server/memdb
- mcp-memory-server（SQLite 衍生）：https://himcp.ai/server/mcp-memory-server-rir
- AnkiMCP（文档 / Web 模式 ngrok）：https://ankimcp.com/docs ；https://ankimcp.ai/docs/installation/web/
- mcp-server-anki（npm）：https://www.npmjs.com/package/mcp-server-anki
- anki-connect-server（PyPI，Python 读 SQLite）：https://pypi.org/project/anki-connect-server/
- @cyanheads/pubmed-mcp-server：https://github.com/cyanheads/pubmed-mcp-server ；https://chat.mcp.so/server/pubmed-mcp-server/cyanheads
- medadapt-content-server（医学学习）：https://github.com/ryoureddy/medadapt-content-server

**生态与采用**
- 官方客户端清单：https://modelcontextprotocol.io/clients.md
- GitHub MCP Registry 发布：https://github.blog/ai-and-ml/github-copilot/meet-the-github-mcp-registry-the-fastest-way-to-discover-mcp-servers/
- Registry 发布流程：https://modelcontextprotocol.io/registry/quickstart
- 客户端支持横向对比：https://contextbolt.com/blog/ai-tools-mcp-support/ ；https://mcpverdict.com/mcp/clients/
- servers 仓库 star（二手）：https://checkmcp.dev/best/most-popular-mcp-servers
- Syracuse University（已查证教育采用）：https://claude.com/customers/syracuse
- Claude for Education（已查证，注意≠用 MCP）：https://www.anthropic.com/news/introducing-claude-for-education
- 教育 MCP 宣传案例（未交叉验证，勿当事实）：https://www.digitalkin.com/en/learn/strategie-adoption-mcp
- Cherry Studio 配置旁证（streamableHttp + Bearer header）：https://developer.mescius.com/mcp/docs/configure-your-ai-client/desktop-and-chat-clients/cherry-studio

**工具设计社区经验（工程参考）**
- 工具命名/参数实践：https://aiclaude.codeguides.io/mcp-core-concepts/defining-callable-tools-in-an-mcp-server/
- 扁平参数/最佳实践：https://goclaw.sh/blog/mcp-server-best-practices
- 错误放 result(isError) 而非协议层：https://kansei-link.com/en/insights/mcp-tool-schema-design-guide-2026.html
- Schema 设计：https://www.raftlabs.com/blog/mcp-server-development-guide

---

### 可信度声明
- 标 `【已查证】` 的结论均来自上述官方文档或一手仓库；
- 标 `【推测/宣传】` 或 `【推测】` 的为二手博客、厂商文案或本报告基于经验的量级估计（如"1–3 天开发量"），不作为事实引用；
- "公开生态中未找到专门的西医综合错题本 MCP"为**负面检索结论**，反映调研时点（2026-10）的可见范围，不代表绝对不存在。
