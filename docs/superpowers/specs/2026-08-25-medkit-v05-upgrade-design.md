# MedKit v0.5 升级方案 · 结构化执行清单

> 日期：2026-08-25 · 状态：待执行 · 方向：**A 阶梯攻坚**（4 段，每段验收 git tag 后进下一段）
> 依据：更新后三路深度代码审查（后端 / 代理与渲染 / 前端测试打包）+ 官方文档核实（DeepSeek / 智谱 / 百炼 / MinerU，2026-08）
> 节奏：集中攻坚 · 总周期约 1.5~2 周

---

## 0. 背景摘要

v0.4.0 已完成上两轮审查的全部修复与可玩性升级（断点续跑 / 取消 / 并发 / 成本预估 / 查重 / Anki 文本导出 / 提示词编辑器 / 审核台）。本轮复审发现三类残留：

1. **过时信息**（数据漂移，部分已构成真 bug）：智谱默认模型落后两代、qwen-max 联网说法错误、MinerU 页限过时、config 默认 `deepseek-chat` 导致联网检索必 400。
2. **更新引入的新缺陷**（上轮未覆盖）：渲染层 IndexError 会在最终导出步崩溃全管线、过滤按钮失效、MedFix 丢字段、Anki 换行损坏、`javascript:` 穿透、若干并发/串账问题。
3. **工程差距**：无 git 仓库、main.py 1088 行 33 路由、裸 dict 接口、零 logging、离线测试 pytest 收集数为 0。

---

## 总览

| 攻坚段 | 主题 | 工作量 | 验收门（全过才进下一段） |
|---|---|---|---|
| S0 | 安全网：git + 测试基线 | 0.5 天 | verify 一键全绿 → `tag v0.4.0-baseline` |
| S1 | 正确性修复 + 过时数据刷新 | 3~4 天 | 每项修复配回归测试，全量绿 → `tag v0.5.0-s1` |
| S2 | 工程化重构 | 2~3 天 | API 契约不变（test_api 全绿）+ ruff/pytest 绿 → `tag v0.5.0-s2` |
| S3 | 功能：.apkg / 案例题 / 素材库 | 3~4 天 | 新题型全链路 + .apkg 实测 → `tag v0.5.0` |

**已确认的三个设计决策**
- D1 案例题数据结构：**扁平 + `case_id` 字段**（不引入嵌套，兼容现有审核台/编辑器/Anki）
- D2 修复轮耗尽的坏题：**剔除出产物 + 写入人工复核清单**（绝不强行渲染）
- D3 git：**仅本地 init**（是否推 GitHub 私有仓后续自定）

---

## S0 安全网（0.5 天）✅ 已完成（commit 2a298cc · tag v0.4.0-baseline）

- [x] `git init`；核对 `.gitignore` 覆盖 `build/ dist/ dist-installer/ projects/ __pycache__/ .ruff_cache/`；首次 commit
- [x] `tests/test_pipeline_offline.py`：`main()` 内嵌套用例提升为模块级 `test_` 函数（当前 pytest 收集数 = 0，只能手动跑）
- [x] `tests/test_pipeline_offline.py`：配置目录改临时目录隔离（参照 `test_api.py:24-37` 的 TMP_DIR 模式；当前写真实 `~/.medkit`，有污染风险）
- [x] 新增 `verify.cmd`：`ruff check . && python -m pytest -q` 一键验证
- [x] 全量跑绿 → `git tag v0.4.0-baseline`

---

## S1 正确性修复 + 过时数据刷新（3~4 天）✅ 已完成（commits c807964→cf1a7e1 · tag v0.5.0-s1 · pytest 66 全绿）

### A. 渲染层防崩溃（P0/P1）

- [x] `qbank_html.py:12` `LETTERS` 扩容至 10；`options_check.py` 增规则：选项数 >6 → issue（触发 MedFix 改写）；渲染前终检：仍超限/缺字段的题**剔除出产物 + 记入人工复核清单**（D2）
- [x] 押题卷过滤按钮失效修复：`<details class="q">` 增加 `data-type` 属性，`ft()` 改按 `data-type` 过滤（当前查 `t-A1` 类从未写入，点任一按钮隐藏全部题）
- [x] `medfix.py:16` issue 校验：`q_id` 必须命中现有题，否则跳过 + 日志（当前缺 q_id 直接 KeyError）
- [x] `medfix.py:21-27` 修复输出改**合并策略**：`sid/module/subtopic/type/case_id` 取原题，内容字段取新题（当前整体替换导致溯源字段丢失，后续 QC 的 source_slice 为空）
- [x] `qbank_html.py:15-19` Anki `_esc`：`\n` → `<br>`、`\t` → 空格（当前 LLM 解析含换行即损坏文件）；补单测（换行/制表符/引号字段）
- [x] `review_html.py:42` `href` scheme 白名单（仅 http/https，其余剥成纯文本），堵 `javascript:` 穿透
- [x] `medqc.py` 容错：`int(score)` 兼容浮点/None（None→50 + warn）；`severity` 统一 `lower()`；空题库 → 跳过该批 + warn（当前判 PASS 0 分）；删除 `decisions` 聚合死代码或启用（取删除）

### B. 后端安全 / 并发（P1）

- [x] `main.py:887` `delete_preset` 过 `_safe_pid` 消毒（当前可路径穿越删任意 `.json`）
- [x] `config.py:105` 浅拷贝 → `copy.deepcopy`（当前嵌套 dict 污染模块级 DEFAULTS，删配置键后旧值进程内残留）
- [x] usage 记账改**按次上下文**：run / trial / regen 各自独立记账并随响应返回，`orchestrator.py:147` 不再全局 `reset()`（当前互相串账）
- [x] `main.py:986/1023` review / regen 接口检查 `RUNNING` 锁，运行中返回 409；写盘统一走原子写 helper（当前裸 `write_text`，与 A5 加固目标相悖）
- [x] `main.py:71` host 解析兼容 `[::1]`（当前 `split(":")[0]` 得 `"["`，IPv6 回环永远 403）；origins 白名单补 IPv6
- [x] `main.py:254` `ocr_start` 中 `write_bytes`（最大 200MB）移出事件循环（线程 / `run_in_executor`）

### C. 过时数据刷新（已对照官方源核实，2026-08）

- [x] `providers.py` 智谱：`default_model` `glm-4.6` → `glm-5.3`；`price` → 8 / 28（缓存命中 2）；note 更新至 GLM-5.3 / 5-Turbo / 4.7 代际
- [x] `websearch.py:57,219` + `BACKENDS` 注册表：**删除「qwen-max 不支持联网搜索」**（百炼官方 2026-08-19：qwen3-max 系列已支持，现行代际至 Qwen3.8）
- [x] `websearch.py:125,301` `search_zhipu` 默认模型同步 `glm-5.3`
- [x] `mineru.py:24` `V4_PAGE_LIMIT` 200 → **600**（官方现行单文件 ≤200MB/≤600 页，每日 2000 页高优先级额度）；README 同步
- [x] `config.py:28` 默认模型 `deepseek-chat` → `deepseek-v4-flash`；`config.load` 对旧值自动迁移 + 日志提示；`main.py:150` 报错文案同步
- [x] `websearch.py:298` 防御：deepseek 检索后端收到的模型非 `deepseek-v4*` 时回退 `deepseek-v4-flash`（**修掉「默认配置下联网检索必 400」**）
- [x] `README.md`：服务商表全量更新；`Setup-0.3.0` → 0.4.0；「五角色提示词」→「四个提示词」（五阶段中门禁是规则引擎非 LLM 角色）

### D. 门禁与出题健壮性（审查补充项）

- [x] `medgen.py:62-66` Bloom 配比兼容小数（`float` 解析后归一，当前 `int(0.3)=0` 静默回退默认）；合计 ≠100 时归一 + warn
- [x] `medgen.py:86,124-131` `options=None` 防御（`setdefault` 不覆盖显式 null，下游 enumerate 崩）；LLM 超发题数截断
- [x] `medgen.py:118-119` 链式 `replace` 注入面：教材文本含 `{teacher_text}` 字面量会被二次替换 → 改一次性安全替换（先占位唯一令牌或逐键一次性 format）
- [x] `trace_check.py:6-7` 兼容全角冒号「源：」与 `[源:S999]`（当前只认半角，全角误报 F2）→ 另修：兼容全角括号【源:…】（LLM 实际输出即全角括号，旧代码溯源全量误报）
- [x] `dedup_check.py:10` 保留数字判别（当前剥数字后「血钾 5.5 vs 7.0」两道不同临床题误报近似重复）
- [x] `bloom_check.py:18` 小题量（n<10）放宽分布硬校验（当前 1/n>15% 必 fail 且 q_id="BLOOM" 无法被 MedFix 定位，修复轮空转）

### E. 前端健壮性

- [x] `index.html` `initTheme` 及全部 localStorage 读写包 try/catch（当前隐私模式抛异常中断整个脚本，UI 全挂）
- [x] `showTab` 切走项目详情时 `stopPoll()`；OCR 轮询循环离开页面终止（当前后台持续轮询）
- [x] `window.onerror` + `unhandledrejection` → toast 兜底（当前 fetch 失败静默）
- [x] `index.html:1294` spinner 修复（当前 `textContent` 写 HTML，显示为字面文本）
- [x] `qbank_html.py:178,246` 押题卷计时器：重载后从保存的 `st.t0` 恢复（当前 `Date.now()` 重置，保存值从未使用）

### F. P2 顺带修（全部完成）

- [x] OCR 取消竞态：worker 完成后覆写 cancelled 状态 → 终态不可逆
- [x] 取消后部分检索结果落盘标记 `incomplete`，续跑重新检索（当前当作完整结果）
- [x] 同秒同名项目 `mkdir(exist_ok=True)` 静默合并 → 加时间戳/序号后缀
- [x] 原子写统一为 orchestrator 的「唯一 tmp 名 + 重试」实现（main.py 固定 tmp 名无重试）→ 收敛于 `core/fsutil.py`

**S1 验收**：每项修复配回归测试（新增 tests/test_s1_render.py · test_s1_backend.py · test_s1_data.py · test_s1_gates.py）；`python -m pytest -q` 全绿（66 passed）；FakeLLM 全链路通过 → `git tag v0.5.0-s1` ✅

---

## S2 工程化（2~3 天）

- [ ] **拆分 main.py（1088 行 / 33 路由）**：`routers/{config,ocr,parse,projects,pipeline,prompts,presets,search,review}.py` + `state.py`（RUNNING / OCR_JOBS / 记账）；main.py 仅留 app 装配
- [ ] `@app.on_event("shutdown")` → lifespan 上下文管理器（已弃用 API）
- [ ] Pydantic 请求模型：CreateProject / Run overrides / Review edits / Regen / PromptUpdate / Preset 增删（当前全裸 dict）
- [ ] 统一异常体系：`LLMError/SearchError/MinerUError/PipelineError` → 全局 `exception_handler` 映射结构化 4xx/5xx（当前裸 `except Exception`）
- [ ] logging：`~/.medkit/logs/medkit.log` RotatingFileHandler + 控制台；UI 实时日志通道（log 回调）保留不动
- [ ] **版本单源** `medkit/__init__.py` `__version__ = "0.5.0"`；`build.bat` 从中生成 iss 版本；README 引用（当前 iss/spec/README 三处手写）
- [ ] `start.bat` 依赖检查补全 8 个包（当前漏 pymupdf/python-docx/markdown/httpx）；提示改「4880，占用自动回退 4881-4889」
- [ ] 常量与逻辑去重：`CHARS_PER_TOKEN` 两处合一、成本公式两处合一、trial(2000) 与管线(4000) 的 teacher_text 截断统一
- [ ] `main.py:393` 删除死参数 `ocr: str = Form("0")`
- [ ] 测试补齐：渲染转义/过滤按钮单测、`delete_preset` 拒绝路径穿越、review/regen 运行中返回 409
- [ ] （可选）`.github/workflows/ci.yml`：push 时 ruff + pytest，与 verify.cmd 等价

**S2 验收**：test_api 全绿证明 API 契约不变；ruff + pytest 绿；PyInstaller 打包后 exe 冒烟启动 → `git tag v0.5.0-s2`

---

## S3 功能（3~4 天）

### 1. .apkg 真包导出（genanki 0.13.1，纯 Python 零重依赖）

- [ ] `requirements.txt` + `medkit.spec` 增加 genanki
- [ ] **model_id/deck_id 稳定化：按项目名哈希生成**（★随机 id 会导致重复导入生成重复卡，这是 genanki 最常见坑）
- [ ] 模板：正面 = 题干 + 选项；背面 = 答案 + 解析 + 溯源；标签 = 题型 / Bloom / 章节
- [ ] X 型多选：自评模式卡（显示答案前不勾选状态）
- [ ] 导出按钮加入产物列表（与现有 Anki .txt 并列保留）
- [ ] 测试：产物 zip 结构可解析、collection.anki2 可读、特殊字符字段不损坏

### 2. A3/A4 案例题 + B1 组题（D1：扁平 + case_id）

- [ ] 数据结构：question 增加 `case_id / case_order / case_stem / group_kind(case|option_group)`；B1 共享选项存 `group` 字段——保持扁平；**case_stem 在组内每道子题冗余存一份**（换取子题独立编辑/剔除/修复时不丢题干），现有审核台/编辑器/Anki 结构不动
- [ ] `medgen.md` 提示词：案例题模式（每案例 3~5 子题、子题独立选项、共用题干）；B1 真组题（选项组共享，替代现有「B1 自动分摊」）
- [ ] `quota.py`：案例题按子题计数
- [ ] 门禁：子题逐条校验（options/trace/bloom），dedup 增加组内查重
- [ ] medqc / medfix：子题单位携带案例题干上下文；修复保持组结构（merge 策略天然兼容）
- [ ] 渲染：题库 HTML/MD 按组折叠展示；押题卷案例题分组呈现 + 分组判分；Anki/.apkg 子题卡带题干前缀
- [ ] 审核台：组维度折叠，子题可单独剔除（剔除后组内剩余仍有效）
- [ ] 测试：FakeLLM 产出案例组 → 门禁 → QC → 渲染全链路断言
- [ ] **先写期望数据结构的测试，再动实现**（改动横跨 prompts/门禁/QC/渲染全链）

### 3. 素材库复用

- [ ] 解析会话索引页：sessions 列表（文件名/大小/章节数/时间）；创建课题可选**任意历史 session**（当前只能用当次解析）
- [ ] 多教材合并出题：课题可挂多个 session，quota 跨 session 按章加权
- [ ] 项目配置模板：一键复制 provider/model/旋钮/Bloom 配比/检索设置
- [ ] 测试：跨项目复用 session 创建课题

**S3 验收**：新题型 FakeLLM 全链路绿；.apkg 导入 Anki 桌面版实测；素材复用手测清单通过 → `git tag v0.5.0`

---

## 全局验收

- [ ] `verify.cmd`（ruff + pytest）全绿
- [ ] dist 打包 exe 冒烟：启动 → 解析 → 出题 → 四产物导出（含 .apkg）
- [ ] 手测清单：隐私模式打开不崩、tab 切换轮询停止、取消+断点续跑、X 型 checkbox 判分、题型过滤按钮、GLM-5.3 真实调用一次
- [ ] README 全量更新；`git tag v0.5.0`

---

## 风险与回滚

| 风险 | 缓解 |
|---|---|
| 集中改动回归面大 | 每攻坚段独立 tag；翻车 `git reset` 回上一 tag |
| S2 重构 API 契约漂移 | 以 test_api 为契约测试，重构前先补齐缺失断言 |
| S3 案例题横跨全链 | 先在 FakeLLM 测试里定义期望数据结构，再动实现 |
| genanki 重复导入重复卡 | model_id 按项目名稳定哈希（已列入 S3 验收） |

## 明确不做（本轮范围外，留 backlog）

- 前端 a11y 深化（label 关联/焦点陷阱/aria）与 @media 响应式断点
- 跨项目错题本 + 遗忘曲线复习计划
- 押题卷计时持久化之外的练习化功能
- CI 上云执行（仅留 workflow 文件，用否自定）
