# ADR-005 · 数据迁移与回滚策略：JSON→SQLite 幂等导入 + 升级前备份 + 域模块咽喉点路由

- 状态：已接受（2026-08-27，S0）
- 背景：v0.7 五份 JSON（mistakes/knowledge/review_queue/explains/tutor_sessions）是活数据；
  切换 SQL 必须「数据永远可退」且测试/旧代码不受影响。
- 决策：
  1. **幂等导入**：`db.import_from_json()` 以 id 为键 `INSERT OR REPLACE`；每表成功即写
     `meta.imported::<table>` 标记并重跑 `skip(done)`；导入成功后原 JSON 改名
     `*.pre-db-<ts>.bak`（不再回灌，防止「旧文件覆盖新库」）。
  2. **升级前备份**：`db.migrate()` 升级前 `backup_library()` 复制全量 JSON + 旧 db。
  3. **域模块咽喉点路由**：每个域模块（library/review/explain/tutor）的 `_load/_save` 是唯一读写点；
     SQL 模式（模块 `DB_FILE` 存在）走行级事务，否则回落 JSON —— 现有测试 monkeypatch 文件常量
     自动落在 JSON 路径，174 项既有测试零改动全绿；新 SQL 端到端测试（test_db /
     test_library_sql / test_domain_sql）覆盖库模式。
  4. **写路径统一**：变更为 `_store()` 上下文（SQL=单事务 BEGIN IMMEDIATE；JSON=模块 RLock 串行+原子写），
     修复「读-改-写」分离窗口导致的丢失更新（K5）。
- 附修（同源缺陷）：`_new_id`/卡片 id/会话 id 原为裸毫秒时间戳——Windows 时间片精度下同毫秒撞 id
  导致判重误覆盖；改为「时间戳+进程内单调序号」（library/review/tutor 三处）。
- 验证：迁移升级/回滚幂等、备份存在性、导入幂等（重跑不重复、meta 标记、改名不回灌）、
  并发 100/100 无丢失（`tests/test_db.py`、`tests/test_library_sql.py`、`tests/test_domain_sql.py`）。
- 回退：JSON→SQLite——恢复 `.pre-db-*.bak` 原文件名即回到 JSON 模式；SQLite 迁移——`downgrade_to(0)`
  配回滚 SQL；启用/停用——删除 medkit.db 即整体回落 JSON（域模块路由自动感知）。
