# ADR-003 · LLM 结构化输出契约：Pydantic 校验 + 自动修复（不引入 instructor）

- 状态：已接受（2026-08-27，S0 决策；落地随 WP-01/02/05）
- 背景：项目已有稳健 `chat_json`（剥围栏/截首 JSON）；大纲抽取、真题考频归一、记忆卡生成等
  新 LLM 输出需要「契约层」——字段缺失/类型错/多余时不应直接落库。
- 决策：新增 `core/schema.py`，定义每类输出的 Pydantic 模型（`model_validate`）：
  校验失败 → 带错误信息重发 1 次修复 → 仍失败按既有「人工复核清单」兜底（不入库）。
  不引入 instructor 包（其价值=response model 封装；本项目已有 chat_json，仅补校验+重试循环）。
- 对标：instructor（567-labs）社区标准做法：response model → pydantic 校验 → 失败重试。
- 验证：契约测试（schema 字段 + 关键不变式：X 型答案升序、image_ref 存在性）进提示词回归层。
- 回退：`core/schema.py` 独立文件，可整体关闭（校验失败直接过 → 等价现状）。
