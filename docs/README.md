# docs · 文档导航

仓库文档按「活跃 / 历史」两层组织：**活跃文档**留在 `docs/` 顶层，**已完结的审查、设计、实验**一律移入 `docs/archive/` 只读留档。

## 活跃层（顶层）

| 路径 | 内容 |
|---|---|
| `AGENT_HANDOFF.md` | 交接与执行记录（最新变更表 + 产品方向决策 §6 + 执行批次状态）——**新人/新 Agent 首选** |
| `0.10.0-requirement-analysis.md` | 0.10.0 需求整理（18 条 issue 汇总，来源 `0.9.0.issue.txt`） |
| `0.10.0-task-split.md` | 0.10.0 任务拆分（13 个工作包 / 4 个迭代 / 验收口径） |
| `0.10.0-work-breakdown.md` | 0.10.0 工作包细化（函数/端点/数据/交互/测试粒度 + PR 拆分） |
| `engineering/` | 工程规范：借鉴优秀工程与最小改动规则（`borrow-rules.md`） |
| `adr/` | 架构决策记录（ADR-001 SQLite 存储 / 002 FTS5+jieba 检索 / 003 Pydantic 契约 / 004 FSRS 调度 / 005 迁移备份） |

## 历史层（docs/archive/）

| 子目录 | 内容 |
|---|---|
| `design-specs/` | 已落地的版本设计规格（v05 升级 / v06 / v07 学习闭环 / 2026-08-29 IA 重组与卡片刷题） |
| `product/` | 产品交接文档（Agent 交接配置 JSON + PRD v1.0 Markdown，外部来源归档） |
| `reviews/` | 已完结里程碑的审查全套：`s1-2026-08-27/`（S1 批）、`2026-08-28/`（全链路 UX + R2）、`2026-08-29/`（差距审查 PRD） |
| `spikes/` | 技术预研（K3 大纲抽取 / K4 图像嵌入 / K5 并发复现 / build_syllabus_seed + k3_out 输出） |
| `workbuddy-memory/` | 外部工具（workbuddy-ai）的历史工作记忆 |

> 详见 `docs/archive/README.md`。

## 约定

- 新文档先判断归属：**进行中的工作**放顶层；**已实现/已完结**的放 `docs/archive/` 对应分类（按日期建子目录）。
- 移动文档后必须 `grep` 全仓同步交叉引用（本目录 `AGENT_HANDOFF.md`、被移文件内部引用、`docs/archive/README.md`）。
- CHANGELOG 是流水日志，**不改写历史条目**（保留当时路径引用）。
