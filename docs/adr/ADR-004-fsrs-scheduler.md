# ADR-004 · 复习调度：Scheduler 协议抽象 + py-fsrs 默认、SM-2 保留 legacy

- 状态：**已实现**（2026-08-27 决策；2026-10-27 重新接线落地，见下方时间线）
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

---

## 时间线：决策与实现之间隔了两个月（2026-10-27 补记）

| 时间 | 事件 |
|---|---|
| 2026-08-27 | 本 ADR 决策通过；`core/scheduler.py` + `core/cards.py` + `core/review.py` 落地（随 WP-05 医学记忆卡） |
| 2026-10-15 | v0.15 奥卡姆精简 T2：实测 `cards=0` / `review_cards=0` / `explains=0` / `tutor_sessions=0`（三域零使用），**删 `core/{explain,tutor,cards,review,scheduler}.py`** |
| 2026-10-15 | SM-2 的纯函数搬进 `core/vault_index.py`（「一个算法一份实现」）⇒ 线上只剩 SM-2 |
| 2026-10-27 | 审计发现：**FSRS 从未接到线上路径**；`core/bank.py` 仍把排期委托给已删除的 `review`/`cards` 域（悬空引用）。本 ADR 重新接线 |

**重新接线的落点**（v0.22.0 · 阶段 1）

- `core/scheduler.py` 恢复并成为**全部复习调度数学的单一真相来源**：FSRS（默认）+ SM-2（legacy）。
- SM-2 的常量与算法从 `core/vault_index.py` 搬入本模块（`vault_index` 改为 import），
  **数值与公式一字未改**——`tests/test_scheduler.py` 用**手写的旧实现**逐值比对钉死。
- 依赖零新增：`fsrs==6.3.2` 早在 `requirements.txt` 里（实现没了但依赖还在）。

**实测标定的参数（fsrs 库默认值不能照搬）**

fsrs 的默认参数是给「同一天内多次复习」设计的，直接用会出三件事：学习步长以**秒**为单位；
`maximum_interval=36500` ⇒ 实测连击 6 次「记住」的间隔变成 8/66/397/1875/7265/23933 天；
`enable_fuzzing=True` 会给 due 加随机偏移 ⇒ **同输入不同输出**，行为不可证伪。

本仓采用：`learning_steps=(10 分钟, 1 天)`、`relearning_steps=(10 分钟,)`、
`maximum_interval=180`（半年封顶）、`desired_retention=0.9`、`enable_fuzzing=False`。
标定对照（连续「记住」6 次的间隔天数）：SM-2 `1→6→15→38→95→238`；FSRS `1→7→32→118→180→180`。
失败后两者都**当天**重来。

**quality(0~5) → FSRS 四档**：`≤1 → Again`、`2 → Hard`、`3~4 → Good`、`5 → Easy`。
「记住」映射到 Good 而非 Easy 是刻意的：Easy 会让新卡**直接毕业**（首次到期 8 天），
而 SM-2 的首次「记住」只排 1 天——体感上像「白复习了」。

**踩过的坑（写下来免得再踩）**

1. `fsrs.State` 的枚举值**不是** `Learning=1 / Relearning=2 / Review=3`，实测是
   `Learning=1 / **Review=2** / Relearning=3`。硬编码数值 ⇒ 卡片状态被静默错判
   （第二次复习被当成 lapse）。**一律用枚举成员或 `.name`，别硬编码 value。**
2. fsrs 6.3.2 要求 `review_datetime` **必须是 timezone-aware 的 UTC**；naive 会被拒。
3. `fsrs.Card(card_id=None)` 会用时间戳自动生成 card_id ⇒ 同输入不同输出。
   本仓用自有 `id` 的 sha1 前 8 位派生稳定整数（ADR 原文「以自有 id 为准」的落地）。
4. 历史 SM-2 卡切到 FSRS 时**必须播种**（`interval→stability`、`ease→difficulty`），
   且播种时 `last_review` 取自 `last_tried`——否则 FSRS 算不出「距上次过了多久」，
   稳定度会退化成默认值。

**守护**：`tests/test_scheduler.py`（33 项）——SM-2 逐值一致、FSRS 确定性、阶梯形状、
上限生效、legacy 播种、两套算法下 vault 语义（`reps`/`lapses`/`state`/`status`）一致、
切算法不丢字段、接口层契约。
