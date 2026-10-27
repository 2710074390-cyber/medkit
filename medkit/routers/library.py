"""routers：个人学习库（M1/M2）——错题沉淀 + 掌握度诊断。

命名空间 /api/library/*，挂载进 main.py。全部数据在本机；不调 LLM（导入结构化走本地规则，
OCR 复用 MinerU；掌握度/优先级纯本地）。错题结构从押题卷（source=paper）和手动/文本/图片导入。
"""

import asyncio
import json
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from ..core import bank as banklib
from ..core import config as cfg
from ..core import dashboard as dash
from ..core import errors as _errs
from ..core import library as lib
from ..core.config import resolve_key
from . import _common
from ._common import read_upload_capped

router = APIRouter()


def _sse(event: str, data: Any) -> str:
    """WP-8：SSE 帧序列化（event + data JSON）。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_error_msg(e: Exception) -> str:
    """C-06：流式 error 帧统一中文归因——不直出端点/响应片段（与 llm_test 同口径）。"""
    from ..core.llm import LLMClient

    return LLMClient._test_error_hint(e) if isinstance(e, Exception) else str(e)


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


class SubjectDeleteBody(BaseModel):
    subject: str = ""


class MistakeBatchBody(BaseModel):
    ids: list[str] = []
    learned: bool = True
    format: str = "json"


class SiteImportBody(BaseModel):
    items: list[dict[str, Any]] = []
    meta: dict[str, Any] = {}


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
    # RV6（2026-09-17 审查）：与 batch-delete 的 _valid_ids 同口径限 500——
    # 无上限的超大列表会长时间占住线程池 worker 并批量写库
    if len(body) > 500:
        raise HTTPException(400, "单次最多导入 500 条（请分批提交）")
    added = lib.batch_add([b.model_dump() for b in body])
    return {"ok": True, "added": added}


@router.post("/api/library/mistakes/import-file")
async def import_file(file: UploadFile = File(...)) -> dict[str, Any]:
    """批量导入文件（本地解析，零 LLM）：.json / .csv / .md / .txt，按扩展名分派。"""
    name = file.filename or ""
    raw = await read_upload_capped(file, _common.MAX_FILE_SIZE, "文件过大（限 200MB）")
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
    # U-05：文本解析 + 批量入库为秒级同步操作，放线程池避免冻结事件循环
    rows = await asyncio.to_thread(lib.parse_import_text, text, ext)
    if not rows:
        raise HTTPException(400, "未解析出题目——请检查文件格式（JSON 数组 / CSV 带表头 / 每题带题号与答案）")
    added = await asyncio.to_thread(lib.batch_add, rows)
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


class RetryBody(BaseModel):
    user_answer: str = ""


@router.post("/api/library/mistakes/{mid}/retry")
def retry_mistake(mid: str, body: RetryBody) -> dict[str, Any]:
    """错题重练（F7 增强）：判一道自己的错题 → 判对归档「已掌握」，判错保持。

    判分口径与题库练习 / 押题卷**同源**（`core.bank.answers_equal`，集合比较）；
    返回答案与解析 ⇒ 本入口同时充当「查看解析」。
    """
    res = lib.retry_mistake(mid, body.user_answer)
    if res is None:
        raise HTTPException(404, "错题不存在")
    # 记一条作答流水（`bank_id` 空 = 非题库来源，`mode="retry"`）。
    # 为什么：每日目标的「今日作答数」取自 `bank_attempts`——错题重练也是练习，
    # 不记的话用户设了目标、重练一整晚，进度条还是 0（自相矛盾的体验）。
    # 跨域写放在 **routers 层编排**（core 之间不互相写对方的表）。
    banklib.record_attempt("", mid, user_answer=body.user_answer,
                           correct=bool(res.get("correct")), mode="retry")
    return {"ok": True, **res}


@router.delete("/api/library/mistakes/{mid}")
def drop_mistake(mid: str) -> dict[str, Any]:
    if not lib.delete_mistake(mid):
        raise HTTPException(404, "错题不存在")
    return {"ok": True}


def _valid_ids(ids: list[str]) -> list[str]:
    out = [str(x).strip() for x in (ids or []) if str(x).strip()]
    if not out:
        raise HTTPException(400, "请先选择错题")
    if len(out) > 500:
        raise HTTPException(400, "单次最多操作 500 条")
    return out


@router.post("/api/library/mistakes/batch-delete")
def batch_delete_mistakes(body: MistakeBatchBody) -> dict[str, Any]:
    """WP-5：批量删除错题（删除前自动导出备份 JSON 到 ~/.medkit/exports/）。"""
    ids = _valid_ids(body.ids)
    res = lib.batch_delete_with_backup(ids)
    return {"ok": True, **res}


@router.post("/api/library/mistakes/batch-learn")
def batch_learn_mistakes(body: MistakeBatchBody) -> dict[str, Any]:
    """WP-5：批量标记已掌握（仅归档标记，不改写掌握度计数）。"""
    ids = _valid_ids(body.ids)
    updated = lib.batch_mark_learned(ids, bool(body.learned))
    return {"ok": True, "updated": updated}


@router.post("/api/library/mistakes/batch-export")
def batch_export_mistakes(body: MistakeBatchBody) -> dict[str, Any]:
    """WP-5：批量导出所选错题（json 原样 / md 可读排版）。"""
    ids = _valid_ids(body.ids)
    fmt = (body.format or "json").lower()
    if fmt not in ("json", "md"):
        raise HTTPException(400, "导出格式仅支持 json / md")
    return {"ok": True, **lib.export_mistakes(ids, fmt)}


@router.post("/api/library/mistakes/import-export")
def import_export_mistakes(body: SiteImportBody) -> dict[str, Any]:
    """WP-11：外部站点做题数据导入（question+subject+chapter 幂等去重）。"""
    items = body.items or []
    if not items:
        raise HTTPException(400, "无导入数据（items 为空）")
    stats = lib.import_site_items(items)
    return {"ok": True, **stats}


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
    # R4-13/B-02：图片独立 20MB（OCR 读图）。2026-10-22：上限改在**读取阶段**生效
    # （原实现先整块读进内存再判，超限文件已经吃满内存）
    data = await read_upload_capped(
        file, _common.MAX_IMAGE_BYTES, "图片超过 20 MB，请压缩或裁剪后重试")
    if not data.strip():
        raise HTTPException(400, "文件为空（0 字节或仅空白）——请确认上传了照片/截图")
    if suffix not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
        raise HTTPException(400, f"不支持的类型 {suffix}（仅图片）")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    try:
        tmp.write(data)
        tmp.flush()
        # 2026-10-22（P0）：**必须在 unlink 之前关掉句柄**。
        # Windows 上「仍打开的临时文件」无法删除：`Path.unlink()` 抛
        # `PermissionError [WinError 32]`，而它在 `finally` 里 —— 会把成功返回的
        # OCR 文本变成 500，并永久留下临时文件。原先只在「体积超限」分支（尚未来得及
        # 建临时文件）被测试覆盖，成功路径从未跑到这里。实测复现见
        # `tests/test_backend_audit_fixes.py::test_import_image_returns_text_and_cleans_up`。
        tmp.close()
        client = mineru_mod.MinerUClient(api_key)
        # extract 内含上传+轮询，可耗时数分钟，须放线程池以免阻塞事件循环冻结全站
        markdown = await asyncio.to_thread(client.extract, tmp_path)
    except MinerUError as e:
        raise HTTPException(502, f"OCR 识别失败：{e}")
    finally:
        try:
            tmp.close()          # 幂等；write 之前就抛异常时也保证关闭
        except OSError:
            pass
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except OSError as e:
            # 删不掉不阻断返回（临时文件残留可接受），但必须留痕而不是让整体 500
            _errs.record("library.import_image.cleanup", "临时文件未能清理", e=e)
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
@router.get("/api/library/subjects")
def subjects() -> dict[str, Any]:
    """已覆盖科目清单（错题 + 知识点并集），附每科统计（刷题页科目卡片）。

    v0.8.1：新增 `stats`（每科错题数/知识点数/掌握率）——全部本地计算，零 LLM、零新表。
    R3-19：一次扫描按 subject 聚合（消除逐科目 get_mastery_view 的 N+1），口径不变。
    2026-10-15（奥卡姆精简 T2）：讲解产物与复习卡两路聚合随其域删除而移除
    （`explains` 0 条 / `cards` 0 张，实测零使用）。
    """
    from collections import defaultdict

    # U-22：脏数据（???/编码损坏）不进科目下拉与每科统计，避免虚无的「??? 科」
    mistakes = [m for m in lib.list_mistakes() if not lib.is_mistake_dirty(m)]
    kps = [k for k in lib.list_knowledge() if not lib.is_kp_dirty(k)]
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
        stats_out.append({
            "subject": s,
            "mistakes": mis_by.get(s, 0),
            "knowledge": total,
            "mastered_rate": round(100 * (solid + mastered) / total) if total else 0,
        })
    return {"subjects": names, "stats": stats_out}


@router.post("/api/library/subjects/delete")
def subject_delete(body: SubjectDeleteBody) -> dict[str, Any]:
    """WP-4：删除科目及其关联本地数据（先自动导出备份到 ~/.medkit/exports/）。"""
    subject = (body.subject or "").strip()
    if not subject:
        raise HTTPException(400, "科目不能为空")
    try:
        res = lib.delete_subject_with_backup(subject)
    except ValueError as e:  # noqa: BLE001  科目名校验失败
        raise HTTPException(400, str(e)) from e
    return {"ok": True, **res}

