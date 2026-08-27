# ADR-001 · 学习库主存储采用 SQLite（WAL + user_version 迁移）

- 状态：已接受（2026-08-27，S0）
- 背景：v0.7 学习库以 JSON 整文件读写（mistakes/knowledge/review_queue/explains/tutor_sessions）。
  SPIKE K5 实测：双窗口并发 grade/record_quiz 各 50 次 → 确定性互撞必丢 1 次、洪泛丢 49/41 次更新，
  窗口「读-改-写」整文件互相覆盖；且多窗口双开、S1 聚合（考频/覆盖度/易混对）需要索引与 join。
- 决策：`~/.medkit/library/medkit.db`，SQLite（stdlib `sqlite3`）+ WAL + `PRAGMA user_version` 迁移；
  JSON 保留为「兼容读 + 一次性导入源」（导入后改名 `*.pre-db-<ts>.bak`，不回灌）。
- 对标：Anki / Zotero / Logseq / Obsidian 全部本地 SQLite —— 本地优先应用社区事实标准。
- 存储模型：每表 `id TEXT PRIMARY KEY + data TEXT(整条 JSON) + 少量查询列`。
  JSON→行→dict 无损往返 → 域模块 dict 语义 100% 保持、对外函数签名零改动（routers 零改动）。
- 并发模型：写操作统一 `BEGIN IMMEDIATE`（写锁先行）——同进程多线程读-改-写串行化；
  连接按线程缓存（`get_conn`），WAL + busy_timeout=30s。
- 验证：K5 复现脚本 `docs/spikes/K5_concurrency_repro.py`；`tests/test_db.py`（迁移幂等/回滚/备份/导入幂等/并发）；
  `tests/test_library_sql.py` + `tests/test_domain_sql.py`（公共 API 并发 100/100 无丢失）。
- 回退：每版迁移升级前自动备份 library（JSON + 旧 db → `*.pre-db-<ts>.bak`）；`downgrade_to(0)` 配回滚 SQL；
  域模块保留 JSON 回落路径（测试/导入源兼容），关闭 db 文件即回落。
