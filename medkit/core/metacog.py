"""EP-01 · P5 复盘层：元认知统计（**全部纯函数**，无 IO）。

指标出处（教育心理学 / 信号检测论成熟概念，非自创）：
- **校准曲线** calibration curve：按 confidence 分桶算实际正确率，看"自评准不准"。
- **Brier 分数**：`mean((conf/5 - is_correct)^2)`，越低越好；衡量概率预测质量的标准指标。
- **JOL 偏差**（Judgement of Learning）：自评均值 − 实际正确率，正数 = 过度自信。
- **跨轮次迁移矩阵**：同一 kp_id 在三轮里的 error_tag 变化。tag 变了 = 上轮修对了方向；
  tag 三轮不变 = 修补动作无效，需换方法。
- **标签×科目热力图**：哪一科集中犯哪类错。生化=记忆偏差 vs 生理=机制混淆，
  两类问题处理方式完全不同（前者靠间隔重复，后者靠重讲机制）。

设计取舍：**不落表、不缓存**。理由——这些指标随查询口径变（全量/近一月/单科），
落表就要处理缓存失效；错题量级在万级以下，实时算的成本可忽略（对比
`library.add_mistake` 实测：SQL 轨单条写入 0.3ms）。纯函数还带来一个好处：
可以被任意样本集喂数据做单元测试，不需要构造数据库。

无数据行的显式语义（不静默补零）：
- 未填 confidence 的错题**不进**校准曲线（它没有自评，混进去会把曲线拉平）；
  但会在返回值里以 `unrated` 报出条数，让调用方知道"有多少被排除了"。
- `correct` 缺失（None）同样排除，单独计入 `unknown_result`。
"""

from __future__ import annotations

import math
from typing import Any, Optional

# error_tag 固定 6 类（《总纲》§3.1「第一轮只留 6 个，绝不扩张」）。
# 顺序即展示顺序：从"最需要重讲"到"最需要审题训练"。
ERROR_TAGS: tuple[str, ...] = (
    "知识盲区",   # 完全不知道
    "记忆偏差",   # 记得但记错了数值/方向/分级
    "机制混淆",   # 因果链接错
    "概念偷换",   # 把相似概念当成同一个（如顺应性/弹性阻力）
    "审题失误",   # 漏了"不""首选""最可能"
    "推理跳步",   # 前提对，中间缺了一环
)
ERROR_TAG_SET = frozenset(ERROR_TAGS)

# 复习轮次（《总纲》§1.1 的时间线）
ROUNDS: tuple[str, ...] = ("早鸟轮", "跟课轮", "强化轮", "冲刺轮")

# 校准曲线的桶边界（confidence 1-5）
CONF_LEVELS: tuple[int, ...] = (5, 4, 3, 2, 1)

# 《总纲》§3.3 的干预阈值：confidence=5 的正确率低于此值 → 说明在凭直觉做题且不自知
OVERCONFIDENCE_CEILING = 0.90

# 样本量下限：少于这个数的桶不给结论（避免 "1 题 100%" 这种噪声被当信号）
MIN_BUCKET_N = 3


def _is_correct(card: dict[str, Any]) -> Optional[bool]:
    """从错题记录提取"是否答对"。

    口径与 `library.compute_score` 一致：以 `correct` 字段为准；
    缺失时**不猜**（返回 None 并计入 unknown_result），而不是默认 False——
    默认 False 会把"未作答"当成"答错"，系统性拉低正确率。
    """
    v = card.get("correct")
    if v is None:
        return None
    return bool(v)


def _confidence(card: dict[str, Any]) -> Optional[int]:
    """提取 confidence，非法值（越界/非数）视为未填。"""
    v = card.get("confidence")
    if v is None or v == "":
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


# --------------------------------------------------------------------- 校准曲线
def calibration(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """校准曲线 + Brier + JOL 偏差。

    返回：
    - `buckets`：从 conf=5 到 1，每桶 {confidence, n, correct, accuracy, expected, gap}
    - `brier`：Brier 分数（None = 无有效样本）
    - `jol_bias`：自评均值(归一到 0-1) − 实际正确率（正 = 过度自信）
    - `unrated` / `unknown_result`：被排除的条数（显式报出，不静默丢）
    - `alert`：是否触发《总纲》§3.3 的过度自信干预
    """
    buckets: list[dict[str, Any]] = []
    unrated = 0
    unknown = 0
    pairs: list[tuple[float, bool]] = []      # (conf/5, is_correct)

    for c in cards:
        conf = _confidence(c)
        ok = _is_correct(c)
        if conf is None:
            unrated += 1
            continue
        if ok is None:
            unknown += 1
            continue
        pairs.append((conf / 5.0, ok))

    for level in CONF_LEVELS:
        rows = [p for p in pairs if round(p[0] * 5) == level]
        n = len(rows)
        correct = sum(1 for _, ok in rows if ok)
        acc = round(correct / n, 4) if n else 0.0
        expected = level / 5.0
        buckets.append({
            "confidence": level,
            "n": n,
            "correct": correct,
            "accuracy": acc,
            "expected": expected,
            # gap < 0 = 实际比自评差（过度自信）；> 0 = 过度保守
            "gap": round(acc - expected, 4) if n else 0.0,
            # 样本不足时标 low_sample，前端据此显示为灰色不参与结论
            "low_sample": n < MIN_BUCKET_N,
        })

    brier: Optional[float] = None
    if pairs:
        brier = round(sum((p - (1.0 if ok else 0.0)) ** 2 for p, ok in pairs) / len(pairs), 4)

    jol_bias: Optional[float] = None
    if pairs:
        mean_conf = sum(p for p, _ in pairs) / len(pairs)
        real_acc = sum(1 for _, ok in pairs if ok) / len(pairs)
        jol_bias = round(mean_conf - real_acc, 4)

    # 《总纲》§3.3：conf=5 桶正确率显著低于 90% → 凭直觉且不自知，最需干预
    top = next((b for b in buckets if b["confidence"] == 5), None)
    alert = bool(top and not top["low_sample"] and top["accuracy"] < OVERCONFIDENCE_CEILING)

    return {
        "buckets": buckets,
        "brier": brier,
        "jol_bias": jol_bias,
        "rated": len(pairs),
        "unrated": unrated,
        "unknown_result": unknown,
        "alert": alert,
        "alert_msg": (
            f"自评 5 的题正确率仅 {top['accuracy']:.0%}（<{OVERCONFIDENCE_CEILING:.0%}）："
            "在凭直觉做题且不自知。建议对自评 4-5 的题强制写出推理再对答案。"
            if alert and top else ""
        ),
    }


# ------------------------------------------------------------------ 标签×科目热力图
def heatmap(cards: list[dict[str, Any]], *,
            value: str = "auto") -> dict[str, Any]:
    """标签 × 科目 交叉计数。

    `value` 三选一：
    - `"auto"`（默认）：**人工 tag 优先、AI tag 兜底**。这是日常要看的口径——
      人工标签是用户亲自写下的，AI 标签只是参考；只写了一个时用另一个补上。
    - `"error_tag"`：只看人工归因（AI 未跑或要看"纯人工"视图时用）。
    - `"ai_error_tag"`：只看 AI 归因（用来检验 AI 的判断力）。

    ⚠️ 这里**不把默认设成 `"ai_error_tag"`**：AI 归因是可选步骤，多数错题只有人工 tag，
    默认取 AI 会把大面积行判为"未归类"、热力图看起来全空——而这个失败模式很容易被
    误读成"统计坏了"。默认取 `auto` 保证有 tag 就有统计。
    """
    subjects: list[str] = []
    grid: dict[str, dict[str, int]] = {}
    untagged = 0

    def _pick(c: dict[str, Any]) -> str:
        human = str(c.get("error_tag") or "")
        ai = str(c.get("ai_error_tag") or "")
        if value == "error_tag":
            return human
        if value == "ai_error_tag":
            return ai
        return human if human in ERROR_TAG_SET else ai

    for c in cards:
        subj = str(c.get("subject") or "(未标科目)")
        if subj not in subjects:
            subjects.append(subj)
        tag = _pick(c)
        if tag not in ERROR_TAG_SET:
            untagged += 1
            continue
        grid.setdefault(subj, {})
        grid[subj][tag] = grid[subj].get(tag, 0) + 1

    # 行内找出"最集中的错因"——这正是可执行的洞察（这一科该用什么方法补）
    rows: list[dict[str, Any]] = []
    for subj in subjects:
        counts = grid.get(subj) or {}
        total = sum(counts.values())
        top_tag, top_n = "", 0
        for tag in ERROR_TAGS:
            if counts.get(tag, 0) > top_n:
                top_tag, top_n = tag, counts[tag]
        rows.append({
            "subject": subj,
            "counts": {tag: counts.get(tag, 0) for tag in ERROR_TAGS},
            "total": total,
            "top_tag": top_tag,
            "top_ratio": round(top_n / total, 4) if total else 0.0,
            # 需要全部集中在同一类才给建议，避免小样本噪声
            "advice": _advice_for(top_tag, top_n, total),
        })

    rows.sort(key=lambda r: -r["total"])
    return {
        "tags": list(ERROR_TAGS),
        "subjects": subjects,
        "rows": rows,
        "untagged": untagged,
        "field": value,
    }


_TAG_ADVICE = {
    "知识盲区": "整块没学过——回教材补该章节，别指望刷题解决",
    "记忆偏差": "记得但记错 → 上间隔重复（记忆卡），不是重听",
    "机制混淆": "因果链错了 → 重讲机制并做「机制→病理→临床」串联",
    "概念偷换": "把相似概念当同一个 → 做对比表，成对记",
    "审题失误": "读题问题 → 专项练「不/首选/最可能」标记，与知识无关",
    "推理跳步": "前提对、中间缺环 → 补中间推导，写完整推理链",
}


def _advice_for(tag: str, top_n: int, total: int) -> str:
    """仅当某类占比 ≥60% 且样本 ≥MIN_BUCKET_N 才给建议（否则噪声）。"""
    if not tag or total < MIN_BUCKET_N or top_n / total < 0.6:
        return ""
    return _TAG_ADVICE.get(tag, "")


# --------------------------------------------------------------------- 跨轮次迁移
def migration(events: list[dict[str, Any]], *, kp_ids: Optional[list[str]] = None) -> dict[str, Any]:
    """跨轮次错误类型迁移矩阵（按 **流水** 算，不按快照）。

    输出 `chains`：每个 kp_id 一条按 ROUNDS 顺序的 tag 链，例如
    `{"kp_id": "kp1_x", "tags": {"早鸟轮": "机制混淆", "强化轮": "记忆偏差"},
      "verdict": "改善" | "未变" | "恶化" | "数据不足", "msg": "..."}`

    判据（《总纲》§3.3）：
    - tag 发生变化 → 上一轮修对了方向（`改善`，但仅当**最新 tag 比最旧的"更靠后"**
      才算改善——按 ERROR_TAGS 的顺序，"审题失误"在后表示问题更表层、更易修）；
    - 三轮同一个 tag → `未变`：修补动作无效，需换方法（这是最重要的告警）；
    - 只有一轮数据 → `数据不足`，不给结论。
    """
    by_kp: dict[str, dict[str, str]] = {}
    for e in events:
        if str(e.get("event") or "") not in ("", "answer", "attribution"):
            continue
        kid = str(e.get("kp_id") or "")
        tag = str(e.get("error_tag") or "")
        rnd = str(e.get("round") or "")
        if not kid or not rnd or tag not in ERROR_TAG_SET:
            continue
        by_kp.setdefault(kid, {})
        # 同一轮多次作答 → 取**最后一次**的 tag（最新的自我认知）
        by_kp[kid][rnd] = tag

    chains: list[dict[str, Any]] = []
    order = {t: i for i, t in enumerate(ERROR_TAGS)}
    for kid, tags in by_kp.items():
        if kp_ids is not None and kid not in kp_ids:
            continue
        seq = [(r, tags[r]) for r in ROUNDS if r in tags]
        if len(seq) < 2:
            chains.append({"kp_id": kid, "tags": tags, "verdict": "数据不足",
                           "msg": "只录了一轮，看不出迁移"})
            continue
        first_tag, last_tag = seq[0][1], seq[-1][1]
        if first_tag == last_tag:
            chains.append({
                "kp_id": kid, "tags": tags, "verdict": "未变",
                "msg": f"跨 {len(seq)} 轮仍是「{first_tag}」——修补动作无效，换方法",
            })
        elif order.get(last_tag, 99) > order.get(first_tag, 99):
            chains.append({
                "kp_id": kid, "tags": tags, "verdict": "改善",
                "msg": f"「{first_tag}」→「{last_tag}」，方向对了（问题转向更表层）",
            })
        else:
            chains.append({
                "kp_id": kid, "tags": tags, "verdict": "恶化",
                "msg": f"「{first_tag}」→「{last_tag}」，问题变得更底层，需回炉",
            })

    stuck = [c for c in chains if c["verdict"] == "未变"]
    return {
        "chains": chains,
        "stuck": stuck,
        "stuck_count": len(stuck),
        "rounds": [r for r in ROUNDS if any(r in c["tags"] for c in chains)],
    }


# --------------------------------------------------------------------- 标签一致性
def tag_agreement(cards: list[dict[str, Any]]) -> dict[str, Any]:
    """人工 tag vs AI tag 的一致率，并列出**不一致的题目**。

    不一致 = 自我认知有偏差的地方，这类题目价值最高（《总纲》§3.2）。
    """
    both: list[dict[str, Any]] = []
    for c in cards:
        human = str(c.get("error_tag") or "")
        ai = str(c.get("ai_error_tag") or "")
        if human in ERROR_TAG_SET and ai in ERROR_TAG_SET:
            both.append({"id": c.get("id"), "human": human, "ai": ai,
                         "question": str(c.get("question") or "")[:60]})
    if not both:
        return {"compared": 0, "agreed": 0, "rate": None, "mismatches": []}
    mismatches = [b for b in both if b["human"] != b["ai"]]
    agreed = len(both) - len(mismatches)
    return {
        "compared": len(both),
        "agreed": agreed,
        "rate": round(agreed / len(both), 4),
        "mismatches": mismatches,
    }


# --------------------------------------------------------------------- 减法清单
def subtract_plan(cards: list[dict[str, Any]], *, freq: Optional[dict[str, int]] = None,
                  bottom_ratio: float = 0.25) -> dict[str, Any]:
    """减法清单：告诉用户"这块别看"。

    打分 = `题量 × 历史正确率 × 近三年频次`，**升序**取末尾 `bottom_ratio` 比例的章节
    标为"本周不排"。设计意图与 `gap.plan`（推荐该学什么）**方向相反**，二者互补而非替代。

    `freq`：{chapter: 频次}，来自 `realexam_freq` 表（生成链留给本流水线最有价值的资产）。
    缺失时按 1.0 中性处理，并在返回里标 `freq_missing=True`——**不假装有数据**。

    为什么正确率高的章节要"别看"：复习时间是零和的。正确率 92% 的章节再刷一遍的
    边际收益，远低于正确率 55% 的章节。这不是"放弃"，是把时间挪到刀刃上。
    """
    freq = freq or {}
    by_ch: dict[str, dict[str, Any]] = {}
    for c in cards:
        ch = str(c.get("chapter") or "(未标章节)")
        subj = str(c.get("subject") or "")
        key = f"{subj}|{ch}" if subj else ch
        row = by_ch.setdefault(key, {"subject": subj, "chapter": ch, "n": 0, "correct": 0, "unknown": 0})
        row["n"] += 1
        ok = _is_correct(c)
        if ok is None:
            row["unknown"] += 1
        elif ok:
            row["correct"] += 1

    rows: list[dict[str, Any]] = []
    for key, row in by_ch.items():
        rated = row["n"] - row["unknown"]
        acc = (row["correct"] / rated) if rated else 0.0
        f = float(freq.get(row["chapter"], freq.get(key, 1.0)))
        # 频次缺失按中性 1.0；题量用 log 压缩，避免"刷得多"的章节仅因题量大而排到末尾
        score = math.log1p(row["n"]) * (acc if rated else 0.0) * f
        rows.append({
            "key": key, "subject": row["subject"], "chapter": row["chapter"],
            "n": row["n"], "rated": rated, "accuracy": round(acc, 4),
            "freq": f, "score": round(score, 4),
        })

    rows.sort(key=lambda r: r["score"])
    # 用 ceil 而不是 int：`int(2 * 0.25) == 0` → 章节数很少时清单**恒为空**，
    # 功能看起来"没反应"（实现时正是这么踩到的）。向上取整保证只要有章节
    # 就至少给出一个"别看"的建议，让功能在任何规模下都可见。
    cut = max(1, math.ceil(len(rows) * bottom_ratio)) if rows else 0
    for i, r in enumerate(rows):
        r["skip_this_week"] = i < cut
    return {
        "rows": rows,
        "skip": [r for r in rows if r["skip_this_week"]],
        "freq_missing": not freq,
        "cut": cut,
    }
