"""routers：错题归因流水线（EP-01）—— 命名空间 ``/api/errors/*``。

分层（AGENTS.md / 项目铁律）：**routers → core → db/llm**。本文件不出现
``cur.execute`` / ``dbs.tx`` / SQL 字面量——全部经 ``core.errorpipe`` 等域函数。

与 ``/api/library/*`` 的分工：
- ``/api/library/*``：错题本体的 CRUD、导入导出、掌握度（既有，**未改动其签名**）；
- ``/api/errors/*``：**元认知层**——闸门、归因、统计、减法、知识点对齐。
  两者操作同一份 ``mistakes`` 数据，但职责正交（本体 vs 元认知），故分域不分表。

红线（《总纲》§3.2、§3.4）：**不提供"补填 confidence"的端点**。
自评必须在看答案前完成，任何事后写入通道都会让这份数据失去价值。
详见 ``tests/test_errorpipe.py::test_no_confidence_backfill_endpoint``（源码级守卫）。
"""

import threading
from typing import Any, Optional, TypedDict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import errorpipe as ep
from ..core import errors as errs
from ..core import kpid, metacog
from ..core import library as lib
from ..core import tutor as tut

router = APIRouter()


# ---------------------------------------------------------------- 请求模型
class _ImportStats(TypedDict):
    """`import_jsonl` 的返回体。

    显式 TypedDict（2026-09-29）而非裸 dict 字面量：原先 `stats = {...}`
    因值是异构的（int 与 list 混排）被 mypy 推断为 `dict[str, object]`，
    于是 `stats["errors"].append(...)` 与 `stats["skipped"] += 1` 全被
    判为对 `object` 操作（7 条 error）。运行时本来是对的，**静态类型丢了**。
    这类「同键异类型」的 dict 一律用 TypedDict 明确契约。
    """
    total: int
    gated: int
    ungated: int
    created: int
    skipped: int
    errors: list[str]


class IntakeBody(BaseModel):
    """单条错题录入（含元认知字段）。

    ⚠️ 这里声明 ``confidence`` 是为了**首次录入**时带上来（看答案前填的那一次）。
    编辑端点（``PUT /api/errors/cards/{mid}``）**不接受**该字段——见该端点的说明。
    """

    id: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    question: str = ""
    options: Any = Field(default_factory=list)   # list[str] 或 dict{A:...}（JSONL 兼容）
    answer: str = ""
    user_answer: str = ""
    correct: Optional[bool] = None
    analysis: str = ""
    confidence: Optional[int] = None
    my_reasoning: str = ""
    error_tag: str = ""
    round: str = ""
    fix: str = ""
    knowledge_ref: str = ""


class EditBody(BaseModel):
    """编辑元认知字段。

    **刻意不含 ``confidence`` / ``my_reasoning``**：这两项是"看到答案前的那一秒"的
    唯一真实信号，事后可改就等于把数据变成故事（《总纲》§3.4 硬约束）。
    要修正只能删除整条重录。
    """

    error_tag: str = ""
    round: str = ""
    fix: str = ""
    knowledge_ref: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""


class AliasBody(BaseModel):
    canonical: str = ""
    kp_id: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""


class MergeBody(BaseModel):
    src_kp_id: str = ""
    dst_kp_id: str = ""


# ---------------------------------------------------------------- 录入 / 闸门
@router.post("/api/errors/intake")
def intake(body: IntakeBody) -> dict[str, Any]:
    """录入一条错题并跑流水线（P1→P5）。

    `stages` 固定为「不跑 LLM 的完整链路」：录入是高频操作，不该同步等模型。
    归因由 `POST /api/errors/cards/{mid}/attribute` 单独触发或批量触发。
    """
    payload = body.model_dump()
    if not str(payload.get("question") or "").strip():
        raise HTTPException(status_code=400, detail="题干不能为空")
    res = ep.run(payload, stages=("intake", "kp_align", "persist"))
    if res["stages"].get("intake", {}).get("missing"):
        # 未过闸门仍入库（允许晚上补归因），但明确告知缺什么
        res["warnings"].append(
            "未过闸门：缺少 " + "、".join(res["stages"]["intake"]["missing"]) +
            "——本条不参与归因，补齐后请重新录入")
    return res


@router.post("/api/errors/cards/{mid}/attribute")
def attribute(mid: str) -> dict[str, Any]:
    """对单条错题触发 AI 归因（P3 → P4）。

    **闸门前置**：未过闸门（缺 confidence / my_reasoning）直接 403，
    不给"先看答案再补自评"的绕行路径。
    """
    rec = lib.get_mistake(mid)
    if rec is None:
        raise HTTPException(status_code=404, detail="错题不存在")
    if not ep.gate_ok(rec):
        raise HTTPException(
            status_code=403,
            detail="未过闸门：AI 归因要求先填 confidence(1-5) 与 my_reasoning（看答案前）")
    res = ep.run({"id": mid, **rec}, stages=("attribute", "persist"))
    if not res["stages"].get("attribute", {}).get("ok"):
        raise HTTPException(
            status_code=502,
            detail=res["stages"]["attribute"].get("error") or "AI 归因失败")
    return res


@router.get("/api/errors/cards/{mid}/gate")
def gate(mid: str) -> dict[str, Any]:
    """查询某条是否已过闸门（前端据此决定「看答案」按钮是否可用）。"""
    rec = lib.get_mistake(mid)
    if rec is None:
        raise HTTPException(status_code=404, detail="错题不存在")
    ok = ep.gate_ok(rec)
    missing = [k for k in ep.GATE_REQUIRED if not str(rec.get(k) or "").strip()
               or (k == "confidence" and rec.get(k) is None)]
    return {"id": mid, "gate_ok": ok, "missing": missing}


@router.put("/api/errors/cards/{mid}")
def edit(mid: str, body: EditBody) -> dict[str, Any]:
    """编辑元认知字段（**不含 confidence / my_reasoning**，见 ``EditBody``）。

    按项目既有约定走 ``library.update_mistake`` 的字段白名单；
    元认知字段由 ``errorpipe`` 单独做增量写。
    """
    patch = {k: v for k, v in body.model_dump().items() if v not in ("", None)}
    if patch:
        updated = lib.update_mistake(mid, patch)
        if updated is None:
            raise HTTPException(status_code=404, detail="错题不存在")
    return {"ok": True, "id": mid}


# ---------------------------------------------------------------- 统计 / 复盘
@router.get("/api/errors/stats/calibration")
def stats_calibration(subject: str = "") -> dict[str, Any]:
    """校准曲线 + Brier 分数 + JOL 偏差（含过度自信干预告警）。"""
    cards = _cards(subject)
    return metacog.calibration(cards)


@router.get("/api/errors/stats/heatmap")
def stats_heatmap(subject: str = "", value: str = "auto") -> dict[str, Any]:
    """标签 × 科目热力图。``value`` = auto | error_tag | ai_error_tag。"""
    if value not in ("auto", "error_tag", "ai_error_tag"):
        raise HTTPException(status_code=400, detail="value 只能是 auto/error_tag/ai_error_tag")
    return metacog.heatmap(_cards(subject), value=value)


@router.get("/api/errors/stats/migration")
def stats_migration(subject: str = "") -> dict[str, Any]:
    """跨轮次错误类型迁移矩阵（含"三轮不变=修补无效"的阻塞项）。"""
    from ..core import error_events as ev
    kp_ids = [str(c.get("kp_id")) for c in _cards(subject) if c.get("kp_id")]
    return metacog.migration(ev.list_events(), kp_ids=kp_ids or None)


@router.get("/api/errors/stats/agreement")
def stats_agreement(subject: str = "") -> dict[str, Any]:
    """人工归因 vs AI 归因的一致率，并列出不一致题（自我认知偏差处，价值最高）。"""
    return metacog.tag_agreement(_cards(subject))


@router.get("/api/errors/subtract")
def subtract(subject: str = "", bottom_ratio: float = 0.25) -> dict[str, Any]:
    """减法清单：告诉用户"本周这块别看"。

    频次取自 ``syllabus``/``realexams`` 的既有数据；取不到时标 ``freq_missing``
    （**不假装有数据**）。
    """
    return metacog.subtract_plan(_cards(subject), freq=_freq_map(), bottom_ratio=bottom_ratio)


@router.get("/api/errors/overview")
def overview(subject: str = "") -> dict[str, Any]:
    """一次给全（前端复盘台首屏用；避免 5 个并行请求）。"""
    return ep.analyze(cards=_cards(subject), freq=_freq_map())


# ---------------------------------------------------------------- 知识点对齐
@router.get("/api/errors/kp/resolve")
def kp_resolve(subject: str = "", chapter: str = "", topic: str = "") -> dict[str, Any]:
    """只读探测某组 (subject, chapter, topic) 会归到哪个 kp_id（**不写库**）。"""
    kid = kpid.resolve(subject, chapter, topic, auto_register=False)
    return {"kp_id": kid, "canonical": kpid._canonical(subject, chapter, topic)}  # noqa: SLF001


@router.get("/api/errors/kp/list")
def kp_list() -> dict[str, Any]:
    """别名表全量 + 概览（用于人工检查归类是否正确）。"""
    return {"stats": kpid.stats(), "aliases": kpid.list_aliases()}


@router.post("/api/errors/kp/register")
def kp_register(body: AliasBody) -> dict[str, Any]:
    """登记别名（把某个写法显式挂到指定 kp_id 上）。"""
    if not (body.canonical or body.topic):
        raise HTTPException(status_code=400, detail="canonical 与 topic 至少给一个")
    try:
        return kpid.register(body.subject, body.chapter, body.topic,
                             canonical=body.canonical, kp_id=body.kp_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.post("/api/errors/kp/merge")
def kp_merge(body: MergeBody) -> dict[str, Any]:
    """合并两个 kp_id（人工纠错）。**不改动历史流水**——只影响今后的归类。"""
    if not body.src_kp_id or not body.dst_kp_id:
        raise HTTPException(status_code=400, detail="src_kp_id / dst_kp_id 均必填")
    if body.src_kp_id == body.dst_kp_id:
        raise HTTPException(status_code=400, detail="不能合并到自己")
    n = kpid.merge_into(body.src_kp_id, body.dst_kp_id)
    return {"merged": n, "src": body.src_kp_id, "dst": body.dst_kp_id,
            "note": "已落库的历史事件不改（流水不可变）"}


# ---------------------------------------------------------------- 导入导出 / 自检
@router.post("/api/errors/import/jsonl")
def import_jsonl(body: dict[str, Any]) -> _ImportStats:
    """批量导入 JSONL 错题（《总纲》§3.1 schema）。

    `dry_run=True` 只做归一与闸门体检，不落库——**先看清缺什么再决定要不要录**。
    """
    items = body.get("items") or []
    dry = bool(body.get("dry_run"))
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="items 必须是数组")

    stats = _ImportStats(total=len(items), gated=0, ungated=0,
                         created=0, skipped=0, errors=[])
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict):
            stats["errors"].append(f"第 {i} 条不是对象，已跳过")
            stats["skipped"] += 1
            continue
        norm = ep.normalize_card(it)
        if norm["gate_ok"]:
            stats["gated"] += 1
        else:
            stats["ungated"] += 1
        if dry:
            continue
        try:
            card = norm["card"]
            if card.get("id"):
                # 带 id 的走整链路（含归因省成本 → 只 persist）；不带 id 则先建卡
                rec = lib.add_mistake(card)
            else:
                rec = lib.add_mistake(card)
            if norm["gate_ok"]:
                ep.run({"id": rec["id"], **card}, stages=("kp_align", "persist"))
            stats["created"] += 1
        except Exception as e:  # noqa: BLE001  单条失败不阻断整批
            stats["errors"].append(f"第 {i} 条导入失败：{e}")
            stats["skipped"] += 1
    return stats


@router.get("/api/errors/export/jsonl")
def export_jsonl(subject: str = "") -> dict[str, Any]:
    """导出为 JSONL（《总纲》§2.4「输出端通用格式」；也是**唯一可携带备份**）。"""
    rows = []
    for c in _cards(subject):
        rows.append({
            "id": c.get("id"),
            "date": str(c.get("created_at") or "")[:10],
            "round": c.get("round") or "",
            "subject": c.get("subject") or "",
            "chapter": c.get("chapter") or "",
            "topic": c.get("topic") or "",
            "stem": c.get("question") or "",
            "options": c.get("options") or [],
            "my_answer": c.get("user_answer") or "",
            "correct": c.get("answer") or "",
            "is_correct": c.get("correct"),
            "confidence": c.get("confidence"),
            "my_reasoning": c.get("my_reasoning") or "",
            "error_tag": c.get("error_tag") or "",
            "ai_error_tag": c.get("ai_error_tag") or "",
            "fix": c.get("fix") or "",
            "counterfactual": c.get("counterfactual") or "",
            "kp_id": c.get("kp_id") or "",
            "knowledge_ref": c.get("knowledge_ref") or "",
        })
    return {"count": len(rows), "items": rows}


@router.get("/api/errors/health")
def health() -> dict[str, Any]:
    """管线自检：schema 版本、表就绪、闸门覆盖率。用于诊断"统计为什么是空的"。"""
    out = ep.health()
    cards = lib.list_mistakes()
    out["gate"] = {
        "total": len(cards),
        "gated": sum(1 for c in cards if ep.gate_ok(c)),
        "with_ai": sum(1 for c in cards if c.get("ai_error_tag")),
    }
    return out


@router.get("/api/errors/rounds")
def rounds() -> dict[str, Any]:
    """可选轮次（前端下拉用；单源 metacog.ROUNDS）。"""
    return {"rounds": list(metacog.ROUNDS), "tags": list(metacog.ERROR_TAGS)}


# ---------------------------------------------------------------- 苏格拉底复习（阶段 3）
class SocraticStartBody(BaseModel):
    """开一场错题复习会话。`qtype` 留空则用会话首轮默认（explain）。"""

    mistake_id: str = ""
    qtype: str = ""


class SocraticAnswerBody(BaseModel):
    session_id: str = ""
    user_answer: str = ""


def _socratic_client(cancel: Optional[threading.Event] = None) -> Any:
    """与 `library._tutor_client` 同口径（`gen` 档位），保证计费口径一致。"""
    from ..agents import get_client as _gc
    return _gc("gen", cancel=cancel)


# 答案回显剥离（红线第 3 道防线）。
#
# 为什么需要它：提示词已经明文禁止"不得给出正确答案"，契约也没有 answer 字段——
# 但 `gap` / `next_question` 是**自由文本**，模型完全可能"嘴瓢"把答案写进去
# （尤其是被追问急了的时候）。前两道防线拦不住自由文本，所以必须有一道
# **返回值出口的机械剥离**：只要文本里出现正确答案串，就整段替换为安全文案。
#
# 判据取"答案原文出现"（子串匹配，忽略大小写与空白差异）：
# 宁可误伤（把一段无害文本替掉），不可放过——误伤的代价是这轮提示质量下降，
# 放过的代价是这份错题数据资产在"学生自己想到答案"这个前提上被污染，
# 而后者恰是整条管线存在的理由。
_ANSWER_REDACTED = "（本轮反馈含答案信息，已按红线拦截——请围绕你自己的推理再试一次。）"


def _answer_tokens(rec: dict[str, Any]) -> list[str]:
    """提取需要拦截的答案串（**只取非平凡长度**，避免 "A"/"B" 这类单字母全量误伤）。"""
    raw = str(rec.get("answer") or "").strip()
    if len(raw) < 2:
        # 单字符答案（"A"）无法用子串判定——它在任何中文文本里都可能偶然出现。
        # 这种情况交由提示词与契约防守，不做机械剥离（误伤率 100%，得不偿失）。
        return []
    toks = {raw}
    # 选项型答案 "A. 增加" / "A、增加" → 同时拦选项正文（模型更可能抄正文）
    for sep in (". ", "、", "．", ":", "："):
        if sep in raw:
            _, _, tail = raw.partition(sep)
            if len(tail.strip()) >= 2:
                toks.add(tail.strip())
    return [t for t in toks if len(t) >= 2]


def _norm_for_scan(s: str) -> str:
    """归一化：去空白 + 大小写无关。跨行/加空格的"绕行"写法也要能命中。"""
    return "".join(str(s or "").split()).casefold()


def _strip_answer_echo(text: str, rec: dict[str, Any]) -> tuple[str, bool]:
    """把文本里的答案串剥掉。返回 (清洗后文本, 是否发生过剥离)。"""
    if not text:
        return text, False
    toks = _answer_tokens(rec)
    if not toks:
        return text, False
    norm = _norm_for_scan(text)
    if any(_norm_for_scan(t) in norm for t in toks):
        return _ANSWER_REDACTED, True
    return text, False


def _socratic_precheck(mid: str) -> dict[str, Any]:
    """苏格拉底的**准入条件**：错题存在 + 已归因且通过闸门。

    为什么不复用 `ep.gate_ok`：闸门只管"有没有填 confidence/my_reasoning"，
    而苏格拉底追问要**打在当初那个错误岔路口**上，锚点来自 `error_tag` + `fix`。
    没有归因就没有锚点，硬开只会问成"再讲一遍这道题"——那正是本功能要避免的。
    """
    rec = lib.get_mistake(mid)
    if rec is None:
        raise HTTPException(status_code=404, detail="错题不存在")
    if not ep.gate_ok(rec):
        raise HTTPException(
            status_code=403,
            detail="未过闸门：复习要求先填 confidence(1-5) 与 my_reasoning（看答案前）")
    if not str(rec.get("error_tag") or "").strip():
        raise HTTPException(
            status_code=409,
            detail="该错题尚未归因——请先触发 AI 归因（POST /api/errors/cards/{mid}/attribute），"
                   "苏格拉底追问需要一个错误类型作为锚点")
    return rec


@router.get("/api/errors/socratic/eligible")
def socratic_eligible(subject: str = "") -> dict[str, Any]:
    """列出**可开复习**的错题（已过闸门 + 已归因），并给出推荐顺序。

    排序口径：`stuck` 无从得知（会话级），故按「错题本身的可复习价值」排——
    ① 未命中过岔路口的历史轮次多；② 错题正确率低；③ 真题考频高。
    这是**只读探测**，前端据此决定哪些错题显示「苏格拉底复习」入口。
    """
    freq = _freq_map()
    rows: list[dict[str, Any]] = []
    for c in _cards(subject):
        if not ep.gate_ok(c):
            continue
        tag = str(c.get("error_tag") or "").strip()
        if not tag:
            continue
        chap = str(c.get("chapter") or "")
        rows.append({
            "id": str(c.get("id") or ""),
            "subject": c.get("subject") or "",
            "chapter": chap,
            "topic": c.get("topic") or "",
            "error_tag": tag,
            "fix": c.get("fix") or "",
            "confidence": c.get("confidence"),
            "round": c.get("round") or "",
            "freq": int(freq.get(chap, 0)),
        })
    # 频次高 → 更值得花时间；同类聚在一起便于连续复习
    rows.sort(key=lambda r: (-r["freq"], r["subject"], r["chapter"]))
    return {"count": len(rows), "items": rows}


@router.post("/api/errors/socratic/start")
def socratic_start(body: SocraticStartBody) -> dict[str, Any]:
    """开一场苏格拉底式错题复习：出第一问（锚定学生当初的错误推理）。

    第一问生成失败 → **回滚会话**（照 `library.tutor_start` 的 D-04 做法），
    不留"没有问题文本的空会话"伪装成已复习。
    """
    if not body.mistake_id.strip():
        raise HTTPException(status_code=400, detail="缺少 mistake_id")
    rec = _socratic_precheck(body.mistake_id)

    from ..agents import socratic_review as soc
    subject = str(rec.get("subject") or "")
    kp_name = str(rec.get("topic") or rec.get("chapter") or "")
    kp_id = str(rec.get("kp_id") or "")
    state = "weak"
    with errs.swallow("socratic.state", "读取知识点掌握度失败（默认 weak）"):
        kp = next((k for k in lib.list_knowledge()
                   if kp_id and str(k.get("kp_id") or "") == kp_id), None)
        if kp:
            state = str(kp.get("state") or "weak")

    qtype = body.qtype if body.qtype in ("explain", "apply", "contrast", "predict", "trace") \
        else "explain"
    session = tut.start_mistake_session(body.mistake_id, subject, kp_name, kp_id, state)
    try:
        question = soc.start_applying(_socratic_client(), rec, qtype, state, 0)
    except Exception as e:  # noqa: BLE001
        tut.delete_session(session["id"])
        raise HTTPException(
            status_code=502,
            detail=f"第一问生成失败，请稍后重试（{type(e).__name__}）") from e
    if not (question or "").strip():
        tut.delete_session(session["id"])
        raise HTTPException(status_code=502, detail="第一问生成失败（模型返回为空），请稍后重试")
    # 红线第 3 道：第一问同样可能夹带答案（模型"顺手"把答案写进提问）
    question, leaked = _strip_answer_echo(question, rec)
    if leaked:
        tut.delete_session(session["id"])
        errs.record("socratic.start_echo", "第一问含正确答案，已作废重开",
                    mistake_id=str(rec.get("id") or ""))
        raise HTTPException(
            status_code=502,
            detail="模型的第一问夹带了答案信息，已按红线作废——请重试")
    tut.seed_first(session["id"], qtype, question)
    return {"session": session, "question": question, "type": qtype, "state": state,
            "error_tag": rec.get("error_tag") or "", "fix": rec.get("fix") or ""}


@router.post("/api/errors/socratic/answer")
def socratic_answer(body: SocraticAnswerBody) -> dict[str, Any]:
    """提交一轮复习作答：LLM 判分 + 出下一问；命中岔路口与否决定追问还是换档。

    **红线守卫（第 3 道）**：返回值里绝不允许出现正确答案文本。
    `answer` 只在 `_socratic_precheck` 里被读出来喂给提示词，
    经 `_leak_check()` 断言后才返回——见 `test_socratic_never_leaks_answer`。
    """
    if not body.session_id.strip():
        raise HTTPException(status_code=400, detail="缺少会话 ID")
    session = tut.get_session(body.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="复习会话不存在")
    if session.get("kind") != "mistake":
        raise HTTPException(status_code=409, detail="该会话不是错题复习会话")
    cur = session.get("current") or {"type": "explain", "text": ""}
    if not cur.get("text"):
        raise HTTPException(status_code=400, detail="该会话当前没有待回答的问题")
    if len(session.get("rounds") or []) >= tut.MAX_ROUNDS:
        return {"session": session, "score": 0, "gap": "", "next_question": cur,
                "state": session.get("state", "weak"), "retry": False, "done": True,
                "hit_crossroad": False,
                "note": "已达本轮轮次上限，感谢练习——可另开一场会话继续"}

    rec = lib.get_mistake(str(session.get("mistake_id") or ""))
    if rec is None:
        raise HTTPException(status_code=404, detail="该会话锚定的错题已不存在（可能被删除）")

    from ..agents import socratic_review as soc
    result = soc.score_answer(
        _socratic_client(), rec, body.user_answer,
        qtype=cur.get("type", "explain"),
        state=session.get("state", "weak"),
        history=session.get("rounds") or [],
        stuck_rounds=int(session.get("stuck", 0)),
    )
    score = int(result.get("score", -1))
    if score < 0:
        # 无法判定 → 不计分、不记轮次、不改状态，请学生围绕考点重答
        gap, _ = _strip_answer_echo(str(result.get("gap") or ""), rec)
        return {"session": session, "score": -1,
                "gap": gap or "回答未能可靠评分，请围绕考点再试一次。",
                "next_question": cur, "state": session.get("state", "weak"),
                "retry": True, "hit_crossroad": False}

    # 红线第 3 道：出口剥离答案回显（模型可能把答案写进自由文本）
    gap_raw = str(result.get("gap") or "")
    nq_raw = str(result.get("next_question") or "")
    gap, gap_leaked = _strip_answer_echo(gap_raw, rec)
    nq, nq_leaked = _strip_answer_echo(nq_raw, rec)
    if gap_leaked or nq_leaked:
        # 留痕：这条记录用于评估"模型多常违反红线"，也是提示词迭代的依据
        errs.record("socratic.answer_echo",
                    "模型输出含正确答案，已在出口剥离",
                    mistake_id=str(rec.get("id") or ""), in_gap=gap_leaked,
                    in_next_question=nq_leaked)
    if nq_leaked:
        # 下一问被剥掉 → 不能把空问题喂给下一轮（学生会看到空白）
        nq = "换个角度：不讲答案，只说这一步推理里你当时最确定的一环和最不确定的一环。"

    updated = tut.record_mistake_answer(
        session["id"], body.user_answer, score, gap, nq,
        bool(result.get("hit_crossroad")))
    if updated is None:
        raise HTTPException(status_code=404, detail="复习会话不存在或类型不符")
    return {
        "session": updated, "score": score, "gap": gap,
        "next_question": updated["current"], "state": updated["state"],
        "hit_crossroad": bool(result.get("hit_crossroad")),
        "stuck": int(updated.get("stuck", 0)),
        "stuck_limit": tut.STUCK_ROUNDS,
        "where_uncertain": list(result.get("where_uncertain") or []),
        # 提示词要求"连续 N 轮未命中才允许方向性提示"，前端据此显示剩余轮数
        "hint_allowed": int(updated.get("stuck", 0)) >= tut.STUCK_ROUNDS,
        "redacted": bool(gap_leaked or nq_leaked),
    }


@router.get("/api/errors/socratic/{sid}")
def socratic_get(sid: str) -> dict[str, Any]:
    """读取一场复习会话（前端刷新/断线重连后恢复上下文）。"""
    session = tut.get_session(sid)
    if session is None:
        raise HTTPException(status_code=404, detail="复习会话不存在")
    if session.get("kind") != "mistake":
        raise HTTPException(status_code=409, detail="该会话不是错题复习会话")
    return {"session": session}


# ---------------------------------------------------------------- 内部工具
def _cards(subject: str = "") -> list[dict[str, Any]]:
    cards = lib.list_mistakes()
    if subject:
        cards = [c for c in cards if str(c.get("subject") or "") == subject]
    return cards


def _freq_map() -> dict[str, int]:
    """章节 → 已确认真题频次（来自 `realexam_freq`，仅 `confirmed=1` 计入）。

    取不到就返回空 dict → `subtract_plan` 会标 `freq_missing=True`。
    **刻意不编造频次**：减法清单是个"建议别看"的决策，不能建立在假数据上。

    实现说明（EP-01 修复）：原实现探测 `realexams.list_freq()`，
    但该模块的公开函数叫 **`freq_view()`**（`list_freq` 从不存在）→ `hasattr` 恒为 False，
    **静默返回空 dict**，减法清单永远带 `freq_missing=True`（真题考频白导）。
    这类「用 hasattr 探测能力」的写法失败时没有任何信号，故：
    ① 改为直接调用确实存在的 `freq_view()`（它已按章节聚合好）；
    ② 异常改走 `errors.swallow` 留痕，不再静默吞。
    """
    from ..core import realexams

    with errs.swallow("errorpipe.freq_map", "真题考频取用失败（减法清单退化为仅按题量×正确率）"):
        view = realexams.freq_view()
        return {str(c.get("chapter") or ""): int(c.get("freq") or 0)
                for c in (view.get("chapters") or [])
                if c.get("chapter")}
    return {}


# 说明：`threading` 仅为与同族路由模块保持一致的导入面；本域当前无长任务后台线程。
_ = threading
