"""知识点图谱与学习者模型（v0.22 阶段 2）。

## 这个模块补的是什么

2026-10-27 审计发现的两处结构性断点，根因都在这里：

1. **题目没有稳定的知识点身份**——`bank_items.topic` 与 `QuestionItem.subtopic` 都是
   自由字符串，同一考点跨题库永远是几个不同的字符串 ⇒ 做不了知识追踪、无法聚合、
   无法追溯前置知识。本模块用 `syllabus_items.id`（稳定哈希）做**主键**，
   用 `item_kc` 把题挂上去。
2. **全量作答没进学习者模型**——掌握度只由**错题**聚合（`library._touch_knowledge_in`
   记的 `correct` 是「错题后来被攻下」）。本模块用 `kc_state` 承接**全部**作答（对与错）。

## 三张表的分工

| 表 | 性质 | 说明 |
|---|---|---|
| `item_kc` | **真相**（人工确认过的那部分） | 题 → 知识点（多对多）。`confirmed=1` 的是用户点过头的 |
| `kc_state` | **读模型**（可重建） | 人 × 知识点的状态。可从 `bank_attempts ⋈ item_kc` 重放重建 |
| `kc_edges` | **真相**（人工确认过的那部分） | 知识点之间的关系：前置 / 关联 / 易混 |

**为什么 `kc_state` 是读模型而不是真相**：作答流水（`bank_attempts`）才是真相，
它是 append-only 的。`kc_state` 只是「流水 + 映射」的聚合结果，**删了可以重放重建**——
这与 vault 域「SQLite 只是可弃索引」的既有原则一致，也避免了「同一份作答有两个存储点」。

## 知识追踪为什么用 BKT 而不是深度模型

公开 benchmark 上 BKT 的 AUC 是 0.71–0.76，SAINT 是 0.82–0.85——但**样本量决定选型**：
SAINT 需要 1,000 万次交互，而 MedKit 是**单用户本地**工具，永远到不了。
BKT 的四参数（HMM：已掌握 / 学习 / 失误 / 猜测）可解释、可手调、纯函数可测，
且不需要引入任何 ML 依赖。详见 `docs/0.22.0-知识点图谱与学习者模型_实施方案.md` §0 D1。

## 收缩（经验贝叶斯）为什么必须有

BKT 的后验在只有一次观测时方差极大（答对 → 0.78，答错 → 0.09）。
直接展示会让用户看到「我这一个考点掌握度 78%，那一个 9%」这种噪声。
收缩把后验按权重 `k` 拉回人群先验（`p_prior=0.30`，`k=2`），
n 增大后自动失效（n=20 时先验只占 9%）。
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from datetime import datetime
from typing import Any, Optional

from . import db as dbs

__all__ = [
    "PRIOR_P_MASTERY",
    "PRIOR_WEIGHT",
    "norm_text",
    "rule_candidates",
    "bkt_update",
    "shrink",
    "apply_attempt",
    "calibration_curve",
    "item_stats",
    "backfill",
]

# ---------------------------------------------------------------- 算法常量
# 人群先验：单知识点的首次接触正确率大致在 0.3 上下（执业资格考试的常见通过率区间）。
# 这是一个**可调常量**——敏感性检查见 `tests/test_kc.py::test_shrink_is_sensitive_to_prior`。
PRIOR_P_MASTERY = 0.30
# 先验的等效观测数：n 次观测 + 2 次「先验」的加权平均。
# k=2 ⇒ 第一次观测后先验仍占 2/3（单次答对不能定论）；n=8 后先验只占 20%。
PRIOR_WEIGHT = 2.0

# BKT 四参数默认值（Corbett & Anderson 的经典取值）
P_TRANSIT = 0.25    # 学习率：两次观测之间「学会」的概率
P_SLIP = 0.10       # 失误率：已掌握却答错（粗心）
P_GUESS = 0.20      # 猜测率：未掌握却答对（蒙对）

# 模糊匹配的相似度下限（字符 bigram Jaccard）
FUZZY_MIN_SCORE = 0.45
# 精确归一后相等 ⇒ 视为 exact
EXACT_SCORE = 1.0

_PUNCT_RE = re.compile(r"[\s·、，,。．\.；;：:！!？\?（）()\[\]【】「」『』<>/\\|_\-—–～~*#\"'`]+")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ================================================================ 纯函数
def norm_text(s: str) -> str:
    """归一到可比较形式：全角→半角、去空白与标点、转小写。

    为什么要归一：`慢阻肺的诊断` / `慢阻肺 诊断` / `慢阻肺（诊断）` 指的是同一个考点，
    不归一就永远匹配不上 ⇒ 掌握度被拆成三行。
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", str(s))
    s = _PUNCT_RE.sub("", s)
    return s.lower()


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def rule_candidates(topic: str, kc_pool: list[dict]) -> list[dict]:
    """按 `topic` 在知识点池里找候选。

    `kc_pool` 元素形如 `{"kc_id","item","chapter","subject"}`。
    返回 `[{"kc_id","item","chapter","subject","score","match_type"}]`，按 score 降序。

    `match_type`：
      - `exact`：归一后完全相等（score=1.0）——**可直接自动确认**；
      - `fuzzy`：字符 bigram Jaccard ≥ `FUZZY_MIN_SCORE`——**必须人工确认**。

    **为什么不做语义匹配**：本仓没有向量检索（FTS5+jieba 是既有检索底座），
    而语义近似的两个考点（如「慢阻肺的诊断」vs「慢阻肺的治疗」）恰恰**不能**互相当候选——
    挂错了会让掌握度归到错的知识点上，比不挂更糟。所以只给「足够像」的候选，
    剩下的交给用户点。
    """
    t = norm_text(topic)
    if not t:
        return []
    tb = _bigrams(t)
    out: list[dict] = []
    for kc in kc_pool:
        kb = _bigrams(norm_text(str(kc.get("item") or "")))
        if not kb:
            continue
        if t == norm_text(str(kc.get("item") or "")):
            score, mtype = EXACT_SCORE, "exact"
        else:
            inter = len(tb & kb)
            union = len(tb | kb)
            score = inter / union if union else 0.0
            if score < FUZZY_MIN_SCORE:
                continue
            mtype = "fuzzy"
        out.append({
            "kc_id": str(kc.get("kc_id") or ""),
            "item": str(kc.get("item") or ""),
            "chapter": str(kc.get("chapter") or ""),
            "subject": str(kc.get("subject") or ""),
            "score": round(score, 4),
            "match_type": mtype,
        })
    out.sort(key=lambda r: (-r["score"], r["kc_id"]))
    return out


def bkt_update(p_mastery: float, correct: bool, p_transit: float = P_TRANSIT,
               p_slip: float = P_SLIP, p_guess: float = P_GUESS) -> float:
    """一次作答后的掌握概率（Corbett & Anderson 四参数 HMM，**先学后观测**的次序）。

    次序很重要：先按 `p_transit` 推进（两次观测之间学生可能学会了），再用观测更新。

        prior = p + (1 - p) * transit
        L0 = P(观测 | 未掌握)   L1 = P(观测 | 已掌握)
        post = L1 / (L0 + L1)

    答对：L0 = (1-prior)*guess,  L1 = prior*(1-guess)
    答错：L0 = (1-prior)*(1-slip), L1 = prior*slip

    实测（p=0.30, transit=.25, slip=.10, guess=.20）：
      答对 → 0.783；答错 → 0.091。方向正确（答对升、答错降）。
    """
    p = min(max(float(p_mastery), 0.0), 1.0)
    prior = p + (1.0 - p) * p_transit
    if correct:
        l0 = (1.0 - prior) * p_guess
        l1 = prior * (1.0 - p_guess)
    else:
        l0 = (1.0 - prior) * (1.0 - p_slip)
        l1 = prior * p_slip
    denom = l0 + l1
    if denom <= 0.0:
        return prior
    return l1 / denom


def shrink(p: float, n: int, p_prior: float = PRIOR_P_MASTERY,
           k: float = PRIOR_WEIGHT) -> float:
    """经验贝叶斯收缩：把 n 次观测的后验按权重 k 拉回人群先验。

        p = (n * p + k * p_prior) / (n + k)

    `n=0` ⇒ 返回先验（冷启动不返回 0）。n 增大 ⇒ 先验自动失效。
    """
    n = max(0, int(n))
    if k <= 0:
        return min(max(float(p), 0.0), 1.0)
    out = (n * float(p) + k * float(p_prior)) / (n + k)
    return min(max(out, 0.0), 1.0)


def apply_attempt(state: Optional[dict], correct: bool,
                  p_prior: float = PRIOR_P_MASTERY,
                  p_transit: float = P_TRANSIT, p_slip: float = P_SLIP,
                  p_guess: float = P_GUESS) -> dict:
    """把一次作答应用到知识点状态，返回**新的** state dict（纯函数，不改入参）。

    流程：BKT 后验 → 收缩 → 计数累加 → 置信度累加。
    `state` 为 `None` 时按冷启动（`p=先验`、`attempts=0`）处理。
    """
    st = dict(state or {})
    p = _as_float(st.get("p_mastery"), 0.0) or p_prior
    n = _as_int(st.get("attempts"))
    post = bkt_update(p, correct, p_transit=p_transit, p_slip=p_slip, p_guess=p_guess)
    n_new = n + 1
    st["p_mastery"] = round(shrink(post, n_new, p_prior), 4)
    st["attempts"] = n_new
    st["correct"] = _as_int(st.get("correct")) + (1 if correct else 0)
    st["last_seen"] = _now()
    return st


def calibration_curve(pairs: list[tuple[int, bool]],
                      buckets: tuple[int, ...] = (1, 2, 3, 4, 5)) -> dict:
    """自评置信度 → 实际正确率。

    输入 `[(自评 1..5, 是否答对)]`，输出
    `{"n","brier","buckets":[{"confidence","n","accuracy"}],"curve"}`。

    `brier` = mean((自评/5 - 是否答对)²)——校准误差，越小越准。
    `n < 10` 时调用方应显示「样本不足」而不是数字（前端已按 `insufficient` 字段处理）。
    """
    rows = [(int(c), bool(ok)) for c, ok in pairs if c is not None]
    n = len(rows)
    out_buckets = []
    curve = []
    for c in buckets:
        sel = [ok for cc, ok in rows if cc == c]
        acc = (sum(1 for x in sel if x) / len(sel)) if sel else 0.0
        out_buckets.append({"confidence": c, "n": len(sel),
                            "accuracy": round(acc, 4)})
        curve.append([c, round(acc, 4)])
    brier = 0.0
    if n:
        brier = sum(((c / 5.0) - (1.0 if ok else 0.0)) ** 2 for c, ok in rows) / n
    return {"n": n, "brier": round(brier, 4), "buckets": out_buckets, "curve": curve,
            "insufficient": n < 10}


def item_stats(attempts: list[dict], items: list[dict]) -> dict[str, dict]:
    """每题的难度与区分度（**个人难度**，不是群体难度）。

    - `p` = 正确率（难度：越低越难）
    - `discrimination` = 点二列相关（该题对分的区分能力）
    - `n` = 作答次数；`p_shrunk` = 收缩到 0.6 的正确率（小样本下不 overreact）

    **为什么不做 IRT**：IRT 2PL 要求每题 300–500 份作答才稳，单用户本地永远达不到
    （详见实施方案 §0 D2）。这里用「个人难度 + 收缩」——对单用户场景更贴切也更诚实：
    同一道题对 A 是 0.8、对 B 是 0.3，这在本场景不是噪声，就是我们要的信号。
    """
    by_item: dict[str, list[bool]] = {}
    for a in attempts:
        iid = str(a.get("item_id") or "")
        if not iid:
            continue
        by_item.setdefault(iid, []).append(bool(a.get("correct")))
    totals = [1.0 if a.get("correct") else 0.0 for a in attempts]
    total_mean = (sum(totals) / len(totals)) if totals else 0.0
    var = (sum((t - total_mean) ** 2 for t in totals) / len(totals)) if totals else 0.0
    sd = math.sqrt(var)
    known = {str(i.get("id") or ""): i for i in items}
    out: dict[str, dict] = {}
    for iid, oks in by_item.items():
        n = len(oks)
        k = sum(1 for x in oks if x)
        p = k / n if n else 0.0
        # 小样本收缩：把 p 往 0.6 拉（等效 2 次观测）
        p_shrunk = (n * p + 2 * 0.6) / (n + 2)
        # 点二列相关：答对者平均总分 − 答错者平均总分，按总分离差标准化
        right_tot = [t for a, t in zip(attempts, totals, strict=True)
                     if str(a.get("item_id") or "") == iid and a.get("correct")]
        wrong_tot = [t for a, t in zip(attempts, totals, strict=True)
                     if str(a.get("item_id") or "") == iid and not a.get("correct")]
        m1 = (sum(right_tot) / len(right_tot)) if right_tot else 0.0
        m0 = (sum(wrong_tot) / len(wrong_tot)) if wrong_tot else 0.0
        disc = 0.0
        if n >= 2 and sd > 0 and 0.0 < p < 1.0:
            disc = ((m1 - m0) / sd) * math.sqrt(p * (1.0 - p))
        it = known.get(iid) or {}
        out[iid] = {
            "item_id": iid,
            "n": n,
            "correct": k,
            "p": round(p, 4),
            "p_shrunk": round(p_shrunk, 4),
            "discrimination": round(disc, 4),
            "topic": str(it.get("topic") or it.get("subtopic") or ""),
            "bad": bool(n >= 3 and (p < 0.2 or disc < 0.0)),
        }
    return out


# ================================================================ 小工具
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


# ================================================================ 存储层
_KC_STATE_COLS = ("subject", "chapter", "item", "p_mastery", "scaffold_level",
                  "attempts", "correct", "due", "last_seen", "updated_at")


def ensure_ready() -> None:
    dbs.migrate()


# ---- kc_state（读模型） ----------------------------------------------
def _normalize_state(state: dict) -> dict:
    """补齐一条状态记录的必填字段（**纯函数**，不改入参）。"""
    rec = dict(state)
    kc_id = str(rec.get("kc_id") or rec.get("id") or "")
    if not kc_id:
        raise ValueError("知识点状态必须有 kc_id")
    rec["id"] = kc_id
    rec.setdefault("kc_id", kc_id)
    rec.setdefault("subject", "")
    rec.setdefault("chapter", "")
    rec.setdefault("item", "")
    rec.setdefault("p_mastery", PRIOR_P_MASTERY)
    rec.setdefault("scaffold_level", 0)
    rec.setdefault("attempts", 0)
    rec.setdefault("correct", 0)
    rec.setdefault("due", "")
    rec.setdefault("last_seen", "")
    rec.setdefault("updated_at", _now())
    return rec


def put_state_in(cur: Any, state: dict) -> dict:
    """在**调用方的事务里**写一条状态（不自己开事务）。

    为什么必须存在：`dbs.tx()` 是 `BEGIN IMMEDIATE`，**不可重入**——在别人的写事务里
    再开一个会直接抛 `cannot start a transaction within a transaction`。
    而「作答流水 + 状态」必须**同事务原子**（见 `report_attempt`），
    所以状态写入必须能接受外部游标。
    """
    rec = _normalize_state(state)
    dbs.put_row(cur, "kc_state", rec, _KC_STATE_COLS)
    return rec


def upsert_state(state: dict) -> dict:
    """写入一条知识点状态（`data` JSON 为权威，查询列是冗余副本）。

    **双写不变量**：冗余列的值必须与 `data` 里的同名字段一致。
    守卫：`tests/test_kc.py::test_kc_state_columns_match_data`。
    """
    ensure_ready()
    with dbs.tx(write=True) as cur:
        return put_state_in(cur, state)


def _state_in(cur: Any, kc_id: str) -> Optional[dict]:
    return dbs.find_row(cur, "kc_state", "id = ?", (str(kc_id),))


def _confirmed_kcs_in(cur: Any, item_id: str) -> list[str]:
    rows = cur.execute(
        "SELECT kc_id FROM item_kc WHERE item_id = ? AND confirmed = 1 ORDER BY kc_id",
        (str(item_id or ""),)).fetchall()
    return [str(r[0]) for r in rows]


def _kc_meta_in(cur: Any, kc_id: str) -> dict:
    """知识点的展示元数据（科目/章节/条目）——来自 `syllabus_items`。

    用**同一个游标**读，既省一次连接、也不会在写事务里再开事务。
    """
    row = dbs.find_row(cur, "syllabus_items", "id = ?", (str(kc_id),)) or {}
    return {"subject": str(row.get("subject") or ""),
            "chapter": str(row.get("chapter") or ""),
            "item": str(row.get("item") or "")}


def apply_attempt_in(cur: Any, item_id: str, correct: bool,
                     kc_item_id: str = "") -> dict:
    """在**调用方的事务里**把一次作答应用到该题映射到的全部知识点。

    `item_id` 是流水里的题 id；`kc_item_id` 允许覆盖用于**查映射**的 id
    （错题重练的流水记的是错题 id，但它可能带着 `source_ref.item_id` 指向原题）。

    返回 `{"item_id", "mapped", "kcs": [{"kc_id","p_before","p_mastery","delta",
    "attempts","correct"}]}`——**没有映射不算错**（未回填的用户就是这样），
    只是 `mapped=False`，调用方如实展示即可。
    """
    probe = str(kc_item_id or item_id or "")
    kc_ids = _confirmed_kcs_in(cur, probe)
    out: dict[str, Any] = {"item_id": str(item_id or ""), "mapped": bool(kc_ids), "kcs": []}
    meta_cache: dict[str, dict] = {}
    for kc_id in kc_ids:
        row = _state_in(cur, kc_id) or {"kc_id": kc_id}
        if not row.get("item"):
            if kc_id not in meta_cache:
                meta_cache[kc_id] = _kc_meta_in(cur, kc_id)
            meta = meta_cache[kc_id]
            for k, v in meta.items():
                if v and not row.get(k):
                    row[k] = v
        before = _as_float(row.get("p_mastery"), PRIOR_P_MASTERY)
        new = apply_attempt(row, bool(correct))
        new["kc_id"] = kc_id
        put_state_in(cur, new)
        out["kcs"].append({
            "kc_id": kc_id,
            "p_before": round(before, 4),
            "p_mastery": round(_as_float(new.get("p_mastery")), 4),
            "delta": round(_as_float(new.get("p_mastery")) - before, 4),
            "attempts": _as_int(new.get("attempts")),
            "correct": _as_int(new.get("correct")),
        })
    return out


def apply_attempt_for_item(item_id: str, correct: bool,
                           kc_item_id: str = "") -> dict:
    """`apply_attempt_in` 的自持事务版本（调用方**没有**现成事务时用）。"""
    ensure_ready()
    with dbs.tx(write=True) as cur:
        return apply_attempt_in(cur, item_id, correct, kc_item_id=kc_item_id)


def rebuild_state() -> dict:
    """从 `bank_attempts ⋈ item_kc` **重放重建** `kc_state`（读模型的真相校验）。

    这是「`kc_state` 是读模型不是真相」这条设计的**可执行证明**：
    清空状态表后按流水顺序重放，得到的 `p_mastery` / `attempts` / `correct`
    必须与增量维护的结果**逐值相等**（守卫：`tests/test_kc_state.py`）。

    只重放**已确认映射**的题；未映射的流水跳过（它们没有知识点归属，不是丢失）。
    """
    ensure_ready()
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM kc_state")
        rows = cur.execute(
            "SELECT item_id, correct FROM bank_attempts"
            " ORDER BY occurred_at, id").fetchall()
        applied = 0
        for item_id, correct in rows:
            res = apply_attempt_in(cur, str(item_id or ""), bool(correct))
            if res["mapped"]:
                applied += 1
        total = cur.execute("SELECT COUNT(*) FROM kc_state").fetchone()[0]
    return {"attempts": len(rows), "applied": applied,
            "kcs": int(total)}


def knowledge_rows(subject: str = "") -> list[dict]:
    """把 `kc_state` 摊成「薄弱项表」的行（与 legacy `knowledge` 行同形）。

    `score` 直接用 `p_mastery`（0~1，与 legacy `compute_score` 同量纲）；
    `miss` = attempts − correct（错几次就密度多少，`_priority_of` 同口径）。

    **没有数据库时返回空表**（不建库）：本函数会被 `library.get_mastery_view()` 在
    纯 JSON 模式下调用，读路径**不该**有「顺手把库建出来」的副作用。
    """
    if not dbs.enabled():
        return []
    ensure_ready()
    where, params = "", []
    if subject:
        where = "WHERE subject = ?"
        params.append(subject)
    with dbs.tx(write=False) as cur:
        rows = dbs.list_rows(cur, "kc_state", where, tuple(params))
    out: list[dict] = []
    for r in rows:
        attempts = _as_int(r.get("attempts"))
        if attempts <= 0:
            continue          # 没作答过的状态只是占位，不该出现在「薄弱项」里
        correct = _as_int(r.get("correct"))
        out.append({
            "name": str(r.get("item") or r.get("id") or ""),
            "kc_id": str(r.get("id") or ""),
            "subject": str(r.get("subject") or ""),
            "chapter": str(r.get("chapter") or ""),
            "score": round(_as_float(r.get("p_mastery"), PRIOR_P_MASTERY), 4),
            "miss": max(0, attempts - correct),
            "attempts": attempts,
            "correct": correct,
            "last_tried": str(r.get("last_seen") or ""),
            "priority": _priority_of(r),
            "score_source": "kc",
        })
    return out


def get_state(kc_id: str) -> Optional[dict]:
    ensure_ready()
    with dbs.tx(write=False) as cur:
        row = dbs.find_row(cur, "kc_state", "id = ?", (str(kc_id),))
    return row


def list_states(subject: str = "", order: str = "priority",
                limit: int = 100, offset: int = 0) -> tuple[int, list[dict]]:
    """知识点状态列表（分页）。返回 `(总数, 行)`。

    `order`：`priority`（默认，按薄弱优先）/ `mastery`（掌握度升序）/ `due`（到期优先）。
    """
    ensure_ready()
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    where, params = "", []
    if subject:
        where = "WHERE subject = ?"
        params.append(subject)
    with dbs.tx(write=False) as cur:
        total = cur.execute(f"SELECT COUNT(*) FROM kc_state {where}",
                            tuple(params)).fetchone()[0]
        rows = dbs.list_rows(cur, "kc_state", f"{where}", tuple(params))
    if order == "mastery":
        rows.sort(key=lambda r: (_as_float(r.get("p_mastery")), str(r.get("id"))))
    elif order == "due":
        rows.sort(key=lambda r: (str(r.get("due") or "9999"), str(r.get("id"))))
    else:  # priority：越薄弱越靠前
        rows.sort(key=lambda r: (-_priority_of(r), str(r.get("id"))))
    return int(total), rows[offset:offset + limit]


def _priority_of(row: dict) -> float:
    """优先级（复用 library 的口径形状：掌握度缺口为主 + 错题密度 + 近期失败）。

    与 `library.compute_priority` 同形但输入是概率——**不引入第二套口径**。
    """
    p = _as_float(row.get("p_mastery"), PRIOR_P_MASTERY)
    attempts = _as_int(row.get("attempts"))
    correct = _as_int(row.get("correct"))
    miss = max(0, attempts - correct)
    miss_density = min(miss / 6.0, 1.0)
    return round(min(max(0.5 * (1.0 - p) + 0.3 * miss_density, 0.0), 1.0), 3)


def state_with_priority(row: dict) -> dict:
    out = dict(row)
    out["priority"] = _priority_of(row)
    out["state"] = _state_of(_as_float(row.get("p_mastery"), PRIOR_P_MASTERY))
    return out


def _state_of(p: float) -> str:
    """掌握状态（**与 `library.compute_state` 同口径**：weak/shaky/solid/mastered）。"""
    if p >= 0.85:
        return "mastered"
    if p >= 0.65:
        return "solid"
    if p >= 0.40:
        return "shaky"
    return "weak"


# ---- item_kc（映射） -------------------------------------------------
def set_mappings(item_id: str, kc_ids: list[str], source: str = "human") -> list[dict]:
    """覆盖式设置某题的**已确认**映射（唯一写入口）。返回写入的行。

    - 覆盖语义：本次 `kc_ids` 即该题最终的全部已确认映射；空数组 = 取消该题全部确认。
    - `source='human'` ⇒ `confirmed=1`、`confidence=1.0`（人工确认即视为确定）。
    - **不碰** `source='llm'` 的未确认行（那些只是候选，删了由回填重建）。
    """
    ensure_ready()
    iid = str(item_id or "")
    if not iid:
        raise ValueError("item_id 不能为空")
    now = _now()
    want = [str(k) for k in (kc_ids or []) if str(k)]
    with dbs.tx(write=True) as cur:
        cur.execute("DELETE FROM item_kc WHERE item_id=? AND confirmed=1", (iid,))
        for kc_id in want:
            cur.execute(
                "INSERT INTO item_kc (item_id, kc_id, source, confidence, confirmed,"
                " created_at, updated_at) VALUES (?,?,?,?,1,?,?)"
                " ON CONFLICT(item_id, kc_id) DO UPDATE SET"
                " source=excluded.source, confidence=excluded.confidence,"
                " confirmed=1, updated_at=excluded.updated_at",
                (iid, kc_id, str(source or "human"), 1.0, now, now))
    return mappings_for_item(iid, only_confirmed=True)


def mappings_for_item(item_id: str, only_confirmed: bool = False) -> list[dict]:
    ensure_ready()
    where = "item_id = ?"
    params: tuple[Any, ...] = (str(item_id or ""),)
    if only_confirmed:
        where += " AND confirmed = 1"
    with dbs.tx(write=False) as cur:
        rows = cur.execute(
            f"SELECT item_id, kc_id, source, confidence, confirmed, created_at, updated_at"
            f" FROM item_kc WHERE {where} ORDER BY confirmed DESC, confidence DESC, kc_id",
            params).fetchall()
    return [dict(r) for r in rows]


def mappings_for_kc(kc_id: str) -> list[dict]:
    ensure_ready()
    with dbs.tx(write=False) as cur:
        rows = cur.execute(
            "SELECT item_id, kc_id, source, confidence, confirmed FROM item_kc"
            " WHERE kc_id = ? ORDER BY item_id", (str(kc_id or ""),)).fetchall()
    return [dict(r) for r in rows]


def list_edges(kc_id: str = "", kind: str = "") -> list[dict]:
    """知识点之间的关系（前置 / 关联 / 易混）。

    **SQL 与事务边界归 core**（U-09 守卫：路由层不得直写 SQL / 调迁移）——
    `routers/kc.py` 只调用本函数。
    """
    ensure_ready()
    where, params = [], []
    if kc_id:
        where.append("(src = ? OR dst = ?)")
        params += [kc_id, kc_id]
    if kind:
        where.append("kind = ?")
        params.append(kind)
    cond = ("WHERE " + " AND ".join(where)) if where else ""
    with dbs.tx(write=False) as cur:
        rows = cur.execute(
            f"SELECT src, dst, kind, weight, source, confirmed FROM kc_edges {cond}"
            f" ORDER BY src, dst, kind", tuple(params)).fetchall()
    return [dict(r) for r in rows]


def confidence_pairs(subject: str = "", days: int = 30) -> list[tuple[int, bool]]:
    """从 `bank_attempts` 取 (自评置信度, 是否答对)。

    **只取带 `confidence` 的作答**——没自评的行不参与（否则会把「没填」当成「最没把握」，
    校准曲线会被系统性拉低）。

    **SQL 与事务边界归 core**（U-09 守卫）。
    """
    from datetime import date, timedelta

    ensure_ready()
    since = (date.today() - timedelta(days=max(1, int(days)))).isoformat()
    with dbs.tx(write=False) as cur:
        rows = cur.execute(
            "SELECT confidence, correct FROM bank_attempts"
            " WHERE occurred_at >= ? AND confidence IS NOT NULL", (since,)).fetchall()
    return [(int(r[0]), bool(r[1])) for r in rows if r[0] is not None]


def confirmed_mappings() -> dict[str, list[str]]:
    """全量已确认映射：`{item_id: [kc_id, ...]}`（重建 `kc_state` 用）。"""
    ensure_ready()
    out: dict[str, list[str]] = {}
    with dbs.tx(write=False) as cur:
        for r in cur.execute(
                "SELECT item_id, kc_id FROM item_kc WHERE confirmed = 1"
                " ORDER BY item_id, kc_id").fetchall():
            out.setdefault(str(r[0]), []).append(str(r[1]))
    return out


# ---- 知识点池（来自 syllabus_items） ---------------------------------
def kc_pool(subject: str = "", source: str = "all") -> list[dict]:
    """知识点池：`syllabus_items` 里 kind='item' 的条目（稳定 id + 章节 + 科目）。

    章级行（kind='chapter'）**不进池**——它们是分组，不是可挂载的知识点。
    """
    from . import syllabus as syl
    rows = syl.list_kc_items(subject=subject, source=source)
    return [{"kc_id": str(r.get("id") or ""), "item": str(r.get("item") or ""),
             "chapter": str(r.get("chapter") or ""),
             "subject": str(r.get("subject") or "")} for r in rows]


# ---- 回填 ------------------------------------------------------------
def backfill(bank_id: str = "", subject: str = "", method: str = "rule",
             min_score: float = FUZZY_MIN_SCORE, dry_run: bool = True) -> dict:
    """把已有题映射到知识点（**幂等、可重入**）。

    规则：
      - 精确匹配 ⇒ `source='rule'`、`confidence=1.0`、**自动确认**（confirmed=1）；
      - 模糊匹配（≥ min_score）⇒ `source='rule'`、`confidence=score`、**待人工确认**；
      - `method='llm'` 且 LLM 不可用 ⇒ **降级为 rule**（不报错，见实施方案 §0 D6）。

    **不覆盖人工确认过的映射**：回填只写 `source='rule'` 的行，
    且 `ON CONFLICT` 时不降级已有 `confirmed=1` 的行。
    """
    ensure_ready()
    from . import bank as banklib
    method = str(method or "rule").strip().lower()
    if method not in ("rule", "llm"):
        raise ValueError("method 只能是 rule 或 llm")
    notes: list[str] = []
    if method == "llm":
        # 阶段 2 只落规则通道；LLM 兜底通道（agents/kc_mapper.py）尚未实现 ⇒ 明确降级。
        notes.append("LLM 兜底通道尚未实现，已降级为规则匹配（method=llm 按 rule 处理）")
        method = "rule"

    items = banklib.list_items(bank_id) if bank_id else []
    if not items and not bank_id:
        from . import bank as _bl
        for b in _bl.list_banks():
            items.extend(_bl.list_items(str(b.get("id") or "")))
    pool = kc_pool(subject=subject)
    scanned = matched = unmatched = created = 0
    for it in items:
        scanned += 1
        iid = str(it.get("id") or "")
        topic = str(it.get("topic") or it.get("subtopic") or "")
        if not iid or not topic:
            unmatched += 1
            continue
        cands = [c for c in rule_candidates(topic, pool) if c["score"] >= min_score]
        if not cands:
            unmatched += 1
            continue
        matched += 1
        if dry_run:
            continue
        existing = {m["kc_id"] for m in mappings_for_item(iid) if m["confirmed"]}
        for c in cands:
            if c["kc_id"] in existing:
                continue
            confirmed = 1 if c["match_type"] == "exact" else 0
            if confirmed:
                created += 1
            with dbs.tx(write=True) as cur:
                cur.execute(
                    "INSERT INTO item_kc (item_id, kc_id, source, confidence, confirmed,"
                    " created_at, updated_at) VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(item_id, kc_id) DO UPDATE SET"
                    " source=excluded.source, confidence=excluded.confidence,"
                    " updated_at=excluded.updated_at"
                    " WHERE item_kc.confirmed = 0",
                    (iid, c["kc_id"], "rule", c["score"], confirmed, _now(), _now()))
    return {"scanned": scanned, "matched": matched, "unmatched": unmatched,
            "created": created, "method": method, "dry_run": bool(dry_run),
            "notes": notes}


def item_id_hash(item_id: str) -> str:
    """题目的稳定短 id（供外部系统对齐用，非主键）。"""
    return hashlib.sha1(str(item_id or "").encode("utf-8")).hexdigest()[:16]
