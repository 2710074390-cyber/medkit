"""routers：MinerU OCR（任务制：start / 轮询 / 取消 / 测试）。"""

import asyncio
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from ..core import config as cfg
from ..core import errors as _errs
from ..core import mineru as mineru_mod
from ..core.config import resolve_key
from ..core.mineru import MinerUError
from ..state import OCR_JOB_DIR, OCR_JOBS, OCR_LOCK, OCR_SEM
from ._common import (
    IMAGE_SUFFIXES,
    MAX_FILE_SIZE,
    MAX_IMAGE_BYTES,
    TEXT_SUFFIXES,
    _mineru_to_result,
)

router = APIRouter()


class MineruTestBody(BaseModel):
    api_key: str = ""


@router.post("/api/mineru/test")
def mineru_test(body: MineruTestBody) -> dict[str, Any]:
    key = body.api_key or resolve_key((cfg.load().get("mineru", {}) or {}).get("api_key", ""))
    client = mineru_mod.MinerUClient(key)
    ok, msg = client.test()
    return {"ok": ok, "msg": msg, "mode": client.mode()}


def _jobs_file() -> Path:
    return OCR_JOB_DIR / "jobs.json"


def _save_jobs_to_disk() -> None:
    """B34：任务记录持久化到磁盘（OCR_JOB_DIR/jobs.json），重启后仍在。
    调用方需已持有 OCR_LOCK（或保证无并发写）；cancel Event 不可序列化，落盘时剔除。"""
    try:
        OCR_JOB_DIR.mkdir(parents=True, exist_ok=True)
        snapshot = {k: {kk: vv for kk, vv in j.items() if kk not in ("cancel", "_thread")}
                    for k, j in OCR_JOBS.items()}
        tmp = _jobs_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_jobs_file())
    except Exception as e:  # noqa: BLE001  落盘失败不阻断 OCR 主流程（下次启动仅丢失本次记录）
        _errs.record("ocr._save_jobs_to_disk", "静默容错（U-15 留痕）", e=e)


def _restore_jobs_from_disk() -> None:
    """B34：启动时把 jobs.json 中的任务记录恢复进内存（重建 cancel Event）。"""
    try:
        if not _jobs_file().exists():
            return
        data = json.loads(_jobs_file().read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  损坏的 jobs.json 忽略（不阻断启动）
        data = {}
    with OCR_LOCK:
        for jid, j in data.items():
            if not isinstance(j, dict):
                continue
            j["cancel"] = threading.Event()
            OCR_JOBS[jid] = j


def _cleanup_orphan_tmp() -> None:
    """B34：启动时清理无任务记录的孤儿 tmp 文件（重启后仍在但无记录的残留上传）。"""
    try:
        if not OCR_JOB_DIR.exists():
            return
        known = {j.get("id") for j in OCR_JOBS.values()}
        for p in OCR_JOB_DIR.iterdir():
            if p.name in ("jobs.json",) or p.name.endswith(".json.tmp"):
                continue
            if p.is_file() and p.stem not in known:
                p.unlink(missing_ok=True)
    except Exception as e:  # noqa: BLE001  清理失败不阻断启动
        _errs.record("ocr._cleanup_orphan_tmp", "静默容错（U-15 留痕）", e=e)


def restore_ocr_persistence() -> None:
    """B34：启动入口——恢复任务记录 + 清理孤儿 tmp（main.py lifespan 调用）。"""
    _restore_jobs_from_disk()
    _cleanup_orphan_tmp()


def _ocr_job_set(job_id: str, state: str | None = None, msg: str | None = None) -> None:
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
        if job:
            if state:
                job["state"] = state
            if msg is not None:
                job["msg"] = msg
        _save_jobs_to_disk()   # B34：状态变更即持久化（重启后任务仍在）


def _ocr_job_cleanup() -> None:
    """惰性清理 24h 前的旧任务记录。"""
    now = time.time()
    with OCR_LOCK:
        for jid in [k for k, j in OCR_JOBS.items() if now - j["created"] > 86400]:
            OCR_JOBS.pop(jid, None)


def _run_ocr_job(job: dict[str, Any], tmp_path: str, name: str, suffix: str) -> None:
    """后台线程：跑 MinerU → 结果写入 job（带状态回调 + 取消）。"""
    jid = job["id"]
    try:
        with OCR_SEM:
            if job["cancel"].is_set():
                raise MinerUError("任务已取消")
            client = mineru_mod.MinerUClient(resolve_key(
                (cfg.load().get("mineru", {}) or {}).get("api_key", "")))
            _ocr_job_set(jid, "running", "已提交，等待 MinerU 调度…")
            markdown = client.extract(
                tmp_path,
                progress=lambda label: _ocr_job_set(jid, "running", label),
                cancel=job["cancel"])
            if job["cancel"].is_set():  # F1（v0.5）：取消竞态 — 完成后不得覆写 cancelled 终态
                _ocr_job_set(jid, "cancelled", "已取消（识别中止，未采用结果）")
                return
            via = "mineru-v4" if client.mode() == "v4" else "mineru-agent"
            job["result"] = _mineru_to_result(name, markdown, via)
            _ocr_job_set(jid, "done", "识别完成，已自动加入输入")
    except MinerUError as e:
        if job["cancel"].is_set():
            _ocr_job_set(jid, "cancelled", "已取消")
        else:
            _ocr_job_set(jid, "failed", f"识别失败：{e}")
    except Exception as e:  # noqa: BLE001
        _ocr_job_set(jid, "failed", f"识别异常：{e}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)
        job.pop("_thread", None)   # 线程终态后移除引用（shutdown 只 join 在飞线程）


@router.post("/api/ocr/start")
async def ocr_start(file: UploadFile = File(...),
                    role: str = Form("textbook")) -> dict[str, Any]:
    _ocr_job_cleanup()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        raise HTTPException(400, f"不支持的类型 {suffix}（PDF/DOCX/MD/TXT/图片）")
    data = await file.read()
    if not data.strip():
        raise HTTPException(400, "文件为空（0 字节或仅空白）——请上传有内容的文档")
    # B-02：图片独立上限（20MB）——OCR 读图场景与 PDF/DOCX 的 200MB 分开
    limit = MAX_IMAGE_BYTES if suffix in IMAGE_SUFFIXES else MAX_FILE_SIZE
    if len(data) > limit:
        mb = limit // (1024 * 1024)
        raise HTTPException(400, f"{'图片' if suffix in IMAGE_SUFFIXES else '文件'}超过 {mb} MB"
                                 f"{'，请压缩或裁剪后重试' if suffix in IMAGE_SUFFIXES else '，请按章节拆分后重试'}")

    OCR_JOB_DIR.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:12]
    tmp_path = str(OCR_JOB_DIR / f"{jid}{suffix}")
    await asyncio.to_thread(Path(tmp_path).write_bytes, data)  # v0.5：≥200MB 写盘移出事件循环

    job: dict[str, Any] = {"id": jid, "name": file.filename, "role": role, "state": "queued",
                           "msg": "排队中…", "result": None, "created": time.time(),
                           "cancel": threading.Event()}
    with OCR_LOCK:
        OCR_JOBS[jid] = job
        _save_jobs_to_disk()   # B34：新任务即落盘（重启后仍在）
    t = threading.Thread(target=_run_ocr_job, args=(job, tmp_path, file.filename, suffix),
                         daemon=True, name=f"medkit-ocr-{jid}")
    job["_thread"] = t
    t.start()
    return {"job_id": jid, "state": "queued"}


@router.get("/api/ocr/jobs/{job_id}")
def ocr_job(job_id: str) -> dict[str, Any]:
    _ocr_job_cleanup()
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    return {"job_id": job_id, "state": job["state"], "msg": job["msg"],
            "result": job["result"]}


@router.delete("/api/ocr/jobs/{job_id}")
def ocr_cancel(job_id: str) -> dict[str, Any]:
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    job["cancel"].set()
    _ocr_job_set(job_id, "cancelled", "已取消")
    return {"job_id": job_id, "state": "cancelled"}
