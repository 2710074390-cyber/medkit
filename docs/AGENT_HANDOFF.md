# Agent 交接文档（MedKit）

> 用途：不依赖对话记忆的工程交接入口。接手的开发者/Agent 先读本文件，再按需深读
> `medkit/core/syllabus.py`、`medkit/routers/syllabus.py`、`medkit/web/js/learn.js` 与测试。
> 目标读者：**任何接手本仓库的人**；本文档按版本滚写，保留最近变更记录。

## 变更记录（最近）

| 日期 | commit | 变更 |
| - | - | - |
| 2026-08-27 | `74d999b` | NX-02：打包环境 jieba 兜底（fts_tokens 仅 bigram、spec 收集 jieba 数据） |
| 2026-08-27 | `8b4baf4` | K3/IMP-13：官方大纲文件导入闭环（LLM 契约抽取 → seed）+ 教师重点 v4 后端（+前端官方大纲入口） |
| 2026-08-27 | 本批 | **大纲标准二选一收尾**：教师重点文件导入前端入口 + 知识点提取（`extract_teacher_kps`）+ 标准切换去「全部」档 + api() Content-Type 修复 + 本交接文档 |
| 2026-08-27 | `b321215` | NX-03：契约层闭环（MedQC 硬闭环 validate_or_repair + score=-1 人工复核；MedGen 软校验计数落 meta，概览卡可见） |
| 2026-08-27 | 本批 | **NX-04（WP-05）**：记忆卡工厂（`agents/medcards.py` + `prompts/medcards.md` + `CardDraft` 契约）+ `core/cards.py`（迁移 v5）+ `core/scheduler.py`（py-fsrs 6.3.2 默认 / SM-2 legacy 可切，创建时绑定）· 讲解产物「生成记忆卡」+ 复习计划「🧠 医学记忆卡」面板 · `export_memory_apkg` |

## 1. 大纲选择机制（v4 · 标准二选一）

从本版起，大纲标准**只有两档**（前端 `syl_std` 两个 pill，默认「教师重点」）：

| source | 含义 | 来源 | 写入路径 |
| - | - | - | - |
| `seed` | 官方大纲（西综306） | ① 内置种子 `data/syllabus_seed_306.json`（`ensure_seed` 幂等导入）；② **上传官方大纲 md/txt**（LLM 契约抽取，`/api/syllabus/seed/import-file`） | 种子文件 / `add_seed_items` |
| `teacher` | 教师重点 = **用户自供内容**（粘贴/文件/项目 teacher 切片，历史 `paste` 已由迁移 v4 归一为 `teacher`） | ① 粘贴「解析预览→确认」；② 文件上传自动处理；③ `sync_teacher` 扫项目切片 | `add_teacher_items` / `sync_teacher` |

- **标准切换语义**：`/api/syllabus/coverage?source=seed|teacher`（内部聚合用 `all`，前端不再提供「全部」档）。
- **覆盖口径**（`match_status`，零 LLM）：条目 vs 学习库「知识点名/错题主题/tag」池 →
  covered（命中）/ mastered（命中且知识点 state ∈ solid|mastered）/ pending（未覆盖）。
- **前端**：学习中心 ⑥ 大纲覆盖 → `#syl_std` 两档 pill + 粘贴卡（解析预览/确认入库/上传教师重点文件/上传官方大纲）。

## 2. 教师重点处理流程（自动处理 · 零 LLM）

```
文件(PDF文本层/DOCX/MD/TXT) ──extract.py 文本抽取──▶ 全文
  │ (扫描件PDF <200字 → mode='error'，提示先 OCR；文本文件 <20字 同样拒绝)
  ▼
import_teacher_text 两档解析（自动判定，无需用户选择）
  ├─ structured：带「章+编号条目」结构（parse_text，≥2 条）→ 章/条目层级
  └─ flat：无显式结构（讲义/PPT 式要点行）→ _teacher_items 行级提取（≥6 字，cap 200）
         → 全部挂「教师重点」章
  ▼
extract_teacher_kps 知识点提取（见下）
  ▼
add_teacher_items 幂等落库（source='teacher'，sha1 id 幂等）
```

- **接口**：`POST /api/syllabus/teacher/import`（文本一键）· `teacher/import-file`（文件，
  限 20MB，`subject` 可选 Form 字段）· `GET /status` · `sync-teacher`（项目切片同步，幂等）。
- **知识点提取**（`extract_teacher_kps`，零 LLM）：条目 → 知识点名（去「重点掌握/考点…：」
  前缀、去尾部标点、超 40 字在最后「、」收束、(subject,name) 去重保序），随导入响应
  `knowledge` 字段返回（预览展示前 10 条）。
  ⚠️ **设计边界**：知识点名**不写入学习库掌握度状态机**（掌握度仅由真实错题/判分事件驱动，
  避免凭空生成 weak 知识点涌入推荐池）；供人核 + 后续出题/记忆卡（WP-05）锚点使用。
- **前端入口**：大纲覆盖 →「粘贴/导入大纲」卡 →「上传教师重点文件」
  （accept=.pdf,.docx,.md,.markdown,.txt,.text；onchange → `sylTeacherImport`）。

## 3. 官方大纲文件导入（K3/IMP-13 · seed 通道 · 唯一 LLM 触点）

- **提示词**：`medkit/prompts/syllabus_extract.md`（逐科 one-subject JSON 契约；质量红线：不臆造/不合并/不混入章标题）。
- **契约**：`medkit/core/schema.py` `OutlineChapter/OutlineSubject/SyllabusOutline`（extra=ignore、
  空条目/空章/空科目剔除、科目名必填、条目去编号与句尾）。
- **流程**：`split_subjects`（「考查内容」锚点 + 中文数字顶级标题，兼容 Markdown `#` 前缀）
  → **逐科** `chat_json`（`max_tokens=16000` 🔑）→ 科目名归一（去尾部括号注释，如「外科学(含骨科学)」→「外科学」）
  → 保序合并；任科失败仅记 `errors`；全败返回 None → 路由回退本地 `parse_text`。
- **接口**：`POST /api/syllabus/seed/parse-file`（预览）· `seed/import-file`（预览 + `add_seed_items`
  幂等入库 source='seed'）。
- **核验结论**（`docs/spikes/K3_syllabus_extract.py` + `k3_out/`）：独立解析器真值 402 条 →
  recall 100.0% / precision 96.5% / 章名 66/75 / **10 条抽样 10/10（≥80% 闸门通过）**。

## 4. 陷阱与注意事项（踩过的坑）

1. 🔑 **推理模型 token 预算**：`deepseek-v4-flash` 会把 `reasoning_tokens` 计入 `max_tokens`；
   `max_tokens=6000` 时大科（内科/外科）返回**空**（finish=length）；**≥16000 才稳定**。
   改 `extract_outline` 参数前先修这条。科目名必须随正文下发（`f"{name}\n{body}"`），否则
   模型猜名（曾把「内科学」猜成首章「诊断学」）。
2. **零 LLM 原则边界**：主流程（教师重点/覆盖判定/报告/考频）全本地零 LLM；唯一 LLM 触点 =
   官方大纲 seed 文件导入（且有本地回退）。新增功能默认本地实现，LLM 需走 ADR-003 契约。
3. **迁移 v4 不可精确回滚**：`syllabus_items` 行 id 是 sha1（不含 source），`_V4_DOWN` 为空；
   历史 `paste` 行归 teacher 后无法按行还原。
4. **前端静态路径**：拆分后静态资源挂 `/assets`（`app.mount("/assets", StaticFiles(...))`），
   链接一律 `/assets/...`；经典脚本共享全局作用域，跨文件函数加载期前向引用会挂。
5. **api() 契约**：字符串体自动补 `Content-Type: application/json`（已修复，浏览器测试不再
  打 fetch 补丁）；FormData 原样透传（不设 header）。改 api() 需同步
   `tests/browser/test_syllabus_view.py`。
6. **标准切换两档**：前端 `syl_std` 只保留 `teacher`/`seed`；browser 用例
   `test_syllabus_paste_parse_confirm` 断言「无 all 档」。改档位需同步该用例 + `sylRender`
   空态文案（learn.js 的 `stdName`/提示分支）。
7. **多 Agent 并发**：本仓库曾出现两个会话并发改同一批文件导致互相覆盖/回滚（git clean 会删
   未跟踪文件）。建议：长任务改完**立即提交**；重要产物放跟踪路径；勿用 `git clean`。
8. **verify.cmd**：`ruff → pytest → 浏览器(Playwright)`；本地无浏览器/无网时
   `SET SKIP_BROWSER=1` 跳过浏览器层（CI 不含浏览器层，仅本地门）。
9. **教师重点文件处理**：extract.py 对「扫描件 PDF（无文本层）」直接拒绝（mode='error' 提示先
   OCR）；本机未下载 MinerU 通道前，扫描件走外部转换（如 WPS/OCR）再上传 md/txt。

## 5. 常用开发入口

- 测试：`python -m pytest tests/ --ignore=tests/browser -q`（单元）· `python -m pytest tests/browser -q`（浏览器，需先 `pip install playwright && playwright install chromium`）。
- 总闸：`verify.cmd`（Windows）。
- 单测隔离：`tests/conftest.py` 把 `dbs.DB_PATH` 等指向 tmp；新增库表/迁移需同时覆盖 `tests/test_db.py`（migration 标记）。
- 打包：`pack/build.bat`（PyInstaller，`medkit.spec`；version 单源 `medkit/__init__.py`）。
