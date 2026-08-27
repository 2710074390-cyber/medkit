# MedKit v0.7.0 设计方案：从「出题工坊」升级为「高效学习闭环」

> 日期:2026-08-27 · 状态:草案(待评审)
> 范围:不破坏现有出题管线,在其之上叠加 3 大新能力——**错题沉淀与掌握度建模、基于教材的知识讲解、提问式学习(Socratic)**;并补齐间隔复习计划。目标:学生在本机完成「出题→判错→归因→讲解→提问→间隔复习」的完整学习闭环。

> **本 Sprint 决策(2026-08-27 用户确认):**
> - 起步范围 = **M1(错题沉淀)+ M2(掌握度诊断)**,作为第一个闭环 MVP
> - 错题来源优先级 = ①本软件押题卷判错 ②拍题/图片 OCR ③手动粘贴/录入
> - 讲解/提问的 grounding 策略 = **教材为主 + 允许模型补充通用医学常识泛化**(非严格锁定切片)

---

## 0. 为何要改(用户诉求)与对标来源

当前 MedKit 的边界是「按重点+教材 → 一次性生成题库/押题卷/复习手册/Anki」。用户的诉求是把它变成**持续使用的高效学习工具**:不仅出题,还能——
1. 导入错题数据(来自自己押题卷的错题、外部刷题 App 导出、拍题/贴文本)
2. 结合教材、用大模型对「未掌握的知识点」进行讲解
3. 提问式学习(Socratic),不让模型直接给答案而是引导
4. 以最省成本的调度,让学生知道「接下来重点学什么」

改造前先在 GitHub / 公开资料做了一轮对标调研,以下方案与推荐直接借鉴的成果:

| 要借鉴的能力 | 对标项目 / 资料 | 结论 |
|---|---|---|
| 知识掌握度建模、主题级熟练度 | AMBOSS Knowledge Profile(IRT Rasch 模型,数据→主题熟练度→驱动自适应) | 不只看正确率,按"主题/技能"拆熟练度,并驱动"下一个学什么" |
| 概念状态机 + 苏格拉底提问 | Cogniloop(概念 `weak→shaky→solid→mastered`,5 类提问:解释/应用/对比/预测/追溯,每轮评分+弱点分析) | 直接采用其状态机与提问类型作为提问式学习骨架 |
| 错题 → AI 打标/同类题 | wttwins/wrong-notebook(AI 生成解析、知识点标签、同类题) | 错题结构化时用 LLM 抽标签/归因 |
| 教材切片引用溯源讲解 | RAGFlow / PrivateGPT / rag-pdf-chat(检索增强 + 来源引用) | 讲解必须引用教材原文位置,防幻觉 |
| 医学教材 RAG + 自测刷题 | side-FALL/med-kb-local(43 本教材,混合检索,智能问答+刷题+考点标注) | 交互形态与"讲解→考点标注"思路可参考 |
| 间隔复习调度 | Anki SM-2 / FSRS(py-fsrs / ts-fsrs) | 复习用轻量 SM-2 起步,预留 FSRS 升级 |
| 医学薄弱诊断 / 数据驾驶舱 | CollinGeorge/MCAT(逐科正确率+错题加权+间隔翻倍)、flutter_learning_analytics(掌握度/遗忘曲线/热力日历) | 驾驶舱可视化 + 错题加权复习 |

> 关键判断:本项目**不引入向量数据库**(成本与本地部署考量),因为教材切片 `slices.json` 是现成、已按章节/含引用的结构化文本——用它做"切片级检索"已足够支撑讲解与提问的落地(定义见 §4.2),参考 med-kb-local 的"教材切片 + 引用限制生成范围"思路,而非 RAGFlow 的全文索引。

---

## 1. 总体架构与数据流

```
                    ┌────────────────────────────────────────────────┐
                    │             MedKit (FastAPI + 本地 Web)         │
                    │                                                │
  出题管线(已有)      │ ①押题卷.html ──判错──┐                        │
  教材+slices.json ──▶│                     ▼                        │
                    │            ┌──────────────────┐               │
                    │            │  ~/.medkit/library/（新增，跨项目）│
                    │            │  mistakes.json    │               │
  外部错题导入 ──────▶│            │  knowledge.json   │──▶ 掌握度诊断  │
  (图片OCR/文本/CSV) │            │  review_queue.json│──▶ 复习调度    │
                    │            └────────┬─────────┘               │
                    │                     ▼                         │
                    │  教材切片 slices.json ──(切片级 grounding)──▶   │
                    │                     ▼                         │
                    │        MedExplain(讲解) / MedTutor(提问式)       │
                    │  —— 引用教材原文 + 状态机 + 判分，回写掌握度 ──     │
                    │                                                │
                    └────────────────────────────────────────────────┘
```

**一句话:** 新增一个跨项目、跨考试的学生个人学习库(`~/.medkit/library/`),它与已有"项目"解耦——错题/掌握度/复习计划都是长期个人资产,不属于某一次出题。所有"生成长文本"的步骤复用已有 BYOK 服务商与教材切片,成本可控。

---

## 2. 新增数据模型(`medkit/core/library.py`,无外部依赖,本地 JSON 原子写复用 `fsutil.write_json_atomic`)

三条核心记录,全部落在 `~/.medkit/library/`:

### 2.1 `mistakes.json` 错题记录
```json
{
  "mistake_id": "m_20260827_001",
  "source": "paper" | "import_text" | "import_ocr" | "import_file",
  "source_ref": {"pid": null, "question_id": "Q023", "file": null},
  "subject": "儿科学", "chapter": "呼吸系统", "topic": "支气管肺炎",
  "question": "…", "options": ["…"], "answer": "…", "analysis": "…",
  "user_answer": "C", "correct": false,
  "error_reason": "concept_gap" | "confusion" | "calculation" | "misread" | "reasoning",
  "know_tags": ["支气管肺炎", "首选抗生素"],
  "bloom": "应用",
  "created_at": "…", "last_tried": "…", "miss_count": 2,
  "learned": false,
  "mastery": "weak"            // weak / shaky / solid / mastered
}
```

### 2.2 `knowledge.json` 知识点掌握度
```json
{
  "kp_id": "kp_…", "name": "支气管肺炎首选治疗",
  "subject": "儿科学", "chapter": "呼吸系统",
  "mastery": "weak",                    // 状态机 weak→shaky→solid→mastered
  "score": 0.35,                        // 综合掌握分 0~1
  "attempts": 8, "correct": 4, "miss": 4,
  "recent_correct_rate": 0.30,          // 近 20 题正确率
  "last_tried": "…", "last_reviewed": "…",
  "priority": 0.92,                     // 推荐优先级(§4.3)
  "slices": ["s_1"],                    // 关联的教材切片 sid(可空)
  "mistakes": ["m_…"],                  // 关联错题
  "history": [{"t": "…", "delta": 0.1, "event": "explain"|"quiz"|"review"}]
}
```

### 2.3 `review_queue.json` 复习卡片(轻量 SM-2)
```json
{
  "card_id": "rk_…", "kp_id": "kp_…",
  "state": "new" | "learning" | "review" | "relearning",
  "interval": 1,          // 天
  "ease": 2.5,            // SM-2 易度因子
  "due": "2026-08-28",
  "review_log": [ {"t": "…", "quality": 4} ]
}
```

> 对齐说明:数据结构参考了 py-fsrs / Anki 的 `Card / ReviewLog / Scheduler / State` 四件套,但**初始实现用经典 SM-2**(零依赖、几行代码、够用),把 FSRS(ts-fsrs 可嵌入前端)列为可选项——符合"低成本起步、预留升级"的本项目取向。

---

## 3. Agent 层扩展(`medkit/agents/`,沿用现有 `get_client(role)` 注入模式)

新增 2 个 agent + 2 个 prompt 影子副本(md),复用 `llm.py / cost.py / presets.py` 的模板与成本框架:

### 3.1 `medexplain.py` — 教材讲解(MedExplain)
- 输入:一个知识点 + 命中切片文本(§4.2) + (可选)关联错题
- 输出:结构化讲解 —— **结论先行 → 机制 → 鉴别/易混 → 记忆锚点**,每段带 `【源: 章节·切片标题】` 引用溯源
- 提示词:复用并扩展 MedGen 的 KNOB 解析风格(difficulty / analysis_style / stem_style),讲解风格对齐 `explain`/`detailed`
- 幻觉控制:仅依据注入的切片文本生成;切片缺失 → 明确提示"该知识点教材未覆盖或需补充素材",不自由发挥

### 3.2 `medtutor.py` — 提问式学习(MedTutor,借鉴 Cogniloop)
- 一个知识点用一个**会话状态机**(本地存 `tutor_sessions.json`):
  - 概念状态:`weak → shaky → solid → mastered`
  - 五类提问轮换:`解释(explain) / 应用(apply) / 对比(contrast) / 预测(predict) / 追溯(trace)`
- 流程:
  1. 系统 prompt 锁定:**不直接给答案**,以引导式追问推进
  2. AI 依据切片教材出第一问(如"解释支气管肺炎首选抗生素的机制")
  3. 用户作答 → AI 判分 0~3 + 一句话差距分析 → 有差距则继续同类追问,连续答对则升一档难度 / 升级概念状态
  4. 每次作答结果回写 `knowledge.json`(正确率/得分)
  5. 达到 `solid` 用小结确认 → 达标即记入复习计划
- 判分回传:判分与"差距分析"是生成长文本 → 走 LLM;但"是否连续答对 / 状态是否晋升"是布尔判断 → 前端/本地规则完成,不额外调模型

### 3.3 提示词清单
- `medkit/prompts/medexplain.md`
- `medkit/prompts/medtutor.md`
- 沿用影子副本机制(`~/.medkit/prompts/` 可覆盖),供"④ 提示词与规则"统一管理

---

## 4. 后端路由(`medkit/routers/`,`state.py` 登记,复用 `_common.py` 的 pid/日志/原子写)

新增命名空间 `/api/library/*`(学习中心),并扩展现有 `/api/projects/{pid}`:

### 4.1 错题导入与沉淀
- `POST /api/library/mistakes` —— 新增一条错题(来自押题卷「同步到错题本」、正文粘贴、文件导入)
- `POST /api/library/mistakes/parse` —— **半自动结构化**:接收原始文本/OCR 结果 → 调 MedExplain 前的抽取器(LLM)归为 `question/options/answer/analysis/error_reason/know_tags`,返回预览让用户确认后再入库(借鉴 wrong-notebook 的 AI 打标)
- `POST /api/library/import/image` —— 图片错题 → 复用已接入的 **MinerU OCR**(`routers/ocr.py`)→ 交给 parse
- `POST /api/library/import/file` —— CSV/JSON 批量导入(兼容外部刷题 App 导出 / 手动整理)
- `PUT /api/library/mistakes/{id}` / `DELETE ...` —— 编辑 / 移除

### 4.2 教材切片级 grounding(讲解与提问的地基,零向量库)
- 复用项目目录 `slices.json`(已有,含 `sid/title/text`),但错题来自跨项目 → 需要一个**"项目切片库"索引**:
  - 新增 `~/.medkit/library/slice_index.json`:把各项目的 `slices.json` 汇总成 `列表[{pid, sid, chapter/title, text}]`(首次入库时扫描,之后增量)
  - 检索策略:简单可靠——按 `subject/chapter/topic` 精确匹配优先 → 匹配不到再对 `title` 做关键词 top-k → 都空则提示补充素材。**不做 embedding**(成本与依赖取舍)
- 讲解/提问时把"命中切片"注入 system(与 MedGen 现在的 `全文仅注入一次` 一致,控成本)

### 4.3 掌握度诊断与优先级(借鉴 AMBOSS + MedPrep 的 IDF 检索思路)
- `GET /api/library/mastery` —— 返回全部知识点及其 `score / state / priority` + 驾驶舱聚合(最薄弱章节、今日该学、近期趋势)
- `GET /api/library/recommend` —— **「接下来重点学什么」**:按 `priority = f(score 低 + miss_count 高 + 距上次答对远 + 覆盖考试权重主题 sparkline)`
- 优先级规则本地计算(不调 LLM);仅"判分/归因"调 LLM
- `GET /api/library/priority`(stretch) —— 集成 IRT 2PL(参考 adaptive-eval `p=1/(1+e^-a(θ-b))`)做题目难度自适应,后续迭代

### 4.4 讲解
- `POST /api/library/explain` —— body `{kp_id | mistake_id, depth}` → 返回 MedExplain 输出(含引用溯源),并更新 `knowledge.json` history

### 4.5 提问式学习
- `POST /api/library/tutor/start` —— 按知识点开一个 Socratic 会话(返回第一问 + 状态机)
- `POST /api/library/tutor/answer` —— 提交作答 → 返回 `{score, gap, next_question, state}`(本地判定状态晋升 + LLM 生成)
- `GET /api/library/tutor/{session_id}` —— 恢复历史(断点续聊)

### 4.6 复习调度(轻量 SM-2)
- `POST /api/library/review/queue` —— 把知识点卡片入队(interval/ease 初始化)
- `GET /api/library/review/today` —— 今日到期卡片(借鉴 MCAT 的"错题加权、做对间隔翻倍")
- `POST /api/library/review/grade` —— 回答问题后传入 quality(0~5)→ 走 SM-2 更新 interval/ease/state
- `POST /api/library/review/export-anki`(可选) —— 生成 `.apkg` 头/URL 提示,接入已有 Anki 导出

---

## 5. 前端(`medkit/web/index.html` 新增)与交互

### 5.1 新增侧栏 tab:「④ 学习中心」
主界面新增第 4 个侧栏项(hash 路由 `#learn`,沿用现有 tab 结构 / 主题变量 / SVG 图标 / toast / 模态),内嵌子视图:

1. **错题本**:列表(科目/章节/错因标签),支持图片/文本/文件导入(拖拽复用现有上传区),「同步到错题本」的落位按钮;每道错题可 `→ 讲解` `→ 提问练习` `→ 标记已掌握`
2. **薄弱点诊断**:掌握度卡片(参考 flutter_learning_analytics:正确率 + 状态条 + 最短弱项),「接下来重点学什么」推荐卡片(§4.3)
3. **讲解**:选中知识点/错题 → 生成讲解 → 展示引用溯源(折叠教材原文),支持再次提问
4. **提问式学习**:对话流(Socratic 引导),每轮展示评分与状态机进度,不让模型直接给答案
5. **复习计划**:今日到期卡片列表 + 答题 → 更新(可折叠;可选推送到 Anki)

### 5.2 UI 复用与一致原则
- 暗色五级灰阶 / 亮暗主题 / 配比条 / OCR 进度卡 / 成本预估 toast 全部沿用现有设计语言
- 每处**触发 LLM** 前都显示「预计 X 万 token · 约 ¥Y」,复用 `/api/cost/estimate`;判分/状态晋升等纯本地判定不给 LLM

---

## 6. 成本与幻觉控制(贯穿设计,符合"零 API / 极低 API"取向)

| 环节 | 是否调 LLM | 说明 |
|---|---|---|
| 错题导入(自有押题卷) | 否 | 题目已结构化,直接落库,打标按现有 subtopic/sid |
| 错题导入(图片/文本) | 是(仅抽取打标) | 复用 MinerU OCR + 一次结构化,用户确认后入库 |
| 掌握度/优先级 | 否 | 本地规则(正确率+密度+时间+权重) |
| 复习调度 SM-2 | 否 | 纯本地算法 |
| 讲解 | 是(仅生成讲解) | 一次性,展示成本预估,引用教材切片 |
| 提问式学习 | 是(出问/判分/追问) | 按轮次,每轮独立小账(复用 usage.context) |
| 状态晋升/连续答对判定 | 否 | 本地布尔判断 |

- grounding:讲解/提问只依据命中切片文本;切片缺失明示"需补充素材",**绝不自由编造**
- 双向成本护栏:讲解/提问均按次记账并写入 `knowledge.history`;重复同知识点提供「本地缓存讲解」降级选项

---

## 7. 实施里程碑与验收

> 建议顺序按"闭环依赖"排:S0 打底 → M1 错题沉淀(闭环入口)→ M2 掌握度(闭环的心脏)→ M3 讲解 → M4 提问式 → M5 复习调度。

| 阶段 | 内容 | 关键产出 / 验收 |
|---|---|---|
| **S0 安全网** | git 基线 + `verify.cmd` 全量回归(99 pytest + 7 浏览器测试全绿) | 现有 v0.6 无回归 |
| **M1 错题沉淀** | `library.py` 数据层 + `mistakes.json` + 导入(押题卷同步/正文/图片OCR/文件)+ 首页「错题本」 | 4 种来源导入闭环、MinerU 图片错题结构化、pytest 单测(mistakes CRUD / 导入解析 mock) |
| **M2 掌握度诊断** | `knowledge.json` + 状态机 + 优先级推荐 + 驾驶舱 | 近 N 题正确率/密度/时间阈值得分、`/recommend` 输出可解释、阈值 薄弱<60%/需复习60-80%/掌握>80% |
| **M3 教材讲解** | `slice_index` + `medexplain` + `/explain` + 讲解页(引用溯源) | 讲解锁定切片、引用【章节】可见、切片缺失不编造、pytest(mock LLM) |
| **M4 提问式学习** | `medtutor` + 会话状态机 + `/tutor/*` + 对话页 | 状态 weak→mastered 晋升正确、五类提问轮换、判分回写掌握度、浏览器多轮对话测试 |
| **M5 复习调度** | 轻量 SM-2 队列 + 今日复习 + (可选)Anki 导出 | 到期队列正确、做对间隔翻倍/做错重学、跨项目开关机数据持久 |

**里程碑验收总闸:** 全量 pytest 通过;浏览器双主题 + 窄屏通过;成本预估在讲解/提问触发前可见;断点/取消/权限(pid 消毒、Host 校验)不回归。

---

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 图片错题 OCR 准确率不足 | 结构化结果预览给用户确认后再入库;失败提示手动粘贴 |
| 提问式判分过松/过严 | 判分 prompt 明确 rubric(结论/机制/完整度),记录 0~3 分分布可调阈值 |
| 讲解引用错切片 | 切片匹配优先精确 subject/chapter/topic;引用以【章节·标题】可点开原切片 |
| 学习库数据损坏 | 复用 `fsutil` 原子写 + meta 容错(借鉴项目损坏 422 处理) |
| 成本超预期 | 判分/打标/调度走本地;生成长文本按次记账 + 预估弹窗 |

**回滚:** 学习库与出题管线完全解耦 → 本次改造不影响任何已存在项目的产物与生成能力;仅新增路由/tab 挂载点可独立开关(`state.py` FLAG + 前端 feature 常量),出问题即时关掉学习中心,不中断出题使用。

---

## 9. 交付节奏建议

- 本次交付 = 本设计文档评审通过后的 **M1 (错题沉淀) + M2 (掌握度诊断)** 为第一个可交给用户使用的 MVP——先用最省成本的本地闭环验证需求价值。
- M3/M4/M5 逐个验收后随版本号 bump 发布(沿用 `medkit/__init__.py` 单源 + GitHub Release + Inno 安装包 + 内置更新检查的既有链路)。
- 全程沿用双测试三角:后端 pytest(mock LLM 离线)、前端浏览器双主题/窄屏、端到端冒烟。

---

## 10. M3 教材讲解落地设计(2026-08-27 评审后细化)

> 本轮用户三处补充约束,均在此落实:
> ① **网络补充默认开启**——讲解不再"切片缺失即放弃",而是切片不足时默认联网补充(复用已有 web_search 多后端),网络素材与教材切片双标来源;
> ② **按科目分类**——个人学习库从"扁平错题列表"升级为"科目 → 知识点 → 讲解产物"三层,切片索引也按科目组织;
> ③ **产物管理**——借鉴 Wrong Question Notebook(WQN)的 *subject + problem sets* 组织与 LLM-wiki / open-notebook 的"生成物沉淀为可导出文档",讲解产物持久化为可查看/按科目归档/重新生成/导出 md 的资产,而非一次性流式输出。

### 10.1 科目维度贯穿(slice_index + 产物都带 subject)
- 复用项目目录 `cfg.load()["ln"]/projects/{pid}/{slices.json, meta.json}`:扫描所有项目,按 `meta.subject` 把 `role=="textbook"` 切片汇总进 `~/.medkit/library/slice_index.json`:
  `{"subjects": {"儿科学": [{"pid","sid","title","source","page","text"}], ...}}`
- 全站"科目"下拉:错题列表 / 薄弱点 / 讲解产物 / 推荐全部可 `?subject=` 过滤;`GET /api/library/subjects` 返回已覆盖科目清单(错题+知识点+切片并集)。
- 检索(零向量库,对齐 §0):命中 `subject` 后,对 `title+text` 做关键词 top-k(知识点名/章节/错题 know_tags 拆词),取 ≤2 切片注入,不足走向网络。

### 10.2 medexplain(agents/medexplain.py)+ 联网补充默认
- 签名:`explain_knowledge(client, subject, kp_name, slices_text, related_mistake, web_materials={})`
- 流程:检索切片 → 若 `use_web`(默认 True)则用 `websearch.build_backend_fn(解析当前服务商)` 做 ≤1 轮补充检索(检索词由 LLM 依知识点生成,Q=1,控成本)→ 切片+网络素材注入 system → LLM 输出结构化讲解。
- 输出规约(**结论先行 → 机制 → 鉴别/易混 → 记忆锚点**),每段带引用:`【教材·切片标题】` 与 `【网: title | url】` 不可混标;幻觉护栏保持 docstring 语义不变。
- 提示词 `medkit/prompts/medexplain.md`(影子副本可覆盖,进「④ 提示词与规则」)。

### 10.3 讲解产物(explains.json,借鉴 WQN subject 组织)
- `~/.medkit/library/explains.json`:`[{explain_id, subject, kp_id, kp_name, created_at, content, sources[], via_web, web_materials[], related_mistake}]`,本地原子写,不占 LLM。
- 每次生成回写 `knowledge.history`(event="explain"),支持「本地缓存讲解」降级(同 kp 已生成 → 前端提示复用)。
- 产物管理:列表(按科目分组)→ 查看全文 → 重新生成(覆盖)→ 导出 Go 可读 markdown(合并当前科目全部产物 → 充当"个人复习手册")→ 删除。

### 10.4 路由与前端
- `POST /api/library/explain` —— body `{subject, kp_id|kp_name, mistake_id?, use_web=true, depth?}` → 检索+补网+生成+存产物+回写掌握度 → 返回 `{explain, title}`;触发前前端复用 `/api/cost/...` 展示成本预估。
- `GET /api/library/explains?subject=` / `GET /api/library/explains/{id}` / `DELETE ...` / `POST /api/library/explains/export?subject=`(返回合并 md)。
- 前端「④ 学习中心」新增**「讲解与学习产物」**子视图:顶部分科目筛选,列表展示产物卡片(科目/知识点/时间/来源数),点击展开全文(教材+网络来源分色),按钮:重新生成 / 导出 md / 删除;薄弱知识点卡片旁直接提供「→ 讲解」入口,讲解前 cost toast。

### 10.5 验收
- mock LLM + mock 网络搜索的 pytest:检索命中切片能生成讲解且标注【教材】;use_web=True 且切片不足时能联网取得网络素材并标【网】;产物 CRUD + 导出合并 md + 按科目分组正确;重生成回写 history 不重复记账。
- 浏览器双主题/窄屏:讲解子视图、科目筛选、产物列表正常;讲解前成本预提示可见。