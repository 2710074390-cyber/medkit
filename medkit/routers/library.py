"""routers：个人学习库（M1/M2）——错题沉淀 + 掌握度诊断。

命名空间 /api/library/*，挂载进 main.py。全部数据在本机；不调 LLM（导入结构化走本地规则，
OCR 复用 MinerU；掌握度/优先级纯本地）。错题结构从押题卷（source=paper）和手动/文本/图片导入。
"""

import asyncio
import tempfile
from pathlib import Path
from typing import Any, Iterator

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..agents import medcards
from ..core import cards as cardlib
from ..core import config as cfg
from ..core import dashboard as dash
from ..core import explain as expl
from ..core import library as lib
from ..core import review as rev
from ..core import tutor as tut
from ..core.config import resolve_key

router = APIRouter()


# ---------------------------------------------------------------- 错题导入 / CRUD
class MistakeBody(BaseModel):
    source: str = "manual"
    source_ref: dict[str, Any] = {}
    subject: str = ""
    chapter: str = ""
    topic: str = ""
    question: str = ""
    options: list[str] = []
    answer: str = ""
    user_answer: str = ""
    correct: bool = False
    analysis: str = ""
    error_reason: str = ""
    know_tags: list[str] = []
    bloom: str = ""
    miss_count: int = 1
    learned: bool = False


class SyncPaperBody(BaseModel):
    pid: str = ""
    questions: list[dict[str, Any]] = []


@router.get("/api/library/mistakes")
def mistakes() -> dict[str, Any]:
    return {"mistakes": lib.list_mistakes()}


@router.get("/api/library/mistakes/{mid}")
def mistake(mid: str) -> dict[str, Any]:
    m = next((x for x in lib.list_mistakes() if x.get("id") == mid), None)
    if m is None:
        raise HTTPException(404, "错题不存在")
    return {"mistake": m}


@router.post("/api/library/mistakes")
def add_mistake(body: MistakeBody) -> dict[str, Any]:
    if not body.question.strip():
        raise HTTPException(400, "题干不能为空")
    rec = lib.add_mistake(body.model_dump())
    return {"ok": True, "mistake": rec}


@router.post("/api/library/mistakes/batch")
def batch_mistakes(body: list[MistakeBody]) -> dict[str, Any]:
    added = lib.batch_add([b.model_dump() for b in body])
    return {"ok": True, "added": added}


@router.post("/api/library/mistakes/import-file")
async def import_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """批量导入文件（本地解析，零 LLM）：.json / .csv / .md / .txt，按扩展名分派。"""
    name = file.filename or ""
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise HTTPException(400, "文件编码无法识别（请用 UTF-8）")
    text = text.lstrip("\ufeff")   # D-06：Excel「CSV UTF-8」带 BOM → 剥离（否则首列变 \ufeff 题干，全行静默过滤）
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else "txt"
    if ext not in ("json", "csv", "md", "txt"):
        raise HTTPException(400, f"不支持的文件类型 .{ext}（支持 json / csv / md / txt）")
    rows = lib.parse_import_text(text, ext)
    if not rows:
        raise HTTPException(400, "未解析出题目——请检查文件格式（JSON 数组 / CSV 带表头 / 每题带题号与答案）")
    added = lib.batch_add(rows)
    return {"ok": True, "added": added, "total": len(rows), "skipped": len(rows) - added, "file": name}


@router.post("/api/library/mistakes/sync-paper")
def sync_paper(body: SyncPaperBody) -> dict[str, Any]:
    pid = body.pid or ""
    added = lib.sync_from_paper(body.questions, pid=pid or None)
    return {"ok": True, "added": added}


@router.post("/api/library/mistakes/import-text")
def import_text(payload: MistakeBody) -> dict[str, Any]:
    """文本导入：若用户只贴了原文（question 含题干+选项+答案），先本地结构化再入库。"""
    if not payload.question.strip():
        raise HTTPException(400, "内容不能为空")
    # 若未显式拆分答案/选项且正文较长 → 走本地解析器
    raw = payload.question
    if not payload.options and (not payload.answer or "\n" in raw):
        structured = lib.parse_question_text(raw)
        merged = payload.model_dump()
        for k, v in structured.items():
            if v and not (k in merged and merged[k]):
                merged[k] = v
        merged["question"] = structured["question"] or merged["question"]
        rec = lib.add_mistake(merged)
        return {"ok": True, "parsed": True, "mistake": rec}
    rec = lib.add_mistake(payload.model_dump())
    return {"ok": True, "parsed": False, "mistake": rec}


@router.put("/api/library/mistakes/{mid}")
def edit_mistake(mid: str, body: MistakeBody) -> dict[str, Any]:
    rec = lib.update_mistake(mid, body.model_dump(exclude_unset=True))
    if rec is None:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "mistake": rec}


@router.post("/api/library/mistakes/{mid}/learn")
def learn_mistake(mid: str, learned: bool = True) -> dict[str, Any]:
    rec = lib.mark_learned(mid, learned)
    if rec is None:
        raise HTTPException(404, "错题不存在")
    return {"ok": True, "mistake": rec}


@router.delete("/api/library/mistakes/{mid}")
def drop_mistake(mid: str) -> dict[str, Any]:
    if not lib.delete_mistake(mid):
        raise HTTPException(404, "错题不存在")
    return {"ok": True}


# ---------------------------------------------------------------- 图片 OCR 导入（复用 MinerU）
@router.post("/api/library/mistakes/import-image")
async def import_image(file: UploadFile = File(...)) -> dict[str, Any]:
    """拍题/图片错题 → MinerU OCR → 返回识别文本，由前端填充后经 import-text 确认入库。"""
    from ..core import mineru as mineru_mod
    from ..core.mineru import MinerUError

    cfg_data = cfg.load()
    api_key = resolve_key((cfg_data.get("mineru", {}) or {}).get("api_key", ""))
    if not api_key:
        raise HTTPException(400, "请先在「① 连接服务商」配置 MinerU OCR API Key")
    suffix = Path(file.filename or "").suffix.lower()
    data = await file.read()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise HTTPException(400, f"不支持的类型 {suffix}（仅图片）")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp.write(data)
    tmp_path = tmp.name
    try:
        client = mineru_mod.MinerUClient(api_key)
        # extract 内含上传+轮询，可耗时数分钟，须放线程池以免阻塞事件循环冻结全站
        markdown = await asyncio.to_thread(client.extract, tmp_path)
    except MinerUError as e:
        raise HTTPException(502, f"OCR 识别失败：{e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return {"ok": True, "via": client.mode(), "text": markdown or ""}


# ---------------------------------------------------------------- 掌握度 / 推荐（M2）
@router.get("/api/library/mastery")
def mastery(subject: str = "") -> dict[str, Any]:
    """C1：掌握度视图（可选 subject 过滤——概览顶部与闭环总览同口径）。"""
    return lib.get_mastery_view((subject or "").strip())


@router.get("/api/library/recommend")
def recommend(limit: int = 10, subject: str = "") -> dict[str, Any]:
    limit = max(1, min(int(limit), 50))
    return {"recommend": lib.recommend(limit, (subject or "").strip())}


@router.get("/api/library/dashboard")
def dashboard(subject: str = "") -> dict[str, Any]:
    """学习闭环总览：掌握度 + 复习(SM-2) + 提问式学习 三闭环聚合（按科目可选）。"""
    d = dash.summary(subject)
    d["corrupted"] = lib.scan_corrupted().get("corrupted", 0)   # v0.7.1：数据卫生提示
    return d


# ---------------------------------------------------------------- 数据卫生（v0.7.1）
@router.get("/api/library/maintenance/scan")
def maintenance_scan() -> dict[str, Any]:
    """扫描学习库编码损坏记录（可逆 cp1252 误读 or 不可逆 '?' 串）。"""
    return lib.scan_corrupted()


@router.post("/api/library/maintenance/heal")
def maintenance_heal() -> dict[str, Any]:
    """一键修复：备份 → 还原可逆乱码 → 不可逆记录打标记（不删数据）。"""
    return lib.heal_encoding()


# ---------------------------------------------------------------- M3 讲解与产物（按科目）
class ExplainBody(BaseModel):
    subject: str = ""
    kp_name: str = ""
    kp_id: str = ""
    mistake_id: str = ""
    use_web: bool = True


def _explain_client():
    from ..agents import get_client as _gc
    return _gc("gen")


def _resolve_subject_kp(body: ExplainBody) -> tuple[str, str, list[dict[str, Any]]]:
    """解析 subject + kp_name；kp_id 或 mistake_id 优先回填科目/知识点。"""
    subject, kp_name = body.subject.strip(), body.kp_name.strip()
    mistakes = lib.list_mistakes()
    related: dict[str, Any] | None = None
    if body.mistake_id:
        related = next((m for m in mistakes if m.get("id") == body.mistake_id), None)
        if related is None:
            raise HTTPException(404, "错题不存在")
    if not kp_name:
        # 从 kp_id 反查
        if body.kp_id:
            kp = next((k for k in lib.list_knowledge() if k.get("id") == body.kp_id), None)
            if kp is None:
                raise HTTPException(404, "知识点不存在")
            kp_name = kp.get("name") or ""
            subject = subject or kp.get("subject") or ""
        elif related:
            kp_name = (related.get("know_tags") or [""])[0] or related.get("topic") or ""
            subject = subject or related.get("subject") or ""
    if not kp_name:
        raise HTTPException(400, "请指定待讲解的知识点")
    if not subject:
        subject = related.get("subject") or "未分类" if related else "未分类"
    return subject, kp_name, [related] if related else []


@router.get("/api/library/subjects")
def subjects() -> dict[str, Any]:
    """已覆盖科目清单（错题 + 知识点 + 讲解产物并集），附每科统计（刷题页科目卡片）。

    v0.8.1：新增 `stats`（每科错题数/知识点数/掌握率/复习卡数与今日到期）——全部本地计算，
    复用 mastery 视图与 review.stats 口径，零 LLM、零新表。
    R3-19：一次扫描按 subject 聚合（消除逐科目 get_mastery_view + rev.stats 的 N+1），口径不变。
    """
    from collections import defaultdict
    from datetime import date as _date

    mistakes = lib.list_mistakes()
    kps = lib.list_knowledge()
    explains = expl.list_explains()
    cards = rev.list_cards()
    seen: set[str] = set()
    mis_by: dict[str, int] = defaultdict(int)
    kp_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for m in mistakes:
        if m.get("subject"):
            seen.add(m["subject"])
        mis_by[m.get("subject") or ""] += 1
    for k in kps:
        if k.get("subject"):
            seen.add(k["subject"])
        kp_by[k.get("subject") or ""].append(k)
    for e in explains:
        if e.get("subject"):
            seen.add(e["subject"])
    cards_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for c in cards:
        cards_by[c.get("subject") or ""].append(c)
    today = _date.today().isoformat()
    names = sorted(seen)
    stats_out = []
    for s in names:
        subj_kps = kp_by.get(s, [])
        total = len(subj_kps)
        solid = mastered = 0
        for k in subj_kps:
            st = lib.compute_state(float(k.get("score") or 0.0))
            if st == "solid":
                solid += 1
            elif st == "mastered":
                mastered += 1
        subj_cards = cards_by.get(s, []) + cards_by.get("", [])   # rev.list_cards 口径：含未分类卡
        stats_out.append({
            "subject": s,
            "mistakes": mis_by.get(s, 0),
            "knowledge": total,
            "mastered_rate": round(100 * (solid + mastered) / total) if total else 0,
            "review_total": len(subj_cards),
            "review_due": sum(1 for c in subj_cards if (c.get("due") or "") <= today),
            "review_new": sum(1 for c in subj_cards if c.get("state") == "new"),
        })
    return {"subjects": names, "stats": stats_out}


@router.get("/api/library/explain/slices")
def explain_slices(subject: str = "", query: str = "", limit: int = 20) -> dict[str, Any]:
    """学习中心：某科目可用的教材切片（零 LLM，供讲解 grounding 确认与复习「查看提示」）。

    v0.7.2：query 非空 → title+text 关键词 top-k（≤5 条）；空 → 前 limit 条（默认 20，向后兼容）。
    """
    import re as _re
    expl.index_slices()
    idx = expl._load_index().get("subjects", {})
    limit = max(1, min(int(limit), 50))
    if subject:
        pool = idx.get(subject) or []
        total = len(pool)
    else:
        pool = [s for subs in idx.values() for s in subs]
        total = len(pool)
    if query:
        # IMP-06：FTS5+jieba 优先（SQL 模式）；不可用/无命中回退标题+正文关键词 top-k
        fts = expl.fts_search(subject, query, 5, pool)
        if fts is not None:
            pool = fts
        else:
            terms = [t for t in _re.split(r"[\s,，、；;]+", query.strip()) if len(t) >= 2][:6]
            scored = []
            for s in pool:
                hay = (str(s.get("title") or "") + " " + str(s.get("text") or "")).lower()
                hits = sum(1 for t in terms if t.lower() in hay)
                if hits:
                    scored.append((hits, s))
            scored.sort(key=lambda x: -x[0])
            pool = [s for _, s in scored][:5]
    clean = [{k: v for k, v in s.items() if k != "_norm"} for s in pool]
    if subject:
        return {"subject": subject, "count": total, "query": query, "slices": clean}
    counts = {k: len(v) for k, v in idx.items()}
    return {"subject": "", "count": total, "query": query, "subjects": counts, "slices": clean}


def _resolve_search_fn():
    """解析当前检索后端 → 可注入的 search_fn；配置缺失/手动模式/异常 → None（纯教材退化）。

    讲解与提问的「无原文回退」共用此解析（错误隔离：后端解析失败不阻断主流程）。
    """
    from ..core import websearch as ws

    try:
        c = cfg.load()
        backend = ws.resolve_backend((c.get("web_search") or {}).get("backend", "auto"),
                                     c.get("provider", ""),
                                     (c.get("web_search") or {}).get("api_key", ""))
        if backend == "manual":
            return None
        key = (c.get("web_search") or {}).get("api_key") or resolve_key(c.get("api_key", ""))
        model = c.get("model_gen", "")
        return ws.build_backend_fn(backend, key, model)
    except Exception:  # noqa: BLE001  后端解析失败 → 纯教材/纯模型知识
        return None


def _explain_guard(body: ExplainBody) -> Iterator[None]:
    """R3-21：同知识点「在飞」去重——连点/双标签重复生成 → 409，防双扣费。"""
    from ..core import dedupe

    key = f"explain:{body.subject}|{body.kp_name}|{body.kp_id}"
    if dedupe.begin(key):
        raise HTTPException(409, "该知识点的讲解正在生成，请稍候查看产物，勿重复提交")
    try:
        yield
    finally:
        dedupe.end(key)


@router.post("/api/library/explain")
def explain(body: ExplainBody, _guard: None = Depends(_explain_guard)) -> dict[str, Any]:
    """教材讲解：检索切片(+联网补充默认) → MedExplain 生成 → 存产物 + 回写掌握度。

    无原文回退（2026-08-29）：切片未命中 → 先说明 + 联网补充 + 模型知识输出，
    产物记录 grounded=False 供前端展示「无教材原文」说明。
    """
    from ..agents.medexplain import explain_knowledge

    subject, kp_name, related = _resolve_subject_kp(body)
    expl.index_slices()
    hits = expl.retrieve(subject=subject, query=f"{kp_name} {_query_extra(related)}")
    slices_text = expl.slice_text_of(hits)
    grounded = bool(slices_text.strip())

    # 联网补充（默认开启）：复用统一后端解析，mock 可注入用该 writer
    search_fn = _resolve_search_fn() if body.use_web else None
    web_materials: list[dict[str, Any]] = []

    client = _explain_client()
    result = explain_knowledge(client, subject, kp_name, slices_text,
                               related[0] if related else None,
                               web_materials=web_materials, search_fn=search_fn,
                               use_web=body.use_web)
    content = result["content"]
    if body.use_web and not content:
        # 网络后端可能失败 → 二次尝试纯教材（不因网络抖动丢讲解）
        fallback = explain_knowledge(client, subject, kp_name, slices_text,
                                     related[0] if related else None, use_web=False)
        content = fallback["content"]
        result = fallback

    rec = {
        "id": f"ex_{_new_milli()}",
        "subject": subject, "kp_name": kp_name,
        "kp_id": body.kp_id or "",
        "created_at": _now_iso(), "content": content,
        "sources": result.get("sources") or [],
        "via_web": bool(result.get("via_web")),
        "grounded": grounded,
        "web_materials": result.get("web_materials") or [],
        "related_mistake": (related[0].get("id") if related else "") or "",
        "slices_used": [h.get("sid") for h in hits],
    }
    expl.save_explain(rec)
    lib.log_knowledge_event(kp_name, "explain",
                            note=f"{subject} / via_web={rec['via_web']} / {len(rec['sources'])} sources")
    return {"explain": rec, "title": kp_name}


@router.get("/api/library/explains")
def explains_list(subject: str = "") -> dict[str, Any]:
    recs = expl.list_explains(subject)
    return {"explains": recs, "total": len(recs)}


@router.get("/api/library/explains/{eid}")
def explains_get(eid: str) -> dict[str, Any]:
    rec = expl.get_explain(eid)
    if rec is None:
        raise HTTPException(404, "讲解产物不存在")
    return {"explain": rec}


@router.delete("/api/library/explains/{eid}")
def explains_delete(eid: str) -> dict[str, Any]:
    if not expl.delete_explain(eid):
        raise HTTPException(404, "讲解产物不存在")
    # C6：讲解删除 → 级联清理其派生的医学记忆卡（source=讲解 id），避免旧卡残留
    removed = cardlib.delete_by_source(eid)
    return {"ok": True, "cards_removed": removed}


@router.post("/api/library/explains/export")
def explains_export(subject: str = "") -> dict[str, Any]:
    md = expl.export_subject_md(subject)
    if not md:
        raise HTTPException(404, "该科目还没有讲解产物")
    return {"ok": True, "markdown": md, "subject": subject}


# ---------------------------------------------------------------- M4 提问式学习（MedTutor）
class TutorStartBody(BaseModel):
    subject: str = ""
    kp_name: str = ""
    kp_id: str = ""
    mistake_id: str = ""


class TutorAnswerBody(BaseModel):
    session_id: str = ""
    user_answer: str = ""


def _tutor_client():
    from ..agents import get_client as _gc
    return _gc("gen")


def _tutor_grounding(subject: str, kp_name: str) -> tuple[str, bool, list[dict[str, Any]]]:
    """提问素材：教材切片检索；无命中 → 联网补充（≤1 检索，错误隔离）返回 (slices_text, grounded, web_materials)。

    无原文回退（2026-08-29）：grounded=False 时检索词与素材进 medtutor 注入，
    前端收到说明文案（未命中教材原文，问题基于网络素材与模型知识）。
    """
    from ..agents.medexplain import _build_web_query, _search_web

    expl.index_slices()
    hits = expl.retrieve(subject=subject, query=kp_name)
    slices_text = expl.slice_text_of(hits)
    grounded = bool(slices_text.strip())
    web_materials: list[dict[str, Any]] = []
    if not grounded:
        search_fn = _resolve_search_fn()
        if search_fn is not None:
            web_materials = _search_web(_build_web_query(subject, kp_name, None), search_fn)
    return slices_text, grounded, web_materials


def _tutor_start_guard(body: TutorStartBody) -> Iterator[None]:
    """R3-21：同知识点同契入「在飞」去重（防连点双开会话双扣费）。"""
    from ..core import dedupe

    key = f"tutor-start:{body.subject}|{body.kp_name}|{body.kp_id or body.mistake_id}"
    if dedupe.begin(key):
        raise HTTPException(409, "该知识点的提问会话正在创建，请稍候或直接进入会话，勿重复提交")
    try:
        yield
    finally:
        dedupe.end(key)


@router.post("/api/library/tutor/start")
def tutor_start(body: TutorStartBody, _guard: None = Depends(_tutor_start_guard)) -> dict[str, Any]:
    """开一个 Socratic 会话：锁定知识点+教材切片 → LLM 出第一问 → 存会话。"""
    if not body.kp_name.strip() and not body.kp_id and not body.mistake_id:
        raise HTTPException(400, "请指定待学习的知识点")
    subject, kp_name, _ = _resolve_subject_kp(body)
    client = _tutor_client()
    from ..agents import medtutor as mt
    slices_text, grounded, web_materials = _tutor_grounding(subject, kp_name)
    session = tut.start_session(subject, kp_name, body.kp_id or "")
    qtype = session["current"]["type"]
    try:
        question = mt.start_applying(client, subject, kp_name, session["state"],
                                     qtype, slices_text, web_materials)
    except Exception as e:  # noqa: BLE001
        # D-04：第一问生成失败 → 回滚会话（不留「无问题空会话」伪装已掌握）
        tut.delete_session(session["id"])
        raise HTTPException(502, f"第一问生成失败，请稍后重试（{type(e).__name__}）") from e
    if not (question or "").strip():
        tut.delete_session(session["id"])
        raise HTTPException(502, "第一问生成失败（模型返回为空），请稍后重试")
    tut.seed_first(session["id"], qtype, question)
    lib.log_knowledge_event(kp_name, "tutor",
                            note=f"{subject} / start / {qtype} / grounded={grounded}")
    return {"session": session, "question": question, "type": qtype,
            "state": session["state"], "subject": subject, "kp_name": kp_name,
            "grounded": grounded,
            "note": "" if grounded else "本知识点未在本地教材中检索到原文——问题由网络素材与模型知识生成（未经教材核实）。"}


def _tutor_answer_guard(body: TutorAnswerBody) -> Iterator[None]:
    """R3-21：同一作答「在飞」去重（连点提交 → 409，防重复判分扣费）。"""
    from ..core import dedupe

    key = f"tutor-answer:{body.session_id}|{body.user_answer[:80]}"
    if dedupe.begin(key):
        raise HTTPException(409, "该作答正在判分，请勿重复提交（稍候刷新查看结果）")
    try:
        yield
    finally:
        dedupe.end(key)


@router.post("/api/library/tutor/answer")
def tutor_answer(body: TutorAnswerBody, _guard: None = Depends(_tutor_answer_guard)) -> dict[str, Any]:
    """提交作答：LLM 判分+出下一问 → 本地推进状态机 → 回写掌握度。"""
    if not body.session_id.strip():
        raise HTTPException(400, "缺少会话 ID")
    session = tut.get_session(body.session_id)
    if session is None:
        raise HTTPException(404, "提问会话不存在")
    cur = session.get("current") or {"type": "explain", "text": ""}
    if not cur.get("text"):
        raise HTTPException(400, "该会话当前没有待回答的问题")
    subject, kp_name = session.get("subject") or "", session.get("kp_name") or ""
    client = _tutor_client()
    from ..agents import medtutor as mt
    slices_text, grounded, web_materials = _tutor_grounding(subject, kp_name)
    result = mt.score_answer(client, subject, kp_name, session.get("state", "weak"),
                             cur.get("type", "explain"), cur.get("text", ""),
                             body.user_answer, slices_text,
                             history=session.get("rounds") or [],
                             web_materials=web_materials)
    score = result["score"]
    gap = result["gap"]
    if score < 0:
        # 无法定量判定（LLM 判分失败、兜底也判不了）→ 不计分、不记轮次、不改掌握度，
        # 保持当前问题请学生重答；避免把「套话/对着模板凑字数」刷成正式作答。
        cur = session.get("current") or {"type": "explain", "text": ""}
        return {
            "session": session, "score": -1, "gap": gap or "回答未能可靠评分，请围绕考点再试一次。",
            "next_question": cur, "state": session.get("state", "weak"), "retry": True,
            "grounded": grounded,
        }
    updated = tut.record_answer(session["id"], body.user_answer, score, gap,
                                result["next_question"])
    lib.record_quiz(kp_name, score)
    return {
        "session": updated, "score": score, "gap": gap,
        "next_question": updated["current"], "state": updated["state"],
        "grounded": grounded,
    }


@router.get("/api/library/tutor/sessions")
def tutor_sessions(subject: str = "") -> dict[str, Any]:
    recs = tut.list_sessions(subject)
    return {"sessions": recs, "total": len(recs)}


class TutorCleanupBody(BaseModel):
    days: int = 30


@router.post("/api/library/tutor/cleanup")
def tutor_cleanup(body: TutorCleanupBody) -> dict[str, Any]:
    """C18：清理 days 天无活动的提问会话（防会话列表无限增长；不可恢复）。"""
    removed = tut.cleanup_stale(max(1, min(int(body.days), 365)))
    return {"ok": True, "removed": removed}


@router.get("/api/library/tutor/{sid}")
def tutor_session(sid: str) -> dict[str, Any]:
    s = tut.get_session(sid)
    if s is None:
        raise HTTPException(404, "提问会话不存在")
    return {"session": s}


@router.delete("/api/library/tutor/{sid}")
def tutor_session_delete(sid: str) -> dict[str, Any]:
    if not tut.delete_session(sid):
        raise HTTPException(404, "提问会话不存在")
    return {"ok": True}


# ---------------------------------------------------------------- M5 复习调度（轻量 SM-2）
class ReviewQueueBody(BaseModel):
    subject: str = ""
    kp_name: str = ""
    kp_id: str = ""


class ReviewGradeBody(BaseModel):
    card_id: str = ""
    quality: int = 4


@router.post("/api/library/review/queue")
def review_queue(body: ReviewQueueBody) -> dict[str, Any]:
    """把知识点卡片入队（纯本地，不调 LLM）。已存在则返回既有卡。"""
    if not body.kp_name.strip() and not body.kp_id:
        raise HTTPException(400, "请指定要入队的知识点")
    card = rev.enqueue(body.kp_name.strip(), body.subject, body.kp_id or "")
    return {"ok": True, "card": card}


@router.post("/api/library/review/queue-all")
def review_queue_all(subject: str = "") -> dict[str, Any]:
    """把「未到 solid」的知识点批量入队（复习计划铺卡）。"""
    added = rev.enqueue_knowledge(subject)
    return {"ok": True, "added": len(added), "cards": added}


@router.get("/api/library/review/today")
def review_today(subject: str = "") -> dict[str, Any]:
    cards = rev.today_cards(subject)
    return {"cards": cards, "total": len(cards), "stats": rev.stats(subject)}


@router.get("/api/library/review/cards")
def review_cards(subject: str = "") -> dict[str, Any]:
    cards = rev.list_cards(subject)
    return {"cards": cards, "total": len(cards), "stats": rev.stats(subject)}


@router.post("/api/library/review/grade")
def review_grade(body: ReviewGradeBody) -> dict[str, Any]:
    """回答后按 quality(0~5) 走 SM-2 更新 interval/ease/state，并回写知识点。"""
    if not body.card_id.strip():
        raise HTTPException(400, "缺少卡片 ID")
    card = rev.grade(body.card_id.strip(), body.quality)
    if card is None:
        raise HTTPException(404, "复习卡片不存在")
    # 复习质量回流掌握度：把这次 SM-2 判分写回知识点（quality≥3 记成功提取）
    lib.record_review(card.get("kp_name") or "", body.quality)
    return {"ok": True, "card": card}


@router.delete("/api/library/review/{cid}")
def review_card(cid: str) -> dict[str, Any]:
    if not rev.delete_card(cid):
        raise HTTPException(404, "复习卡片不存在")
    return {"ok": True}


# ---------------------------------------------------------------- 医学记忆卡（WP-05/NX-04）
class CardsGenerateBody(BaseModel):
    explain_id: str = ""


class CardsGradeBody(BaseModel):
    quality: int = 3


@router.post("/api/library/cards/generate")
def cards_generate(body: CardsGenerateBody) -> dict[str, Any]:
    """讲解产物 → 医学记忆卡（flag(cards) 门禁；CardDrafts 契约抽取 + 幂等入库）。

    每张卡创建时绑定调度算法（FSRS 默认）；LLM 契约失败走 502（global LLMError handler）。
    """
    from ._common import require_flag

    require_flag("cards")
    eid = (body.explain_id or "").strip()
    if not eid:
        raise HTTPException(400, "缺少讲解产物 ID")
    rec = expl.get_explain(eid)
    if not rec:
        raise HTTPException(404, "讲解产物不存在")
    drafts = medcards.generate_cards(medcards.make_client(), rec)   # LLMError → 502
    if not drafts:
        raise HTTPException(502, "未能从该讲解生成记忆卡（建议重试或选择更聚焦的知识点）")
    added = cardlib.create_from_drafts(drafts, rec.get("subject", ""),
                                       rec.get("kp_name", ""), eid)
    return {"ok": True, "added": len(added), "cards": added,
            "total": len(cardlib.list_cards())}


@router.get("/api/library/cards")
def cards_list(subject: str = "", due: int = 0) -> dict[str, Any]:
    """记忆卡列表（due=1 仅今日到期）；stats 含 total/new/due/review/relearning。"""
    cs = cardlib.list_cards(subject, due_only=bool(due))
    return {"cards": cs, "total": len(cs), "stats": cardlib.stats(subject)}


@router.post("/api/library/cards/{cid}/grade")
def cards_grade(cid: str, body: CardsGradeBody) -> dict[str, Any]:
    """自评 quality(0~5) → 按卡片绑定算法（FSRS/SM-2）推进下次排程。"""
    quality = max(0, min(int(body.quality), 5))
    card = cardlib.grade_card(cid, quality)
    if card is None:
        raise HTTPException(404, "记忆卡不存在")
    # D-09：记忆卡评分回写掌握度（与复习卡同口径）——否则天天「记住」概览掌握率纹丝不动
    lib.record_review(card.get("kp_name") or "", quality)
    return {"ok": True, "card": card}


@router.delete("/api/library/cards/{cid}")
def cards_delete(cid: str) -> dict[str, Any]:
    if not cardlib.delete_card(cid):
        raise HTTPException(404, "记忆卡不存在")
    return {"ok": True}


# ---------------------------------------------------------------- 医学记忆卡导出（D15）
@router.get("/api/library/cards/export/txt")
def cards_export_txt(subject: str = "") -> Any:
    """记忆卡 → Anki 文本（正面/背面 Tab 分隔 + 类型/知识点标签列）。"""
    cards = cardlib.list_cards(subject)
    if not cards:
        raise HTTPException(404, "暂无记忆卡可导出（先在「讲解与学习产物」生成记忆卡）")
    from ..render.apkg import CARD_KIND_LABELS, _esc_anki

    lines = ["#separator:tab", "#html:true", ""]
    for c in sorted(cards, key=lambda x: (str(x.get("kind", "")), str(x.get("front", "")))):
        row = [_esc_anki(c.get("front") or ""), _esc_anki(c.get("back") or ""),
               _esc_anki(CARD_KIND_LABELS.get(c.get("kind"), c.get("kind") or "concept")),
               _esc_anki(c.get("kp_name") or c.get("subject") or "")]
        lines.append("\t".join(row))
    from ..core.fsutil import safe_filename
    return {"ok": True, "filename": f"MedKit记忆卡_{safe_filename(subject or '全部')}.txt",
            "content": "\n".join(lines) + "\n"}


@router.get("/api/library/cards/export/apkg")
def cards_export_apkg(subject: str = "") -> FileResponse:
    """记忆卡 → Anki 真包（独立「MedKit 记忆卡」牌组；稳定 id 防重复导入）。"""
    cards = cardlib.list_cards(subject)
    if not cards:
        raise HTTPException(404, "暂无记忆卡可导出（先在「讲解与学习产物」生成记忆卡）")
    from ..render.apkg import export_memory_apkg

    out_dir = cfg.CONFIG_DIR / "exports"
    out_dir.mkdir(parents=True, exist_ok=True)
    from ..core.fsutil import safe_filename
    out = out_dir / f"MedKit记忆卡_{safe_filename(subject or '全部')}.apkg"
    export_memory_apkg(cards, subject or "未分类", subject or "全部", out)
    return FileResponse(out, media_type="application/octet-stream", filename=out.name)


def _new_milli() -> int:
    import time
    return int(time.time() * 1000) % 100000000


def _now_iso() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def _query_extra(related: list[dict[str, Any]]) -> str:
    if not related:
        return ""
    m = related[0]
    parts = [x for x in (m.get("chapter"), m.get("topic")) if x]
    return " ".join(parts)
