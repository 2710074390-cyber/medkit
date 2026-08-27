# ADR-002 · 切片检索用 SQLite FTS5 + jieba 预分词列（零向量库）

- 状态：已接受（2026-08-27，S0；SPIKE K1 通过）
- 背景：讲解/检索 grounding 需要切片级命中（中文），现状为 `explain.py` 内存 bigram 打分
  （`_retrieve_hits`，对实体/错题题干可用但无索引、无 BM25 排序、全量扫描）。
- 决策：
  - `slices_fts`（FTS5 虚拟表：`subject UNINDEXED, text, tokens`）作为 S1 切片检索辅表；
  - tokens 列由 jieba 预分词写入（`tokenize=simple` 缺省，中文经分词列检索）——
    「无原生扩展」的标准做法，规避自制 tokenizer 的编译/打包风险；
  - 现有 bigram 打分保留为兜底（FTS5 不可用/异常时）。
- SPIKE K1 证据：本机 Python 3.12 + sqlite 3.49.1 FTS5 可用（CREATE VIRTUAL TABLE + MATCH/OR/NOT 语法验证通过）；
  jieba 0.42.1 对 ~2.9 万字（38 块）分词 0.66s（含 0.6s 首次词典加载；词数 10,930）；
  分 10 块建 FTS5 索引 0.042s —— 1 万字切片远低于 1s 阈值。PyInstaller 复用同一 sqlite3 DLL，随包无风险
  （CI 增加 `pip check` + FTS5 建表冒烟作守门）。
- 验证：`docs/spikes/`（K1 基准输出见 ADR-001 引用脚本）；`tests/test_db.py` 断言 `slices_fts` 存在。
- 回退：bigram + 内存 BM25（现状路径零改动）。
