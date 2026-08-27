# ADR-004 · 复习调度：Scheduler 协议抽象 + py-fsrs 默认、SM-2 保留 legacy

- 状态：已接受（2026-08-27，S0 决策；SPIKE K2 通过；实现随 WP-05）
- 背景：v0.7 复习卡为轻量 SM-2（`core/review.py`：间隔 1→6→×ease、quality<3 relearning）。
  医学记忆卡要长线高效调度，社区与 Anki 23.10+ 默认转向 FSRS。
- 决策：
  - `core/scheduler.py` 定义协议：`schedule(card, quality, now) -> (next_due, ease, interval, state)`；
  - 实现 A：py-fsrs（默认）；实现 B：现有 SM-2 迁移为 legacy 实现（可切，保老卡不废）；
  - 历史卡迁移函数幂等（K2 验证 `from_json/to_json` 往返一致；fsrs 自动 card_id 用自有 id 覆盖）；
  - fsrs-optimizer 用 review_log 离线调参留作 stretch，不在 S0。
- SPIKE K2 证据：fsrs 6.3.2；`Scheduler.review_card(Card(due=now), Rating.Good, review_datetime=now)`
  两次调用 state/due 完全一致（确定性），`Card.to_json()/from_json()` 往返相等；
  仅自动生成的 `card_id`（内部时间戳）不同——迁移时以自有 `id` 为准即可。
- 验证：freezegun 冻结时钟单测（D7，随 WP-05 引入 requirements-dev）。
- 回退：Scheduler 协议切回 SM-2（卡数据不丢）；FSRS 数值异常一键切 legacy。
