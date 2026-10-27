"""复习调度器（ADR-004 重新接线 · v0.22.0 阶段 1）。

## 为什么有这一个模块

2026-10-27 审计发现：ADR-004（2026-08-27）已决策「`core/scheduler.py` 定义协议、
默认实现 py-fsrs，SM-2 降为 legacy 可切」，但 v0.15 奥卡姆精简 T2 把
`core/{cards,review,scheduler}.py` 整个删掉了（当时实测三域零使用）——
**FSRS 从未接到线上路径**；`core/bank.py` 至今仍把「何时该复习」委托给
已经不存在的 `review` / `cards` 域（悬空引用，见本文件末尾的注释修复）。

本模块是那次删除留下的坑的正式填平，也是**全部复习调度数学的单一真相来源**：
SM-2 的常量与算法搬到这里（`core/vault_index.py` 改为从本模块 import，
旧名字保留可 import，不破坏任何调用方），FSRS 作为新实现并列。

## 两种实现，可一键切换

| 实现 | 角色 | 说明 |
|---|---|---|
| `FSRSScheduler` | 默认 | `fsrs==6.3.2`（requirements.txt 早已 pin ⇒ **零新依赖**） |
| `SM2Scheduler` | legacy | 与原 `core/vault_index.py` 的 SM-2 常量**逐值一致**（`tests/test_scheduler.py` 用参数化黄金值钉死） |

## 参数为什么是这些（实测标定，2026-10-27）

fsrs 库的默认值是给「同一天内多次复习」设计的：
`learning_steps=(60s, 600s)`、`desired_retention=0.9`、`maximum_interval=36500`、
**`enable_fuzzing=True`**。直接照搬到本仓会出三件事：

1. 学习步长以**秒**为单位 ⇒ 一道错题「记住」10 分钟后再冒出来，违背「今日复习」
   的一日一卡语义；
2. `maximum_interval=36500` ⇒ 实测连续 6 次「记住」的间隔是
   8 → 66 → 397 → 1875 → 7265 → 23933 天。对备考场景是灾难：考完试都轮不到第 6 次。
3. `enable_fuzzing=True` 给 due 加随机偏移 ⇒ **同输入不同输出**，破坏可证伪性
   （本仓的规矩：凡行为必有能钉死它的守卫）。

因此本模块用：

- `learning_steps=(10 分钟, 1 天)`——失败当天重来（对齐 SM-2 的 relearning→今天），
  「记住」进 1 天（对齐 SM-2 的第一次 interval=1）；
- `relearning_steps=(10 分钟,)`——同上；
- `maximum_interval=180`——半年封顶：医学生的复习周期以月计，超过半年对备考无意义；
- `desired_retention=0.9`（可配）——FSRS 社区默认；
- `enable_fuzzing=False`——确定性。

标定结果（连续「记住」6 次的间隔天数）：

    SM-2 : 1 → 6 → 15 → 38 → 95 → 238
    FSRS : 1 → 7 → 32 → 118 → 180(封顶) → 180

失败后（Again）两者都**当天**重来（SM-2 `due`=今天；FSRS relearning 10 分钟 ⇒ 日期仍是今天）。

## 字段职责（切算法不丢数据）

| 字段 | 归属 | 语义 |
|---|---|---|
| `due` | 两者共同 | 下次复习日期（由算法算出） |
| `reps` / `lapses` / `state` / `status` | **vault 语义** | 连续通过次数 / 累计失败 / 卡片状态 / 掌握状态。**两套算法下语义完全一致** |
| `ease` / `interval` | 仅 SM-2 | 切到 FSRS 时**冻结**（保留以便回滚），FSRS 路径不再更新 |
| `stability` / `difficulty` / `fsrs_state` / `fsrs_step` / `last_review` | 仅 FSRS | 切回 SM-2 时原样保留，不参与 SM-2 计算 |

`state` 的取值沿用 SM-2 口径（`relearning` / `learning` / `review`），
FSRS 自己的卡片状态另存 `fsrs_state`——**vault 侧的老字段语义一律不变**，
这样切换算法不会让错题本、薄弱项表、「已掌握」判定出现任何行为差异。
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Protocol

from . import config as cfg

__all__ = [
    "SCHEDULERS",
    "INIT_EASE",
    "MIN_EASE",
    "GRADE_PASS",
    "INTERVAL_FIRST",
    "INTERVAL_SECOND",
    "Scheduler",
    "SM2Scheduler",
    "FSRSScheduler",
    "get_scheduler",
    "sm2_update_ease",
    "sm2_next_intervals",
]

# 两种算法名（配置项 `scheduler` 的合法取值）
SCHEDULERS = ("fsrs", "sm2")

# ---------------------------------------------------------------- SM-2 常量
# 2026-10-27 从 core/vault_index.py 搬来（那里改为从本模块 import）。
# 数值**一字未改**——迁移的目的是「数学只有一个出处」，不是改行为。
INIT_EASE = 2.5
MIN_EASE = 1.3
GRADE_PASS = 3          # quality ≥ 3 记「记得」
INTERVAL_FIRST = 1      # 第一次记住 → 1 天
INTERVAL_SECOND = 6     # 第二次记住 → 6 天

# ---------------------------------------------------------------- FSRS 参数
# 见模块 docstring「参数为什么是这些」。
FSRS_LEARNING_STEPS = (timedelta(minutes=10), timedelta(days=1))
FSRS_RELEARNING_STEPS = (timedelta(minutes=10),)
FSRS_MAX_INTERVAL_DEFAULT = 180
FSRS_RETENTION_DEFAULT = 0.9


# ================================================================ 纯函数（SM-2）
def sm2_update_ease(ease: float, quality: int, passed: bool) -> float:
    """SM-2 记忆难度更新。passed → 小幅增减；failed → 固定 −0.2。"""
    if passed:
        e = ease + (0.1 - (5 - max(0, min(quality, 5))) * (0.08 + (5 - quality) * 0.02))
    else:
        e = ease - 0.2
    return max(MIN_EASE, round(e, 2))


def sm2_next_intervals(ease: float, reps: int, interval: int) -> tuple[int, int]:
    """SM-2 间隔推进：返回 (new_interval, new_reps)。连记两次后按前隔 × ease 增长。"""
    reps += 1
    if reps == 1:
        return INTERVAL_FIRST, reps
    if reps == 2:
        return INTERVAL_SECOND, reps
    return max(int(round(interval * ease)), interval + 1), reps


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _as_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f


# ================================================================ 协议
class Scheduler(Protocol):
    """复习调度器协议。ADR-004 定的形状，实现 A=py-fsrs / 实现 B=SM-2 legacy。"""

    name: str

    def schedule(self, state: dict[str, Any], quality: int,
                 now: Optional[datetime] = None) -> dict[str, Any]:
        """推进一次复习，返回**完整**的卡片状态（未由本算法接管的字段原样带回）。

        `state` 是卡片当前状态（vault front matter 的相关字段子集 + `id`）。
        返回值至少含 `due` / `reps` / `lapses` / `state`。
        """
        ...


# ================================================================ SM-2 实现
class SM2Scheduler:
    """SM-2（legacy）。行为与 2026-10-27 之前的 `vault_index._grade_card_of` **逐值一致**。"""

    name = "sm2"

    def schedule(self, state: dict[str, Any], quality: int,
                 now: Optional[datetime] = None) -> dict[str, Any]:
        now = now or datetime.now()
        q = max(0, min(int(quality), 5))
        passed = q >= GRADE_PASS
        ease = sm2_update_ease(_as_float(state.get("ease"), INIT_EASE) or INIT_EASE,
                               q, passed)
        interval, reps = sm2_next_intervals(ease, _as_int(state.get("reps")),
                                            _as_int(state.get("interval")))
        lapses = _as_int(state.get("lapses"))
        if passed:
            card_state = "review" if reps >= 2 else "learning"
        else:
            interval, reps = 0, 0
            card_state = "relearning"
            lapses += 1
        due = (now.date() + timedelta(days=interval)).isoformat()
        out = dict(state)
        out.update({
            "due": due,
            "ease": ease,
            "interval": interval,
            "reps": reps,
            "lapses": lapses,
            "state": card_state,
        })
        return out


# ================================================================ FSRS 实现
class FSRSScheduler:
    """FSRS-6（默认）。用 `fsrs` 库，参数见模块 docstring。

    **为什么自己先播种（`_seed_from_legacy`）**：本仓历史上跑的是 SM-2，
    用户已有的错题文件里只有 `ease` / `interval` / `reps` / `lapses`，没有 FSRS 的
    `stability` / `difficulty`。不播种 ⇒ 切到 FSRS 的第一张卡会被当成**全新卡**，
    用户刚复习完的错题又被安排到 1 天后重来（体感上像「白复习了」）。
    播种是**近似映射**（SM-2 的 ease/interval 换算成 FSRS 的 stability/difficulty），
    标定即失真，几个来回后由 FSRS 自己的参数接管——这是刻意的：宁可初值粗糙，
    也不要把用户的复习历史整个丢掉。
    """

    name = "fsrs"

    def __init__(self, retention: float = FSRS_RETENTION_DEFAULT,
                 max_interval: int = FSRS_MAX_INTERVAL_DEFAULT) -> None:
        self.retention = _clamp(float(retention), 0.70, 0.98)
        self.max_interval = max(1, int(max_interval))

    # -- 内部：从 state 还原 fsrs.Card -------------------------------
    @staticmethod
    def _card_id(state: dict[str, Any]) -> int:
        """用自有 id 派生一个**稳定的整数** card_id。

        fsrs 库的 `Card(card_id=None)` 会用时间戳自动生成 ⇒ 同输入不同输出
        （fsrs 的 fuzz 也以 card_id 为种子 ⇒ 不固定 card_id 就无法保证确定性）。
        ADR-004 写的是「以自有 id 为准」，这里就是那个「为准」。
        """
        raw = str(state.get("id") or "medkit")
        return int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8], 16)

    @staticmethod
    def _seed_from_legacy(state: dict[str, Any]) -> dict[str, Any]:
        """历史 SM-2 卡 → FSRS 初值（近似）。没复习过的卡不播种（当全新卡）。"""
        reps = _as_int(state.get("reps"))
        lapses = _as_int(state.get("lapses"))
        if reps <= 0 and lapses <= 0:
            return {}
        interval = _as_float(state.get("interval")) or 1.0
        ease = _as_float(state.get("ease"), INIT_EASE) or INIT_EASE
        # ease 2.5（默认）→ difficulty 5.0（中性）；ease 1.3（最难）→ 7.9；
        # ease 2.8（最易）→ 4.3。线性且单调，够用。
        difficulty = _clamp(11.0 - 2.4 * ease, 1.0, 10.0)
        return {
            "stability": max(interval, 0.1),
            "difficulty": difficulty,
            "fsrs_state": "review" if reps >= 2 else "learning",
        }

    def _build_card(self, state: dict[str, Any], now_utc: datetime):
        import fsrs  # 局部导入：SM-2 legacy 路径不该因缺 fsrs 而炸

        seed = self._seed_from_legacy(state)
        stability = _as_float(state.get("stability"), 0.0) or seed.get("stability")
        difficulty = _as_float(state.get("difficulty"), 0.0) or seed.get("difficulty")
        fsrs_state = str(state.get("fsrs_state") or "") or seed.get("fsrs_state") or "learning"
        last_review_raw = str(state.get("last_review") or "")
        if not last_review_raw and seed:
            # 播种出来的卡没有 `last_review`（那是 FSRS 独有的字段），但**复习过**的卡
            # 一定有 `last_tried`（vault 在每次评分时写它）——拿它当 last_review，
            # 否则 FSRS 算不出「距上次过了多久」，稳定度会退化成默认值。
            last_review_raw = str(state.get("last_tried") or "")

        if not stability or not last_review_raw:
            # 全新卡 / 只有 SM-2 历史的卡 → 从零开始（last_review 留空）
            return fsrs.Card(card_id=self._card_id(state),
                              state=fsrs.State.Learning, step=0,
                              stability=None, difficulty=None,
                              due=None, last_review=None)

        try:
            last_review = datetime.fromisoformat(last_review_raw)
        except ValueError:
            last_review = None
        if last_review is None:
            return fsrs.Card(card_id=self._card_id(state), state=fsrs.State.Learning,
                              step=0, stability=None, difficulty=None,
                              due=None, last_review=None)
        if last_review.tzinfo is None:
            last_review = last_review.astimezone(timezone.utc)

        st_map = {
            "learning": fsrs.State.Learning,
            "relearning": fsrs.State.Relearning,
            "review": fsrs.State.Review,
        }
        step = _as_int(state.get("fsrs_step"), 0)
        due_raw = str(state.get("due") or "")
        due = None
        if due_raw:
            try:
                due = datetime.fromisoformat(due_raw)
            except ValueError:
                due = None
            if due is not None and due.tzinfo is None:
                due = due.astimezone(timezone.utc)
        if due is None:
            due = now_utc
        # mypy：stability / difficulty 可能是 None（播种缺失）——显式兜到 0.0，
        # 不要让 `float()` 去接 `Any | None`（本仓 mypy 基线只升不降）。
        stab_val = _as_float(stability, 0.0)
        diff_val = _as_float(difficulty, 0.0)
        return fsrs.Card(card_id=self._card_id(state),
                          state=st_map.get(fsrs_state, fsrs.State.Learning),
                          step=step if step >= 0 else None,
                          stability=stab_val,
                          difficulty=diff_val,
                          due=due,
                          last_review=last_review)

    # -- 协议实现 ---------------------------------------------------
    def schedule(self, state: dict[str, Any], quality: int,
                 now: Optional[datetime] = None) -> dict[str, Any]:
        import fsrs

        now = now or datetime.now()
        q = max(0, min(int(quality), 5))
        passed = q >= GRADE_PASS

        # quality(0~5) → FSRS 四档。
        #
        # **为什么 3 和 4 都映射到 Good**：本仓的复习 UI 是三按钮（忘了 0 / 模糊 2 / 记住 4）
        # + 可选的精确自评（0~5）。若把「记住(4)」映射成 Easy，fsrs 会让新卡**直接毕业**
        # （initial_stability=8.2956 ⇒ 首次「记住」就排到 8 天后），而 SM-2 的首次「记住」
        # 只排 1 天——体感上像「白复习了」。实测：Good 在 learning_steps=(10m, 1d) 下
        # 首次到期恰为 **+1 天**，与 SM-2 一致；Easy 只留给「记得非常牢」(5)。
        if q <= 1:
            rating = fsrs.Rating.Again
        elif q == 2:
            rating = fsrs.Rating.Hard
        elif q <= 4:
            rating = fsrs.Rating.Good
        else:
            rating = fsrs.Rating.Easy

        # naive 的 now 视为**本地时间**（`astimezone` 对 naive 按系统时区解释）⇒ 转 UTC。
        now_utc = now.astimezone(timezone.utc)
        card = self._build_card(state, now_utc)
        sched = fsrs.Scheduler(
            desired_retention=self.retention,
            learning_steps=FSRS_LEARNING_STEPS,
            relearning_steps=FSRS_RELEARNING_STEPS,
            maximum_interval=self.max_interval,
            enable_fuzzing=False,   # 确定性：同输入必须同输出
        )
        card, _ = sched.review_card(card, rating, review_datetime=now_utc)

        # vault 侧的老字段语义与 SM-2 保持一致（切换算法不改变「已掌握」判定）
        reps = _as_int(state.get("reps"))
        lapses = _as_int(state.get("lapses"))
        if passed:
            reps += 1
            card_state = "review" if reps >= 2 else "learning"
        else:
            reps = 0
            card_state = "relearning"
            lapses += 1

        # fsrs.State 的**数值不是** Learning=1/Relearning=2/Review=3（实测是 1/2/3 里
        # Learning=1、**Review=2**、Relearning=3）——硬编码数值会静默错判卡片状态。
        # 一律用枚举的**名字**映射，升级 fsrs 也不会错。
        fsrs_state_name = {"Learning": "learning", "Review": "review",
                           "Relearning": "relearning"}.get(
            getattr(card.state, "name", ""), "learning")

        out = dict(state)
        out.update({
            "due": card.due.astimezone().date().isoformat(),
            "reps": reps,
            "lapses": lapses,
            "state": card_state,
            "stability": round(float(card.stability or 0.0), 4),
            "difficulty": round(float(card.difficulty or 0.0), 4),
            "fsrs_state": fsrs_state_name,
            "fsrs_step": _as_int(getattr(card, "step", 0)),
            "last_review": now.isoformat(timespec="seconds"),
        })
        return out


# ================================================================ 工厂
_CACHE: dict[tuple[str, float, int], Scheduler] = {}


def get_scheduler(name: str = "", retention: float = 0.0,
                  max_interval: int = 0) -> Scheduler:
    """按名字/配置取调度器。

    `name` 留空 ⇒ 读配置 `scheduler`（缺省 `fsrs`）；非法值回退 `fsrs`。
    实例按 (name, retention, max_interval) 缓存——参数变了就是新 key，
    不会拿到旧配置的实例。
    """
    want = (name or "").strip().lower()
    ret = float(retention or 0.0)
    mx = int(max_interval or 0)
    if not want:
        try:
            want = str(cfg.load().get("scheduler") or "fsrs").strip().lower()
        except Exception:  # noqa: BLE001 —— 配置损坏不该让复习功能整体挂掉
            want = "fsrs"
    if want not in SCHEDULERS:
        want = "fsrs"
    if want == "sm2":
        key = ("sm2", 0.0, 0)
        if key not in _CACHE:
            _CACHE[key] = SM2Scheduler()
        return _CACHE[key]
    if not ret:
        try:
            ret = float(cfg.load().get("fsrs_retention") or FSRS_RETENTION_DEFAULT)
        except Exception:  # noqa: BLE001
            ret = FSRS_RETENTION_DEFAULT
    if not mx:
        try:
            mx = int(cfg.load().get("fsrs_max_interval") or FSRS_MAX_INTERVAL_DEFAULT)
        except Exception:  # noqa: BLE001
            mx = FSRS_MAX_INTERVAL_DEFAULT
    key = ("fsrs", round(ret, 4), int(mx))
    if key not in _CACHE:
        _CACHE[key] = FSRSScheduler(retention=ret, max_interval=mx)
    return _CACHE[key]


def describe(name: str = "") -> dict[str, Any]:
    """调度器自述（供 `/api/vault/scheduler` 回给前端展示当前口径）。"""
    sch = get_scheduler(name)
    info: dict[str, Any] = {"name": sch.name, "available": list(SCHEDULERS)}
    if sch.name == "fsrs":
        info["retention"] = getattr(sch, "retention", FSRS_RETENTION_DEFAULT)
        info["max_interval"] = getattr(sch, "max_interval", FSRS_MAX_INTERVAL_DEFAULT)
        info["learning_steps_days"] = [round(s.total_seconds() / 86400, 4)
                                       for s in FSRS_LEARNING_STEPS]
    else:
        info["init_ease"] = INIT_EASE
        info["pass_quality"] = GRADE_PASS
    return info
