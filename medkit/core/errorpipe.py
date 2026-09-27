"""错题归因流水线（EP-01）· 编排层。

## 这条管线解决什么

把一道「考生自己记下来的错题」变成**可纵向追踪的元认知档案**。适用于：

- **适用**：医学考研（西综 306）备考中按轮次（早鸟/跟课/强化/冲刺）反复刷题的错题沉淀；
- **不适用**：一次性批量刷题的题海记录（没有跨轮次复用价值）；
  也没有「试卷自动批改」场景——**正确答案必须由考生提供**（《总纲》§3.2 红线）。

## 阶段流转

```
        ┌─ P1 intake ────────┐   归一化 + 摩擦闸门（confidence/my_reasoning 必填）
input → │  normalize_card    │   → 缺 confidence 则**拒绝进入 P2**
        └─────────┬──────────┘
                  ↓
        ┌─ P2 kp_align ──────┐   kp_id 对齐（kpid.resolve，别名表幂等）
        │  align_kp          │
        └─────────┬──────────┘
                  ↓
        ┌─ P3 attribute ─────┐   LLM 归因（可跳过：offline / 无 Key / 用户未选）
        │  attribute_one     │   → ai_error_tag + counterfactual + fix
        └─────────┬──────────┘
                  ↓
        ┌─ P4 persist ───────┐   mistakes 更新 + error_events 追加（append-only）
        │  persist           │
        └─────────┬──────────┘
                  ↓
        ┌─ P5 analyze ───────┐   metacog 纯函数（校准/热力图/迁移/减法）
        │  analyze           │
        └────────────────────┘
```

## 设计要点（与既有代码的关系）

1. **不新建存储层**：复用 `library._store()` 的事务适配器与 `db.put_row`，
   因此自动继承 SQL/JSON 双轨、`BEGIN IMMEDIATE` 串行化、行级增量写。
   新表一律先 `dbs.migrate()`（对齐 ADR-006：不再回落 JSON）。
2. **阶段可跳过、可单跑**：`run()` 的 `stages` 参数控制跑哪几段。
   P3 是唯一有外部依赖（LLM）的阶段，必须能被单独跳过——
   否则断网环境下整条管线不可用（违反《总纲》§2.4「离线优先」）。
3. **fail-soft，绝不静默**：任一阶段失败都 `errors.record()` 留痕并降级，
   返回结构里带 `warnings`，让调用方能区分「没跑」与「跑了但失败」。
4. **红线守卫**：`normalize_card` 强制 confidence/my_reasoning，
   `attribute_one` 不允许写回 answer/correct。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from . import db as dbs
from . import error_events as ev
from . import errors as errs
from . import kpid, library, metacog

# SQL 轨判定：模块级快照 + 运行时兜底读取。
#
# 项目既有约定是各域模块在导入时 `DB_FILE = dbs.DB_PATH` 并让测试 monkeypatch 它
# （见 library/review/cards/explain/tutor）。本模块额外接受**只 patch dbs.DB_PATH**
# 的写法——测试里两种写法都出现过，紧耦合到一种会让隔离莫名失效
# （表现为「测试写进了真实 ~/.medkit」这类最难查的问题）。
_DB_SNAPSHOT = dbs.DB_PATH


def _db_path():
    """当前 db 路径：dbs 被改过就以它为准（单一事实源），否则用导入时快照。"""
    return dbs.DB_PATH if dbs.DB_PATH != _DB_SNAPSHOT else _DB_SNAPSHOT


def db_ready() -> bool:
    """库是否已建（供各阶段决定要不要先 migrate）。"""
    return _db_path().exists()


# --------------------------------------------------------------------- 阶段常量
STAGES: tuple[str, ...] = ("intake", "kp_align", "attribute", "persist", "analyze")

# P1 闸门所需的必填字段（《总纲》§3.1 / §3.4：这两项不许代填、不许事后补）
GATE_REQUIRED: tuple[str, ...] = ("confidence", "my_reasoning")

# 可写入 mistakes 的新字段白名单（与 routers/library.py 的 MistakeBody 对齐）
META_FIELDS: tuple[str, ...] = (
    "confidence", "my_reasoning", "error_tag", "ai_error_tag", "tag_match",
    "round", "kp_id", "fix", "counterfactual", "knowledge_ref",
)

# 轮次取值（与 metacog.ROUNDS 单源）
ROUNDS = metacog.ROUNDS


# ===================================================================== P1 intake
def normalize_card(raw: dict[str, Any]) -> dict[str, Any]:
    """P1：归一化一条录入；返回 ``{card, gate_ok, missing}``。

    **闸门语义**：`confidence` 与 `my_reasoning` 缺失时 `gate_ok=False`，
    调用方**不得**继续跑 P3（因为看答案前的自评是唯一拿得到真实信号的时机，
    看过答案就污染了）。但**卡片本体仍然可以入库**——先把题干存下来，
    晚上补归因是允许的（《总纲》§3.1「允许先标记，晚上批量补」），
    唯独 confidence 不能补。

    归一化对外部 JSONL 特别重要：`options` 在《总纲》schema 里是 dict
    （``{"A": "...", "B": "..."}``），而 MedKit 内部用 list。这是唯一的格式转换点。
    """
    card = dict(raw or {})
    missing: list[str] = []

    # options：dict → list（保持 A/B/C/D/E 顺序；已有顺序键不重排）
    opts = card.get("options")
    if isinstance(opts, dict):
        def _key(k: str) -> tuple[int, str]:
            return (0, k) if len(k) == 1 else (1, k)
        card["options"] = [str(v) for k, v in sorted(opts.items(), key=lambda kv: _key(str(kv[0])))]
    elif opts is None:
        card["options"] = []

    # 文本字段统一 strip
    for k in ("subject", "chapter", "topic", "question", "answer", "user_answer",
              "analysis", "error_reason", "my_reasoning", "error_tag", "round",
              "fix", "knowledge_ref", "case_stem"):
        if k in card and card[k] is not None:
            card[k] = str(card[k]).strip()

    # confidence：非法值视为未填（而不是硬转 0——0 不在 1-5 里，会被下游当"填了但很怪"）
    conf = card.get("confidence")
    conf_n = _coerce_confidence(conf)
    card["confidence"] = conf_n

    # error_tag 校验：不在 6 类里 → 清空并留痕（自造标签会污染热力图与迁移矩阵）
    tag = str(card.get("error_tag") or "")
    if tag and tag not in metacog.ERROR_TAG_SET:
        errs.record("errorpipe.normalize_card",
                    f"error_tag {tag!r} 不在 6 类内，已清空（避免污染统计）")
        card["error_tag"] = ""

    # round 校验：非标准轮次保留原值但标记（用户可能有自己的叫法，不该硬改）
    if card.get("round") and card["round"] not in ROUNDS:
        card["_round_unknown"] = True

    # correct：显式区分「答错」与「未作答」——绝不能把 None 当 False
    if "correct" in card and card["correct"] is not None:
        card["correct"] = bool(card["correct"])

    if conf_n is None:
        missing.append("confidence")
    if not str(card.get("my_reasoning") or ""):
        missing.append("my_reasoning")

    return {"card": card, "gate_ok": not missing, "missing": missing}


def _coerce_confidence(v: Any) -> Optional[int]:
    """1-5 的整数；其余（None/""/0/6/小数/"abc"）一律视为**未填**。

    刻意不抛异常：录入追求低摩擦，非法自评不该让整条录入失败，
    而是降级为「未填」并让闸门拦住它（比静默取默认值安全）。

    ⚠️ 必须显式拒绝**小数**：`int(2.5)` 会被 Python 静默截断成 2，
    于是 ``confidence=2.5`` 这种"看起来填了其实没填"的值会通过闸门，
    把 `conf/5` 的校准曲线算成 0.4——一个不存在的自评档位。
    这条是测试 `test_intake_confidence_out_of_range_treated_as_missing` 逼出来的。
    """
    if v is None or v == "":
        return None
    if isinstance(v, bool):        # True 是 int 的子类，但不该当 1 用
        return None
    if isinstance(v, float) and not v.is_integer():
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 5 else None


def gate_ok(rec: dict[str, Any]) -> bool:
    """查询某条错题是否已过闸门（供路由层与前端判断"能否看答案"）。"""
    if _coerce_confidence(rec.get("confidence")) is None:
        return False
    return bool(str(rec.get("my_reasoning") or "").strip())


# ===================================================================== P2 kp_align
def align_kp(rec: dict[str, Any], *, auto_register: bool = True) -> str:
    """P2：对齐知识点 ID。已在库里的值优先复用（幂等，重跑不改归属）。"""
    existing = str(rec.get("kp_id") or "")
    if existing:
        return existing
    return kpid.resolve(str(rec.get("subject") or ""),
                        str(rec.get("chapter") or ""),
                        str(rec.get("topic") or ""),
                        auto_register=auto_register)


# ===================================================================== P3 attribute
def attribute_one(rec: dict[str, Any], *, client: Any = None) -> dict[str, Any]:
    """P3：对**单条**错题跑 AI 归因。返回 ``{ok, analysis, error}``。

    不写库——纯计算（便于单测）。写库在 P4。

    红线：`analysis` 里不可能出现 answer/correct（见 `agents/error_analysis.analyze`
    的防御性 pop + 契约本身无该字段）。
    """
    if not gate_ok(rec):
        return {"ok": False, "analysis": None,
                "error": "未过闸门（confidence / my_reasoning 缺失）——不许先看答案再归因"}

    try:
        from ..agents import error_analysis as agent
        cli = client if client is not None else agent.make_client()
        analysis = agent.analyze(cli, rec)
        return {"ok": True, "analysis": analysis, "error": ""}
    except Exception as e:  # noqa: BLE001  LLM 失败不阻断记录（错题本体已保存）
        errs.record("errorpipe.attribute_one", "AI 归因失败（错题本体不受影响）", e=e)
        return {"ok": False, "analysis": None, "error": str(e)}


def apply_analysis(rec: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    """把归因结果合并进错题记录（含 tag_match 派生）。**不写库**。

    `tag_match` 的语义：人工 tag 与 AI tag 是否一致。
    《总纲》§3.2 指出**不一致的题目价值最高**（那是自我认知有偏差的地方），
    故这里显式派生一个整数列，让分析层可以低成本筛出这批题。

    只在两边都有值时才判 match；只有一边 → 不判（避免把"没填"误算成"不一致"）。
    """
    out = dict(rec)
    human = str(out.get("error_tag") or "")
    ai = str(analysis.get("error_tag") or "")
    if ai in metacog.ERROR_TAG_SET:
        out["ai_error_tag"] = ai
    # fix / counterfactual：AI 结果只补空位，**不覆盖用户自己写的**
    # （用户亲手写的修正陈述比模型的更贴合自己的记忆方式）
    if analysis.get("fix") and not str(out.get("fix") or "").strip():
        out["fix"] = str(analysis["fix"])
    if analysis.get("counterfactual"):
        out["counterfactual"] = str(analysis["counterfactual"])
    if human in metacog.ERROR_TAG_SET and ai in metacog.ERROR_TAG_SET:
        out["tag_match"] = 1 if human == ai else 0
    if analysis.get("evidence"):
        out["ai_evidence"] = str(analysis["evidence"])
    if analysis.get("kp_point"):
        out["ai_kp_point"] = str(analysis["kp_point"])
    if analysis.get("where_uncertain"):
        out["ai_uncertain"] = list(analysis["where_uncertain"])
    return out


# ===================================================================== P4 persist
def persist(rec: dict[str, Any], *, append_event: bool = True,
            event: str = ev.EVENT_ANSWER, business_key: str = "") -> dict[str, Any]:
    """P4：把一条错题写入 mistakes 并追加一条流水。返回 ``{saved, event_id}``。

    为什么走 `library.update_mistake` 而不是自己写 SQL：
    - 复用 `_store()` 的单事务 + 行级增量写（避免整表替换）；
    - 复用 `_touch_knowledge_in` 的知识点掌握度联动（这是既有资产，不该绕开）。
    但 `update_mistake` 的字段白名单不含元认知字段，故此处走 `_store()` 直接改。

    流水与快照**分开写**且流水**只追加**：快照可以被用户改，流水不能，
    这样跨轮次分析才有可信的历史（见 `error_events` 模块 docstring）。
    """
    warnings: list[str] = []
    mid = str(rec.get("id") or "")
    if not mid:
        return {"saved": None, "event_id": "", "warnings": ["缺少 id，未落库"]}

    saved = _write_meta(mid, rec)
    if saved is None:
        warnings.append(f"错题 {mid} 不存在，未落库（先用 add_mistake 建卡）")

    event_id = ""
    if append_event and saved is not None:
        try:
            event_id = ev.append(
                kp_id=str(saved.get("kp_id") or ""),
                round_=str(saved.get("round") or ""),
                event=event,
                business_key=business_key or mid,
                error_tag=str(saved.get("error_tag") or saved.get("ai_error_tag") or ""),
                is_correct=saved.get("correct"),
            )
        except Exception as e:  # noqa: BLE001  流水失败不回滚快照（宁可少一条统计，不可丢记录）
            errs.record("errorpipe.persist", "error_events 追加失败", e=e)
            warnings.append(f"流水追加失败：{e}")

    return {"saved": saved, "event_id": event_id, "warnings": warnings}


def _write_meta(mid: str, rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    """只更新元认知字段（不动题干/答案），返回落库后的记录。

    走 `library._store()`：拿 `BEGIN IMMEDIATE` 事务 + 定向读 + 行级增量写。
    `_mark_m_row` 是 library 的私有登记函数，此处按项目既有做法复用
    （同包内的 core 模块互相复用私有函数是既有惯例，见 `review.py`/`cards.py`）。

    无 patch 时**直接走只读入口**，不开写事务（避免为一次纯读拿写锁）。
    """
    patch = {k: rec[k] for k in META_FIELDS if k in rec}
    if not patch:
        return library.get_mistake(mid)
    with library._store() as st:  # type: ignore[attr-defined]
        cur = library._find_mistake(st, mid)  # type: ignore[attr-defined]
        if cur is None:
            return None
        for k, v in patch.items():
            cur[k] = v
        cur["last_tried"] = library._now()  # type: ignore[attr-defined]
        st["dirty"]["mistakes"] = True
        library._mark_m_row(st, cur)  # type: ignore[attr-defined]
        return dict(cur)


# ===================================================================== P5 analyze
def analyze(*, cards: Optional[list[dict[str, Any]]] = None,
            events: Optional[list[dict[str, Any]]] = None,
            freq: Optional[dict[str, int]] = None) -> dict[str, Any]:
    """P5：汇总四张表 + 减法清单（纯计算，可直接喂样本做测试）。

    `cards` / `events` 缺省时从库里取；显式传入则完全用传入值——
    这让统计口径可以按需切片（近一月/单科），不必落物化视图。
    """
    cs = cards if cards is not None else library.list_mistakes()
    es = events if events is not None else ev.list_events()
    return {
        "calibration": metacog.calibration(cs),
        "heatmap": metacog.heatmap(cs),
        "migration": metacog.migration(es),
        "agreement": metacog.tag_agreement(cs),
        "subtract": metacog.subtract_plan(cs, freq=freq),
        "counts": {
            "cards": len(cs),
            "events": len(es),
            "gated": sum(1 for c in cs if gate_ok(c)),
            "kp_ids": len({str(c.get("kp_id")) for c in cs if c.get("kp_id")}),
        },
    }


# ===================================================================== 编排
def run(raw: dict[str, Any], *, stages: tuple[str, ...] = STAGES,
        client: Any = None, auto_register_kp: bool = True,
        append_event: bool = True, freq: Optional[dict[str, int]] = None,
        progress: Optional[Callable[[str, str], None]] = None) -> dict[str, Any]:
    """跑一条录入的完整流水线。返回结构化结果（含每阶段的 outcome 与 warnings）。

    `progress(stage, msg)` 可选回调，供路由层做 SSE/日志推送，
    与既有 `orchestrator._substep` 的思路一致但不共用实现——
    出题管线的进度是「阶段×子步骤×百分比」，本管线是「单条记录跑 5 段」，
    硬套会把两边的语义都搞混（这是**有意不抽象**，不是漏做）。

    `stages` 子集示例：
    - ``("intake","kp_align","persist")``：离线补录（不跑 LLM）
    - ``("analyze",)``：只出统计（`raw` 忽略）
    """
    want = set(stages)
    warnings: list[str] = []
    result: dict[str, Any] = {"stages": {}, "warnings": warnings}

    def _note(stage: str, msg: str) -> None:
        if progress:
            # 进度回调是纯 UI 通知，抛错不许影响主流程；但按项目铁律**必须留痕**
            # （「确需吞异常一律走 errors.swallow」，禁止裸 except-pass）。
            with errs.swallow("errorpipe.progress", f"进度回调失败（stage={stage}）"):
                progress(stage, msg)

    # ---- analyze-only 快捷路径
    if want == {"analyze"}:
        _note("analyze", "汇总统计")
        result["stages"]["analyze"] = _summarize(analyze(freq=freq))
        return result

    # ---- P1
    card = dict(raw or {})
    if "intake" in want:
        _note("intake", "归一化 + 闸门校验")
        norm = normalize_card(card)
        card = norm["card"]
        result["stages"]["intake"] = {
            "gate_ok": norm["gate_ok"], "missing": norm["missing"],
        }
        if not norm["gate_ok"]:
            warnings.append(
                "未过闸门（缺 " + "、".join(norm["missing"]) +
                "）：可以入库，但**不许跑 AI 归因**")
    else:
        result["stages"]["intake"] = {"skipped": True}

    # ---- P2
    if "kp_align" in want:
        _note("kp_align", "知识点 ID 对齐")
        kid = align_kp(card, auto_register=auto_register_kp)
        card["kp_id"] = kid
        result["stages"]["kp_align"] = {"kp_id": kid}
        if not kid:
            warnings.append("知识点 ID 未生成（subject/chapter/topic 全空？）")
    else:
        result["stages"]["kp_align"] = {"skipped": True}

    # ---- P3（唯一有外部依赖的阶段；闸门不过一律跳过）
    if "attribute" in want:
        if not gate_ok(card):
            result["stages"]["attribute"] = {
                "ok": False, "skipped_reason": "未过闸门"}
            _note("attribute", "跳过（未过闸门）")
        else:
            _note("attribute", "LLM 归因中…")
            att = attribute_one(card, client=client)
            result["stages"]["attribute"] = {
                "ok": att["ok"], "error": att["error"],
                "error_tag": (att["analysis"] or {}).get("error_tag", ""),
            }
            if att["ok"]:
                card = apply_analysis(card, att["analysis"] or {})
            else:
                warnings.append(f"AI 归因失败（错题本体不受影响）：{att['error']}")
    else:
        result["stages"]["attribute"] = {"skipped": True}

    result["card"] = card

    # ---- P4
    if "persist" in want:
        _note("persist", "落库 + 追加流水")
        # 两条路径：
        # - **无 id** = 新错题 → 建卡（add_mistake 负责派生知识点与掌握度），
        #   再按需追加流水；这是录入端点的主路径。
        # - **有 id** = 已有错题 → 只更新元认知字段（不动题干/答案）。
        if not str(card.get("id") or ""):
            if not str(card.get("question") or "").strip() and not card.get("kp_id"):
                # 既无 id 也无题干 = 不是录入语义（如 analyze 之外的空调用）→ 明确跳过，
                # 而不是"看起来成功但没写任何东西"
                result["stages"]["persist"] = {
                    "saved": None, "event_id": "", "warnings": ["无 id 且无题干，未落库"]}
            else:
                saved = library.add_mistake(card)
                card["id"] = saved.get("id")
                result["card"] = saved
                event_id = ""
                if append_event and saved.get("id"):
                    try:
                        event_id = ev.append(
                            kp_id=str(saved.get("kp_id") or ""),
                            round_=str(saved.get("round") or ""),
                            event=ev.EVENT_ANSWER,
                            business_key=str(saved.get("id")),
                            error_tag=str(saved.get("error_tag")
                                          or saved.get("ai_error_tag") or ""),
                            is_correct=saved.get("correct"),
                        )
                    except Exception as e:  # noqa: BLE001
                        errs.record("errorpipe.persist", "新建卡的流水追加失败", e=e)
                        warnings.append(f"流水追加失败：{e}")
                result["stages"]["persist"] = {
                    "saved": saved, "event_id": event_id, "warnings": [], "created": True}
        else:
            per = persist(card, append_event=append_event)
            result["stages"]["persist"] = per
            warnings.extend(per["warnings"])
            if per.get("saved"):
                result["card"] = per["saved"]
    else:
        result["stages"]["persist"] = {"skipped": True}

    # ---- P5
    if "analyze" in want:
        _note("analyze", "汇总统计")
        result["stages"]["analyze"] = _summarize(analyze(freq=freq))
    else:
        result["stages"]["analyze"] = {"skipped": True}

    return result


def _summarize(full: dict[str, Any]) -> dict[str, Any]:
    """把完整统计压成编排结果里的摘要（避免单条录入的响应里塞进全量热力图）。

    完整数据由 `analyze()` / `/api/errors/stats/*` 端点提供；
    编排结果里只留**标量与告警**——这正是调用方在跑完一条录入后真正要看的东西。
    """
    cal = full.get("calibration") or {}
    mig = full.get("migration") or {}
    hmap = full.get("heatmap") or {}
    agree = full.get("agreement") or {}
    return {
        "counts": full.get("counts") or {},
        "alert": bool(cal.get("alert")),
        "alert_msg": cal.get("alert_msg") or "",
        "brier": cal.get("brier"),
        "stuck_count": mig.get("stuck_count", 0),
        "top_tags": [f"{r['subject']}:{r['top_tag']}"
                     for r in (hmap.get("rows") or [])[:3] if r.get("top_tag")],
        "tag_agreement": agree.get("rate"),
    }


def health() -> dict[str, Any]:
    """管线自检（供诊断端点）：版本、表是否就绪、闸门覆盖率。"""
    ver = dbs.user_version() if db_ready() else 0
    out: dict[str, Any] = {
        "pipeline": "error-attribution",
        "version": 1,
        "db_version": ver,
        "db_ready": ver >= 8,
        "tables": {},
        "kpid": {},
    }
    if ver >= 8:
        try:
            out["tables"]["error_events"] = ev.stats()
        except Exception as e:  # noqa: BLE001
            out["tables"]["error_events"] = {"error": str(e)}
        try:
            out["kpid"] = kpid.stats()
        except Exception as e:  # noqa: BLE001
            out["kpid"] = {"error": str(e)}
    return out
