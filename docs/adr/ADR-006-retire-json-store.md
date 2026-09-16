# ADR-006 · JSON/SQLite 双轨退役：条件、步骤、回滚与影响文件清单

- 状态：已接受（2026-09-15，U-11）
- 背景：
  - ADR-001 已决策 SQLite 为权威存储，ADR-005 落地了「JSON→SQLite 幂等导入 + 升级前备份 +
    域模块咽喉点路由」。但迁移采用「渐进替换 + 保留回落」策略，**从未设定退役里程碑**。
  - 现状实测：`_store_is_sql()` 双分支遍布 **5 个领域模块**（`library.py` / `review.py` /
    `explain.py` / `cards.py` / `tutor.py`，共 22 处判定）；`write_json_atomic` 出现在
    **8 个模块**（`library`5 · `orchestrator`10 · `explain`4 · `config`3 · `tutor`3 · `cards`2 ·
    `review`2 · `projects`2）。
  - 机制根因：`db.enabled()` 的判定是 `DB_PATH.exists()`——**DB 不存在即回落 JSON 分支**。
    这正是 R5-01「测试污染真实用户库」的机制根因：任何一处路径常量未隔离/未迁移，都会
    静默原子写真实 `~/.medkit/library/*.json`。
  - 结论：JSON 轨目前是**永久兜底**而非过渡态，等于给「静默写坏用户数据」留了一个常开入口。
- 决策：
  1. **退役条件（同时满足才可执行）**
     - `db.import_from_json()` 已在启动路径幂等执行且全部表返回 `imported n` / `skip(done)`
       （无 `skip(no file)` 之外的新增）；
     - 目标用户库 `PRAGMA user_version >= 6`（即已跨过 v1~v6 全部结构迁移）；
     - 连续两个发布版本（≥ v0.11.x）无「JSON 轨写坏数据」类缺陷报告。
  2. **退役动作**
     - `_store_is_sql()` 改为**「DB 不存在即建库」**语义：首次调用即 `db.migrate()` 建库，
       不再回落 JSON；
     - 删除 5 个领域模块的 JSON 回落分支（22 处判定）与对应 `*_FILE` 常量；
     - 删除各模块的 `write_json_atomic` 调用点（保留 `core/fsutil.py` 的实现，供
       `config.json` / 项目 `meta.json` / `slices.json` 等**非库数据**继续使用）。
     - 保留 `config.json`（应用配置，非学习库数据，不在本 ADR 范围内）。
  3. **迁移路径（幂等、可中断、可续跑）**
     - 启动：`db.migrate()` → `db.import_from_json()`（以 id 为键 `INSERT OR REPLACE`）→
       每表成功写 `meta.imported::<table>` 标记 → 原 JSON 改名 `*.pre-db-<ts>.bak`（不回灌）；
     - 该路径 ADR-005 已实现，本 ADR 不新增机制，只把「何时切换为唯一路径」定死。
  4. **回滚方案**
     - 库级：**移走** `medkit.db` / `-wal` / `-shm`（`_store_is_sql()` 判据是 `DB_FILE.exists()`，
       只 DROP 表**回不到 JSON 模式**——表空了但域模块仍走 SQL 轨，界面又变「全空」）
       + 恢复 `*.pre-db-*.bak` 原文件名 → 回到 JSON 模式；
       **已提供可执行入口**：`python pack/rollback-json-track.py`（默认 dry-run，`--yes` 执行，
       db 文件保留改名不删除，执行后自动回查「是否真的回落 JSON 轨」）。
       > 勘误（2026-09-16 R7/V-13）：本节原写「`db.downgrade_to(0)` + 恢复 `.bak` 原文件名」，
       > 但按此执行**回不到 JSON 模式**（`downgrade_to` 只 DROP 表、db 文件仍在），
       > 且 `db.downgrade_to()` 全仓零调用方、无任何可执行入口 —— 文档里的回滚路径实际不可执行。
     - 数据级：ADR-005 的升级前全量备份（`backup_library()`）继续有效；
     - 发布级：退役动作单独成版，不与结构迁移同版发布（出问题可只回退该版）。
- 影响文件清单（退役时需处理）
  | 模块 | 待删除内容 |
  |---|---|
  | `medkit/core/library.py` | `_store_is_sql()`（5 处判定）· `MISTAKES_FILE` / `KNOWLEDGE_FILE` |
  | `medkit/core/review.py` | `_store_is_sql()`（4 处）· `REVIEW_QUEUE_FILE` |
  | `medkit/core/explain.py` | `_store_is_sql(path)`（4 处）· `EXPLAINS_FILE` / `SLICE_INDEX_FILE` |
  | `medkit/core/cards.py` | `_store_is_sql()`（5 处）· `CARDS_FILE` |
  | `medkit/core/tutor.py` | `_store_is_sql()`（4 处）· `TUTOR_SESSIONS_FILE` |
  | `medkit/core/db.py` | `enabled()` 语义改为「可建库」；`import_from_json()` 转为一次性迁移工具 |
  | `tests/conftest.py` | `_FILE_CONSTANTS` 隔离清单可随常量删除而收敛（R5-01 哨兵保留） |
- 验证：
  - 退役后 `grep -rn "_store_is_sql\|MISTAKES_FILE\|KNOWLEDGE_FILE\|REVIEW_QUEUE_FILE\|EXPLAINS_FILE\|CARDS_FILE\|TUTOR_SESSIONS_FILE" medkit/` 零命中（除本 ADR 与 CHANGELOG 历史条目）；
  - `tests/test_db.py` / `test_library_sql.py` / `test_domain_sql.py` 全绿；
  - 全新用户目录首启：不产生任何 `library/*.json`，直接建 `medkit.db`。
- 后果：
  - 正面：堵住「回落写真实路径」这一结构性数据事故入口；域模块读写路径单轨，测试隔离面收窄。
  - 负面：失去「删 medkit.db 即回落 JSON」的应急能力——以 `.pre-db-*.bak` 与全量备份替代。
- 关联：ADR-001（SQLite 存储）· ADR-005（迁移备份）· R5-01（测试污染真实用户库）·
  `docs/reviews/工程质量独立核查报告_2026-09-15.md` §3.4（双轨无退役时间表）。
