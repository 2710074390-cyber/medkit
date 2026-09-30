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

import asyncio
import json
import queue
import re
import threading
from typing import Any, Iterator, Optional, TypedDict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..core import errorpipe as ep
from ..core import errors as errs
from ..core import kpid, metacog, vision
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


# ---------------------------------------------------------------- 图像录入（EP-01 图像输入）
#
# ## 与「错题本 → 拍题(图片 OCR)」的区别（`/api/library/mistakes/import-image`）
#
# 那条路径只做 MinerU OCR、把识别文本回填输入框，**不识别模型能力、也不进归因管线**。
# 本组端点是 EP-01 的**图像输入口**：原生视觉优先 → OCR 兜底 → 归一化 → 闸门 →
# 归因 → 落库，全程可流式观察。
#
# ## 四条设计约束
#
# 1. **识别通道的选择只有一处**（`vision.plan()`）。本层不自己判断"模型支不支持视觉"，
#    否则前后端各判一套、口径必然分裂（R14 的教训：同一事实两个来源会各自漂移）。
# 2. **答案必须带出处**：识别出的 `answer` 只有带 `answer_from_image` 才被采用
#    （`vision.sanitize` 强制）。这条让"模型编一个答案写进 mistakes.answer"在数据层就断掉。
# 3. **图片入口不放宽闸门**：`intake/image` 仍然要求 confidence + my_reasoning
#    才跑 AI 归因；缺了就只入库、跳过 P3，并明确告诉用户（不是静默降级）。
# 4. **流式只用于"让等待可感知"**：识别阶段推 `delta`（模型原始输出），
#    流水线阶段推 `stage`（复用 `errorpipe.run` 的 progress 回调）。
#
# ## 响应信封
#
# `extract` 用 **200 + `{ok:false, error}`** 而不是 4xx：识别失败的原因往往有两条
# （视觉超限 / OCR 超时），`attempts` 里逐条记录，前端要能原样展示；
# 塞进 `detail` 一个字符串会丢掉这个信息。

_PREFER_CHOICES = ("auto", "vision", "ocr")
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
# 选项头：`A.` / `A、` / `A．` / `A,` / `A:` / `A)`
_CHOICE_HEAD_RE = re.compile(r"^\s*([A-Ha-h])\s*[.、．,，:：)]")


def _sse(event: str, data: Any) -> str:
    """SSE 帧（与 `routers/library.py` 同格式：`event:` + `data:` + 空行）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _check_prefer(prefer: str) -> str:
    if prefer not in _PREFER_CHOICES:
        raise HTTPException(status_code=400, detail="prefer 只能是 auto/vision/ocr")
    return prefer


@router.get("/api/errors/image/capability")
def image_capability() -> dict[str, Any]:
    """当前配置下的识别能力（前端横幅与端点自检用）。

    `preferred` 就是 `prefer="auto"` **实际会走**的那条路——前端直接展示它，
    不二次判断（避免前后端各判一套）。
    """
    return vision.capability()


@router.post("/api/errors/image/extract")
async def image_extract(file: UploadFile = File(...),
                        prefer: str = Form("auto")) -> dict[str, Any]:
    """图片 → 结构化字段（非流式，**不落库、不跑归因**）。

    供"先识别、看清了再决定入不入库"的客户端，以及不方便消费 SSE 的调用方。
    流式版见 `POST /api/errors/image/extract/stream`（两者共用同一套编排，
    返回体形状一致——见 `vision.iter_extract`）。
    """
    _check_prefer(prefer)
    data = await file.read()
    if not data.strip():
        raise HTTPException(status_code=400, detail="图片为空（0 字节）——请重新拍照或截图")
    # 识别可能耗时数十秒（OCR 轮询 / 视觉模型读图），必须离开事件循环
    return await asyncio.to_thread(vision.extract, data, prefer=prefer)


@router.post("/api/errors/image/extract/stream")
async def image_extract_stream(file: UploadFile = File(...),
                               prefer: str = Form("auto")) -> StreamingResponse:
    """图片 → 结构化字段（SSE 流式）。

    事件序列：`stage`* → (`delta`* 仅视觉路径) → `result`；
    生成器内部异常 → `error`（**必须有这一帧**：否则前端只看到连接断掉，无从判断原因）。
    """
    _check_prefer(prefer)
    data = await file.read()
    cancel_ev = threading.Event()

    def gen():
        try:
            for ev in vision.iter_extract(data, prefer=prefer, stream=True, cancel=cancel_ev):
                kind = str(ev.get("type") or "message")
                if kind == "result":
                    yield _sse("result", ev)
                elif kind == "delta":
                    yield _sse("delta", {"text": ev.get("text") or ""})
                else:
                    yield _sse(kind, {k: v for k, v in ev.items() if k != "type"})
        except Exception as e:  # noqa: BLE001  流内异常转 SSE error 帧（不是 500 断流）
            yield _sse("error", {"msg": f"识别过程出错：{e}"})
        finally:
            cancel_ev.set()   # 断连/结束全路径置位：让 OCR 轮询与模型流及时收手

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


def _norm_choice(v: str) -> str:
    """选项类答案归一：`"b"` / `"B."` / `"B、"` / `"A. 支气管炎"` → `"A"`/`"B"`。

    非选项（填空、长文本答案）原样返回——**不能**把答案正文里的字母抠出来当选项，
    否则"患者男"里的字母会被当成选项字母，`correct` 就变成了随机数。
    """
    s = str(v or "").strip()
    m = _CHOICE_HEAD_RE.match(s)
    if m:
        return m.group(1).upper()
    letters = re.sub(r"[^A-Ha-h]", "", s)
    if letters and len(s) <= 4:      # "BD" / "B,D" / "(B)"
        return letters.upper()
    return s


def _payload_from_fields(fields: dict[str, Any], *, confidence: str, my_reasoning: str,
                         subject: str, chapter: str, topic: str) -> dict[str, Any]:
    """识别结果 + 表单输入 → `errorpipe.run` 的入参。

    `correct` 只在**图中同时有答案与考生作答**时才能派生（两边都有才比，避免把"没看到"
    当成"答错了"）。派生不出来就留 `None`——`normalize_card` 会原样保留 None，
    不会把它当 False（`errorpipe` 里那条「绝不把 None 当 False」的注释就是为此）。
    """
    ans = str(fields.get("answer") or "").strip()
    ua = str(fields.get("user_answer") or "").strip()
    correct: Optional[bool] = None
    if ans and ua:
        correct = _norm_choice(ua) == _norm_choice(ans)
    return {
        "source": "image",
        "question": str(fields.get("question") or ""),
        "options": fields.get("options") or [],
        "answer": ans,
        "user_answer": ua,
        "analysis": str(fields.get("analysis") or ""),
        # 表单显式填写优先于模型读到的（用户改过就以用户为准）
        "subject": subject.strip() or str(fields.get("subject") or ""),
        "chapter": chapter.strip() or str(fields.get("chapter") or ""),
        "topic": topic.strip() or str(fields.get("topic") or ""),
        "confidence": confidence.strip() or None,
        "my_reasoning": my_reasoning.strip(),
        "correct": correct,
    }


def _run_pipeline_stream(payload: dict[str, Any],
                         stages: tuple[str, ...]) -> Iterator[tuple[str, dict[str, Any]]]:
    """跑 `errorpipe.run` 并把它**同步**的 `progress` 回调桥成事件流。

    yield `("stage", {name, label})`；终态 yield `("result", res)` 或 `("error", {msg})`。

    为什么要这一层：`errorpipe.run` 是同步函数、`progress` 是同步回调，
    而端点是生成器 ⇒ 在 worker 线程里跑流水线、用 `queue.Queue` 把事件桥出来。
    **两条录入路径（一步到位 / 两步确认）共用这一个实现**——
    否则"给其中一条加个阶段提示、另一条忘了"这类漂移必然发生
    （本项目已有一处「两份编排各自漂移」的教训）。
    """
    q: queue.Queue = queue.Queue()
    box: dict[str, Any] = {}

    def _worker() -> None:
        try:
            box["res"] = ep.run(
                payload, stages=stages,
                progress=lambda s, m: q.put(("stage", {"name": s, "label": m})))
        except Exception as e:  # noqa: BLE001  流水线异常 → 作为终态返回，不吞
            box["err"] = f"{type(e).__name__}: {e}"
        finally:
            q.put(("__end__", None))

    t = threading.Thread(target=_worker, daemon=True, name="medkit-intake")
    t.start()
    while True:
        try:
            kind, ev = q.get(timeout=0.5)
        except queue.Empty:
            if not t.is_alive():
                break
            continue
        if kind == "__end__":
            break
        yield kind, ev

    if box.get("err"):
        errs.record("errors.intake_stream", "流水线执行失败", detail=str(box["err"]))
        yield "error", {"msg": f"入库失败：{box['err']}"}
        return
    yield "result", box.get("res") or {}


def _stages_for(want_attr: bool) -> tuple[str, ...]:
    """要跑哪几段。`attribute` 是唯一有外部依赖的阶段，用户可关掉。"""
    return (("intake", "kp_align", "attribute", "persist") if want_attr
            else ("intake", "kp_align", "persist"))


def _done_frame(res: dict[str, Any], *, via: str, fields: dict[str, Any],
                extra_warnings: list[str]) -> dict[str, Any]:
    """`done` 帧的构造（两条路径共用，字段形状一致）。"""
    card = res.get("card") or {}
    return {
        "via": via,
        "fields": fields,
        "card": card,
        "stages": res.get("stages") or {},
        "warnings": list(extra_warnings) + (res.get("warnings") or []),
        "gate_ok": ep.gate_ok(card) if card else False,
        "attributed": bool((res.get("stages") or {}).get("attribute", {}).get("ok")),
    }


@router.post("/api/errors/intake/image")
async def intake_image(file: UploadFile = File(...),
                       prefer: str = Form("auto"),
                       confidence: str = Form(""),
                       my_reasoning: str = Form(""),
                       subject: str = Form(""),
                       chapter: str = Form(""),
                       topic: str = Form(""),
                       attribute: str = Form("1")) -> StreamingResponse:
    """拍一张错题图 → 识别 → 归一 → 知识点对齐 →（可选）AI 归因 → 落库。**全程 SSE**。

    这是**一步到位**路径（识别完直接入库）。想先核对识别结果再入库的，用
    `POST /api/errors/image/extract/stream` + `POST /api/errors/intake/stream` 两步走
    ——OCR 认错字是常态，核对一下比事后改错题划算（前端默认走两步，
    「识别后直接入库」勾选才走本端点）。

    ## 为什么 confidence / my_reasoning 由**表单**传入而不是从图里抽

    《总纲》§3.4：这两项必须在**看答案前**填，事后任何写入通道都会让校准数据失效。
    图片里通常就印着答案，所以"先识别再让用户补自评"等于允许事后补填——
    故本端点要求前端**先收齐这两项再发起识别**（UI 上「开始识别」按钮在两项填好前禁用）。
    识别契约里也**没有**这两个字段（见 `schema.ErrorImageExtract`），
    模型没有任何通道代填。

    ## 事件序列

    `stage`* → (`delta`*) → `fields` → `stage`*（流水线各阶段）→ `done`；
    任何一步硬失败 → `error`（已落库的部分不回滚，`done` 里带 warnings 说明）。
    """
    _check_prefer(prefer)
    data = await file.read()
    if not data.strip():
        raise HTTPException(status_code=400, detail="图片为空（0 字节）——请重新拍照或截图")

    cancel_ev = threading.Event()
    stages = _stages_for(str(attribute).strip().lower() not in ("0", "false", "no", ""))

    def gen():
        try:
            yield _sse("stage", {"name": "recognize", "label": "识别图片…"})
            final: dict[str, Any] = {}
            for ev in vision.iter_extract(data, prefer=prefer, stream=True, cancel=cancel_ev):
                kind = str(ev.get("type") or "")
                if kind == "result":
                    final = ev
                elif kind == "delta":
                    yield _sse("delta", {"text": ev.get("text") or ""})
                else:
                    yield _sse(kind, {k: v for k, v in ev.items() if k != "type"})
            if not final.get("ok"):
                yield _sse("error", {"msg": final.get("error") or "识别失败",
                                     "attempts": final.get("attempts") or []})
                return

            fields = final.get("fields") or {}
            yield _sse("fields", {"via": final.get("via"), "fields": fields,
                                  "warnings": final.get("warnings") or []})

            payload = _payload_from_fields(
                fields, confidence=confidence, my_reasoning=my_reasoning,
                subject=subject, chapter=chapter, topic=topic)
            for name, data_ev in _run_pipeline_stream(payload, stages):
                if name == "result":
                    yield _sse("done", _done_frame(data_ev, via=final.get("via") or "",
                                                   fields=fields,
                                                   extra_warnings=final.get("warnings") or []))
                    return
                yield _sse(name, data_ev)
        except Exception as e:  # noqa: BLE001  生成器内任何异常都要变成 error 帧
            yield _sse("error", {"msg": f"识别或入库过程出错：{e}"})
        finally:
            cancel_ev.set()

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


class FieldIntakeBody(BaseModel):
    """已核对字段 → 入库（两步流程的第二步）。

    `fields` 的形状 = `ErrorImageExtract.model_dump()`（前端拿到识别结果、用户改完之后原样回传）。
    这里**不**接受图片：图片那一半在 `/api/errors/image/extract/stream`。
    """

    fields: dict[str, Any] = Field(default_factory=dict)
    confidence: str = ""
    my_reasoning: str = ""
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    attribute: bool = True


@router.post("/api/errors/intake/stream")
def intake_stream(body: FieldIntakeBody) -> StreamingResponse:
    """**已确认的字段** → 归一 → 知识点对齐 →（可选）AI 归因 → 落库。**全程 SSE**。

    与 `intake/image` 的分工：那条是"图 → 一次做完"，本端点是"**用户核对过的字段** → 入库"。
    两条路共用 `_payload_from_fields` 与 `_run_pipeline_stream`，
    所以闸门语义、`correct` 派生、阶段事件**完全一致**（不存在"两步走的那条松一点"）。

    ## 为什么需要它（不是多余的）

    OCR 认错字是常态（纸质讲义照片尤甚）。一步到位意味着**错字连同闸门数据一起落库**，
    事后要改只能删了重录——而 `confidence` / `my_reasoning` 是**不可补填**的，
    重录就等于让用户重新回忆当时的确信程度（那份回忆已经不可靠了）。
    成熟客户端（Cherry Studio / LobeChat 一类）的做法都是「识别 → 校对 → 提交」，
    这里补上第二步。

    ## 闸门不放宽

    `confidence` / `my_reasoning` 仍由**请求体**传入（前端在识别**之前**就收齐），
    与 `intake/image` 同口径。未填则只入库、跳过 P3 并在 `done` 里明说。
    """
    payload = _payload_from_fields(
        body.fields, confidence=body.confidence, my_reasoning=body.my_reasoning,
        subject=body.subject, chapter=body.chapter, topic=body.topic)
    if not str(payload.get("question") or "").strip():
        raise HTTPException(status_code=400, detail="题干不能为空——请先识别或手工填写题干")
    stages = _stages_for(bool(body.attribute))

    def gen():
        try:
            yield _sse("stage", {"name": "intake", "label": "开始入库…"})
            for name, data_ev in _run_pipeline_stream(payload, stages):
                if name == "result":
                    yield _sse("done", _done_frame(data_ev, via="manual",
                                                   fields=body.fields, extra_warnings=[]))
                    return
                yield _sse(name, data_ev)
        except Exception as e:  # noqa: BLE001
            yield _sse("error", {"msg": f"入库过程出错：{e}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)



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


# 说明：`threading` 用于图像录入端点的取消事件与流水线 worker 线程（见 `intake_image`）。
