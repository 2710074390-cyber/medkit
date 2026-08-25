"""FastAPI 入口：127.0.0.1 回环服务 + 静态前端。

设计要点（2026-08 用户评审后重构）：
- /api/parse 纯本地文本层解析，全程 asyncio.to_thread，不阻塞事件循环
- OCR 改任务制：/api/ocr/start → 轮询 /api/ocr/jobs/{id} → DELETE 取消（进度+取消+不阻塞）
- 配置保存「空 Key = 保留原值」，杜绝静默清除
- 2026-08 全面审查后加固（v0.3.0）：
  S1 Host/Origin 校验中间件（防 CSRF 烧钱 + DNS rebinding 窃产物）
  S3 pid 路径消毒；A5 损坏 meta.json 容错（422）+ 原子写
  U1 管线取消（DELETE /run）；DPAPI 密钥解密接入（S2）；Anki 导出（U8）
"""

import asyncio
import hashlib
import json
import os
import random
import re
import shutil
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .core import config as cfg
from .core import extract as ex
from .core import presets as prs
from .core import websearch as ws
from .core.config import resolve_key
from .core.llm import LLMClient
from .core.mineru import MinerUClient, MinerUError
from .core.providers import PROVIDERS, get_provider
from .core.quota import allocate
from .core.slice import slice_text
from .gates import options_check, trace_check

APP_VERSION = "0.4.0"
app = FastAPI(title="MedKit · 医学题库工坊", version=APP_VERSION)

WEB_DIR = Path(__file__).parent / "web"


# ---------------------------------------------------------------- 本地服务边界（S1）
def _local_port() -> int:
    """实际监听端口（S5：run_medkit.py 端口探测后经 MEDKIT_PORT 传入）。"""
    try:
        return int(os.environ.get("MEDKIT_PORT", "4880"))
    except ValueError:
        return 4880


def _allowed_origins() -> set[str]:
    p = _local_port()
    return {f"http://127.0.0.1:{p}", f"http://localhost:{p}",
            f"http://[::1]:{p}",  # v0.5：IPv6 回环 origin 白名单
            f"http://127.0.0.1:{p}/", f"http://localhost:{p}/",
            f"http://[::1]:{p}/"}


@app.middleware("http")
async def _guard_local(request: Request, call_next):
    """仅接受本机 Host/Origin：封死 DNS rebinding 与跨站简单请求（CSRF 烧钱）。"""
    host = (request.headers.get("host") or "").lower().strip()
    # v0.5：兼容 IPv6 回环 [::1]:4880（旧实现 split(':')[0] 得 '['，永远 403）
    if host.startswith("["):
        hostname = host[1:host.find("]")] if "]" in host else ""
    else:
        hostname = host.split(":")[0]
    if hostname not in ("127.0.0.1", "localhost", "::1"):
        return JSONResponse({"detail": "forbidden host"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        org = (request.headers.get("origin") or "").rstrip("/")
        if org and org not in _allowed_origins():
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    return await call_next(request)


def _safe_pid(pid: str) -> str:
    """S3：pid 只允许单段安全字符，禁止 .. / \\ 等路径逃逸。"""
    if pid in {"", ".", ".."} or "/" in pid or "\\" in pid:
        raise HTTPException(400, "非法项目 ID")
    if not re.fullmatch(r"[\w\u4e00-\u9fff-]+", pid):
        raise HTTPException(400, "非法项目 ID")
    return pid


def _read_meta_checked(base: Path) -> dict[str, Any]:
    """A5：meta.json 损坏 → 422（提示可删除重建），不再 500。"""
    p = base / "meta.json"
    if not p.exists():
        raise HTTPException(404, "项目不存在")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        raise HTTPException(
            422, "项目元数据损坏（可能因中途断电写坏）；可删除该项目后重新生成")


def _write_meta_atomic(base: Path, meta: dict[str, Any]) -> None:
    """v0.5：原子写统一走 fsutil（唯一 tmp 名 + 重试；旧实现固定 meta.json.tmp 无重试）。"""
    from .core.fsutil import write_json_atomic

    write_json_atomic(base / "meta.json", meta)


# ---------------------------------------------------------------- 基础
@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": APP_VERSION, "stage": "websearch+playability"}


@app.get("/api/providers")
def providers() -> dict[str, Any]:
    return {"providers": PROVIDERS}


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return cfg.public_view(cfg.load())


class ConfigBody(BaseModel):
    provider: str
    base_url: str
    api_key: str = ""
    model_gen: str = ""
    model_qc: str = ""
    web_search_enabled: bool = False
    web_search_api_key: str = ""
    web_search_backend: str = "auto"
    mineru_api_key: str = ""
    mineru_auto_ocr: bool = True


@app.put("/api/config")
def put_config(body: ConfigBody) -> dict[str, Any]:
    """保存配置。约定：api_key / mineru_api_key 传空串 = 保留已保存值（防静默清除）。
    S2：新键入 Key 一律 DPAPI 加密落盘；旧明文在保存时自动升级为密文。
    """
    prov = get_provider(body.provider)
    if prov is None:
        raise HTTPException(400, "未知服务商: " + body.provider)
    saved = cfg.load()
    base_url = body.base_url or prov.get("base_url", saved.get("base_url", ""))
    model_gen = body.model_gen or prov.get("default_model", saved.get("model_gen", ""))
    model_qc = body.model_qc or model_gen or saved.get("model_qc", "")
    if not model_gen:
        raise HTTPException(400, "请填写生成模型（如 deepseek-v4-flash）")

    api_key = body.api_key or saved.get("api_key", "")  # 空 = 保留
    mineru_api_key = body.mineru_api_key or (saved.get("mineru", {}) or {}).get("api_key", "")
    ws_api_key = body.web_search_api_key or (saved.get("web_search", {}) or {}).get("api_key", "")

    new_cfg = {
        "provider": body.provider,
        "base_url": base_url,
        "api_key": cfg.encrypt_for_save(api_key),
        "model_gen": model_gen,
        "model_qc": model_qc,
        "web_search": {
            "enabled": body.web_search_enabled,
            "backend": body.web_search_backend or "auto",
            "api_key": cfg.encrypt_for_save(ws_api_key),
        },
        "mineru": {"api_key": cfg.encrypt_for_save(mineru_api_key),
                   "auto_ocr": body.mineru_auto_ocr},
        "projects_dir": saved.get("projects_dir", cfg.DEFAULTS["projects_dir"]),
    }
    cfg.save(new_cfg)
    return cfg.public_view(new_cfg)


# ---------------------------------------------------------------- MinerU OCR（任务制）
class MineruTestBody(BaseModel):
    api_key: str = ""


@app.post("/api/mineru/test")
def mineru_test(body: MineruTestBody) -> dict[str, Any]:
    key = body.api_key or resolve_key((cfg.load().get("mineru", {}) or {}).get("api_key", ""))
    client = MinerUClient(key)
    ok, msg = client.test()
    return {"ok": ok, "msg": msg, "mode": client.mode()}


OCR_JOBS: dict[str, dict[str, Any]] = {}
OCR_LOCK = threading.Lock()
OCR_SEM = threading.Semaphore(2)  # 同时并行 OCR 数上限（mineru 限频保护）
OCR_JOB_DIR = cfg.CONFIG_DIR / "ocr"


def _ocr_job_set(job_id: str, state: str | None = None, msg: str | None = None) -> None:
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
        if job:
            if state:
                job["state"] = state
            if msg is not None:
                job["msg"] = msg


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
            client = MinerUClient(resolve_key(
                (cfg.load().get("mineru", {}) or {}).get("api_key", "")))
            _ocr_job_set(jid, "running", "已提交，等待 MinerU 调度…")
            markdown = client.extract(
                tmp_path,
                progress=lambda label: _ocr_job_set(jid, "running", label),
                cancel=job["cancel"])
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


@app.post("/api/ocr/start")
async def ocr_start(file: UploadFile = File(...),
                    role: str = Form("textbook")) -> dict[str, Any]:
    _ocr_job_cleanup()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in TEXT_SUFFIXES:
        raise HTTPException(400, f"不支持的类型 {suffix}（PDF/DOCX/MD/TXT/图片）")
    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(400, "文件超过 200 MB，请按章节拆分后重试")

    OCR_JOB_DIR.mkdir(parents=True, exist_ok=True)
    jid = uuid.uuid4().hex[:12]
    tmp_path = str(OCR_JOB_DIR / f"{jid}{suffix}")
    await asyncio.to_thread(Path(tmp_path).write_bytes, data)  # v0.5：≥200MB 写盘移出事件循环

    job = {"id": jid, "name": file.filename, "role": role, "state": "queued",
           "msg": "排队中…", "result": None, "created": time.time(),
           "cancel": threading.Event()}
    with OCR_LOCK:
        OCR_JOBS[jid] = job
    threading.Thread(target=_run_ocr_job, args=(job, tmp_path, file.filename, suffix),
                     daemon=True).start()
    return {"job_id": jid, "state": "queued"}


@app.get("/api/ocr/jobs/{job_id}")
def ocr_job(job_id: str) -> dict[str, Any]:
    _ocr_job_cleanup()
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    return {"job_id": job_id, "state": job["state"], "msg": job["msg"],
            "result": job["result"]}


@app.delete("/api/ocr/jobs/{job_id}")
def ocr_cancel(job_id: str) -> dict[str, Any]:
    with OCR_LOCK:
        job = OCR_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在或已过期")
    job["cancel"].set()
    _ocr_job_set(job_id, "cancelled", "已取消")
    return {"job_id": job_id, "state": "cancelled"}


# ---------------------------------------------------------------- LLM 工具
class TestBody(BaseModel):
    base_url: str
    api_key: str = ""
    model: str


@app.post("/api/llm/test")
def llm_test(body: TestBody) -> dict[str, Any]:
    key = body.api_key or resolve_key(cfg.load().get("api_key", ""))
    try:
        client = LLMClient(body.base_url, key, body.model, timeout=30)
        ok, msg = client.test()
        return {"ok": ok, "msg": msg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": str(e)}


class ModelsBody(BaseModel):
    base_url: str
    api_key: str = ""


@app.post("/api/llm/models")
def llm_models(body: ModelsBody) -> dict[str, Any]:
    """POST + JSON body：Key 不进 URL（避免日志记录）。"""
    key = body.api_key or resolve_key(cfg.load().get("api_key", ""))
    try:
        client = LLMClient(body.base_url, key, "x", timeout=20)
        return {"ok": True, "models": client.list_models()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "models": [], "msg": str(e)}


# ---------------------------------------------------------------- 素材解析
MAX_FILE_SIZE = 200 * 1024 * 1024  # 200 MB（对齐 MinerU 精准 API 上限）
CHARS_PER_TOKEN = 0.8  # 中文估算：1 字 ≈ 0.8 token（2026-08 审计修正，防高估一倍）
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TEXT_SUFFIXES = {".pdf", ".docx", ".md", ".markdown", ".txt"} | IMAGE_SUFFIXES


def _analyze_slices(slices: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """切片质量体检：章节结构 / 内容量 / token 估算。"""
    warnings: list[str] = []
    chapter_like = [s for s in slices if s["title"]]
    if len(chapter_like) < 2:
        warnings.append("未检测到章节标题（第X章 / 一、 / 1.1 / ##），将按全文整体切片；"
                        "建议使用带章节标题的教材版本，可自动按章分配题数")
    total_chars = sum(b["chars"] for b in blocks)
    if total_chars < 800:
        warnings.append("内容较短（疑似节选），出题素材不足时建议补充完整章节")
    if any(b.get("label", "").startswith("P") for b in blocks) and len(blocks) > 30:
        warnings.append("PDF 页数较多：建议只上传目标章节对应的页码范围/文件，降低输入成本")
    return {
        "chars": total_chars,
        "est_tokens": int(total_chars * CHARS_PER_TOKEN),
        "slice_count": len(slices),  # 契约字段（前端渲染依赖）
        "warnings": warnings,
        "slices": [{"sid": s["sid"], "title": s["title"],
                    "chars": len(s["text"]),
                    "text": s["text"],
                    "preview": s["text"][:120].replace("\n", " ")}
                   for s in slices],  # 全量返回（本地回环，无网络开销）
    }


def _mineru_to_result(name: str, markdown: str, via: str) -> dict[str, Any]:
    """MinerU OCR 结果 → 与本地解析同构的 result。"""
    text = markdown or ""
    blocks = [{"index": 0, "label": "MinerU-OCR", "text": text, "chars": len(text)}]
    slices = slice_text(blocks)
    info = _analyze_slices(slices, blocks)
    return {"name": name, "ok": True, "via": via,
            "via_note": "MinerU OCR 识别完成，已自动加入输入", **info}


def _parse_bytes(name: str, data: bytes, suffix: str) -> dict[str, Any]:
    """单文件本地解析（同步，由调用方放入线程池）。返回 result dict。

    OCR 需要时的结果带 ocr_needed 标记，由前端决定是否启动 OCR 任务。
    """
    if len(data) > MAX_FILE_SIZE:
        return {"name": name,
                "error": "文件超过 200 MB。建议按章节拆分成多个文件（也符合“一次一章”的推荐做法）"}
    if suffix in IMAGE_SUFFIXES:
        return {"name": name, "error": "图片文件需要「扫描件自动识别（MinerU OCR）」；"
                                        "开启后将自动识别并加入输入",
                "ocr_needed": True, "ocr_reason": "image"}
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        blocks = ex.extract_text(tmp_path)
        slices = slice_text(blocks)
        info = _analyze_slices(slices, blocks)
        return {"name": name, "ok": True, "via": "local", **info}
    except ex.ExtractError as e:
        return {"name": name, "error": str(e), "ocr_needed": True, "ocr_reason": "scan"}
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@app.post("/api/parse")
async def parse_files(files: list[UploadFile] = File(...),
                      role: str = Form("textbook"),
                      ocr: str = Form("0")) -> dict[str, Any]:
    """本地文本层解析（线程池执行，绝不阻塞事件循环）；OCR 走任务接口。"""
    async def process(f: UploadFile) -> dict[str, Any]:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in TEXT_SUFFIXES:
            return {"name": f.filename,
                    "error": f"不支持的类型 {suffix}（支持 PDF/DOCX/MD/TXT；图片需开启 OCR）"}
        data = await f.read()
        return await asyncio.to_thread(_parse_bytes, f.filename, data, suffix)

    results = await asyncio.gather(*(process(f) for f in files))
    return {"role": role, "results": list(results)}


# ---------------------------------------------------------------- 示例素材（首次体验引导）
# 打包后位于 <可执行目录>/_internal/medkit/data/samples（随包分发）
SAMPLE_DIR = Path(__file__).parent / "data" / "samples"
SAMPLE_TEXTBOOK = SAMPLE_DIR / "样例_儿科学_节选.md"
SAMPLE_TEACHER = SAMPLE_DIR / "样例_教师重点.md"


@app.get("/api/sample")
def sample_materials() -> dict[str, Any]:
    """一键载入示例素材（体验用）：返回与 /api/parse 相同结构。"""
    def load(path: Path, name: str) -> dict[str, Any]:
        blocks = ex.extract_text(path)
        slices = slice_text(blocks)
        info = _analyze_slices(slices, blocks)
        return {"name": name, "ok": True, **info}

    try:
        return {"sample": True,
                "subject": "儿科学（示例）",
                "teacher_text": "\n".join(s["text"] for s in load(SAMPLE_TEACHER, "示例_教师重点.md")["slices"]),
                "textbook": load(SAMPLE_TEXTBOOK, "示例_教材_儿科学节选.md"),
                "teacher": load(SAMPLE_TEACHER, "示例_教师重点.md")}
    except ex.ExtractError as e:
        return {"sample": False, "error": str(e)}


# ---------------------------------------------------------------- 项目
DEFAULT_BLOOM_RATIOS = {"记忆": 30, "理解": 40, "应用": 25, "创造": 5}


class ProjectBody(BaseModel):
    subject: str
    exam: str = "期末"
    target: int = 100
    ratios: dict[str, int] = {"A1": 40, "A2": 30, "B1": 20, "X": 10}
    toggles: dict[str, bool] = {"qbank": True, "paper": True, "review": True}
    textbook_slices: list[dict[str, Any]] = []
    teacher_slices: list[dict[str, Any]] = []
    teacher_text: str = ""
    exam_slices: list[dict[str, Any]] = []
    # 可玩性 1A/2A/2B
    requirements: str = ""                              # 自由文本附加要求（≤500 字）
    knobs: dict[str, str] = {}                          # 结构化旋钮
    bloom: dict[str, int] = {}                          # Bloom 配比（空 = 默认 30/40/25/5）
    # §5.4 多轮网络检索（默认关）
    web_search: bool = False
    web_backend: str = "auto"
    web_ref_quota: int = 0                              # 引用配额 0~30%，默认 0
    web_manual_text: str = ""                           # manual 模式：用户粘贴素材


def _validate_bloom(bloom: dict[str, int]) -> dict[str, int]:
    if not bloom:
        return dict(DEFAULT_BLOOM_RATIOS)
    clean = {k: int(v or 0) for k, v in bloom.items() if k in DEFAULT_BLOOM_RATIOS}
    if sum(clean.values()) != 100:
        raise HTTPException(400, f"Bloom 配比合计应为 100%（当前 {sum(clean.values())}%）")
    return clean


@app.post("/api/projects")
def create_project(body: ProjectBody) -> dict[str, Any]:
    if not body.subject.strip():
        raise HTTPException(400, "科目不能为空")
    if not body.textbook_slices or not any(s.get("text") for s in body.textbook_slices):
        raise HTTPException(400, "教材为必填项，请先解析上传教材")
    if not body.teacher_slices or not any(s.get("text") for s in body.teacher_slices):
        raise HTTPException(400, "教师重点为必填项，请先解析上传教师重点")
    if not (10 <= body.target <= 500):
        raise HTTPException(400, "目标题数需在 10~500 之间")
    ratio_sum = sum(v for v in body.ratios.values() if v > 0)
    if abs(ratio_sum - 100) > 1:
        raise HTTPException(400, f"题型配比合计应为 100%（当前 {ratio_sum}%），请调整后重试")
    if not (0 <= body.web_ref_quota <= 30):
        raise HTTPException(400, "网络引用配额需在 0~30% 之间")
    bloom = _validate_bloom(body.bloom)
    req = (body.requirements or "").strip()
    if len(req) > 500:
        raise HTTPException(400, "附加生成要求超过 500 字，请精简")

    # 项目 ID 防呆：仅保留中英数字与 -_，避免特殊字符进路径
    safe_subject = re.sub(r"[^\w\u4e00-\u9fff-]", "_", body.subject.strip())
    pid = f"{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    proj_dir = Path(cfg.load()["projects_dir"]) / pid
    proj_dir.mkdir(parents=True, exist_ok=True)

    all_slices = ([{**s, "role": "textbook"} for s in body.textbook_slices]
                  + [{**s, "role": "teacher"} for s in body.teacher_slices]
                  + [{**s, "role": "exam"} for s in body.exam_slices])
    (proj_dir / "slices.json").write_text(
        json.dumps(all_slices, ensure_ascii=False, indent=1), encoding="utf-8")

    title_by_sid = {s["sid"]: s.get("title", "") for s in body.textbook_slices}
    quota = [{**q, "title": title_by_sid.get(q["sid"], "")} for q in
             allocate(body.textbook_slices, body.teacher_text, body.target)]
    meta = {
        "pid": pid,
        "subject": body.subject,
        "exam": body.exam,
        "target": body.target,
        "ratios": body.ratios,
        "toggles": body.toggles,
        "requirements": req,
        "knobs": body.knobs,
        "bloom": bloom,
        "web_search": bool(body.web_search),
        "web_backend": body.web_backend or "auto",
        "web_ref_quota": int(body.web_ref_quota or 0),
        "web_manual_text": (body.web_manual_text or "")[:20000],
        "stages": {"parsing": "done"},
        "stage": "quota",
        "quota": quota,
        "seed": int(random.random() * 1_000_000),  # 每项目固定种子：可复现 + 每次不同
        "created": datetime.now().isoformat(),
    }
    _write_meta_atomic(proj_dir, meta)
    (proj_dir / "stage.json").write_text(
        json.dumps({"stage": "quota", "updated": datetime.now().isoformat()}),
        encoding="utf-8")
    return {"pid": pid, "quota": quota}


STAGE_LABELS = {
    "websearch": "网络检索中", "parsing": "解析素材", "quota": "配额已分配",
    "generating": "出题中",
    "gate1": "门禁①", "qc": "质检中", "fixing": "修复中",
    "finalizing": "汇总题库", "reviewing": "复习手册生成中",
    "rendering": "渲染产物", "done": "已完成", "error": "出错（见日志）",
    "cancelled": "已取消（可继续生成）",
}


@app.get("/api/projects")
def list_projects() -> dict[str, Any]:
    base = Path(cfg.load()["projects_dir"])
    items = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            stage_raw = meta.get("stage", "")
            pkey = meta.get("pid", d.name)
            items.append({"pid": pkey,
                          "subject": meta.get("subject", ""),
                          "exam": meta.get("exam", ""),
                          "target": meta.get("target", 0),
                          "stage": stage_raw,
                          "running": bool(RUNNING.get(pkey)),
                          "stage_label": STAGE_LABELS.get(stage_raw, stage_raw or "……"),
                          "created": meta.get("created", "")})
    return {"projects": items}


@app.get("/api/projects/{pid}")
def get_project(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    meta = _read_meta_checked(base)
    meta["stage_label"] = STAGE_LABELS.get(meta.get("stage", ""), meta.get("stage", ""))
    artifacts = _project_artifacts(base)
    meta["artifacts"] = artifacts
    meta["running"] = bool(RUNNING.get(pid))
    progress_path = base / "progress.json"
    if progress_path.exists():
        try:
            meta["progress"] = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return meta


def _project_artifacts(base: Path) -> list[str]:
    names: list[str] = []
    for d in (base, base / "最终产物"):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in (".md", ".html", ".json", ".txt") \
                    and p.name not in ("slices.json", "meta.json", "stage.json",
                                       "questions_raw.json", "questions_gate1.json",
                                       "checkpoint.json"):
                names.append(p.name)
    return names


@app.get("/api/projects/{pid}/status")
def project_status(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    meta = _read_meta_checked(base)
    stage = meta.get("stage", "")
    log_lines: list[str] = []
    if (base / "run.log").exists():
        log_lines = (base / "run.log").read_text(encoding="utf-8").splitlines()[-60:]
    progress = None
    progress_path = base / "progress.json"
    if progress_path.exists():
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            progress = None
    return {"pid": pid, "stage": stage,
            "stage_label": STAGE_LABELS.get(stage, stage or "……"),
            "running": bool(RUNNING.get(pid)),
            "progress": progress,
            "artifacts": _project_artifacts(base),
            "log": log_lines}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    if not base.exists():
        raise HTTPException(404, "项目不存在")
    if RUNNING.get(pid):
        raise HTTPException(400, "项目正在生成中：请先「停止」后再删除")
    if (base / "meta.json").exists():
        shutil.rmtree(base, ignore_errors=True)
    return {"ok": True}


@app.get("/api/projects/{pid}/files/{name}")
def project_file(pid: str, name: str) -> FileResponse:
    pid = _safe_pid(pid)
    if Path(name).name != name:  # 防路径穿越
        raise HTTPException(400, "非法文件名")
    if Path(name).suffix.lower() not in (".md", ".html", ".json", ".txt"):
        raise HTTPException(400, "仅支持预览 md/html/json/txt 产物")
    if name in ("meta.json", "slices.json", "stage.json", "progress.json"):  # 内部文件不对外预览
        raise HTTPException(404, "文件不存在")
    base = Path(cfg.load()["projects_dir"]) / pid
    f = base / name
    if not f.exists() or not f.is_file():
        f2 = base / "最终产物" / name
        if not f2.exists() or not f2.is_file():
            raise HTTPException(404, "文件不存在")
        f = f2
    suffix = f.suffix
    mime = ("text/html; charset=utf-8" if suffix == ".html"
            else "text/plain; charset=utf-8")
    return FileResponse(f, media_type=mime)


@app.get("/api/projects/{pid}/export/anki")
def export_anki(pid: str) -> FileResponse:
    """U8：Anki 文本导入文件（正面/反面 Tab 分隔 + HTML 换行）。"""
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    meta = _read_meta_checked(base)
    if meta.get("stage") != "done":
        raise HTTPException(409, "项目尚未生成完成，无题库可导出")
    f = base / "最终产物" / "anki_export.txt"
    if not f.exists():
        raise HTTPException(404, "anki_export.txt 不存在，请重新生成")
    return FileResponse(f, media_type="text/plain; charset=utf-8",
                        filename="anki_export.txt")


@app.post("/api/projects/{pid}/run")
def run_project(pid: str) -> dict[str, Any]:
    """启动出题管线（后台线程，前端轮询 /status）；断点存在时自动续跑。"""
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    meta_ = _read_meta_checked(base)
    if meta_.get("stage") == "done":
        raise HTTPException(409, "该项目已完成生成（如需重跑请删除后重建）")
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先在「① 连接服务商」保存 API Key（留空会保留旧值）")
    if not c.get("model_gen"):
        raise HTTPException(400, "请先配置生成模型")
    with RUN_LOCK:
        if RUNNING.get(pid):
            raise HTTPException(409, "该项目正在生成中（可点「停止」或稍候查看 /status）")
        ev = threading.Event()
        RUNNING[pid] = ev
    threading.Thread(target=_run_pipeline_thread, args=(pid, ev), daemon=True).start()
    return {"ok": True, "started": True}


@app.delete("/api/projects/{pid}/run")
def cancel_project(pid: str) -> dict[str, Any]:
    """U1：取消生成（保留已生成题目与断点，可再次「开始生成」续跑）。"""
    pid = _safe_pid(pid)
    with RUN_LOCK:
        ev = RUNNING.get(pid)
    if not ev:
        raise HTTPException(404, "项目当前未在生成中")
    ev.set()
    return {"ok": True, "cancelling": True}


RUNNING: dict[str, threading.Event] = {}
RUN_LOCK = threading.Lock()


def _run_pipeline_thread(pid: str, cancel_ev: threading.Event) -> None:
    try:
        from .core.orchestrator import PipelineCancelled
        from .core.orchestrator import run_project as _rp
        try:
            res = _rp(pid, cancel=cancel_ev)
            base = Path(cfg.load()["projects_dir"]) / pid
            if res.get("stage") == "cancelled":
                _log_project(base, "⏹ 管线已取消（断点已保留，可继续生成）")
        except PipelineCancelled:
            pass  # 正常取消路径
    except Exception as e:  # noqa: BLE001
        base = Path(cfg.load()["projects_dir"]) / pid
        try:
            _log_project(base, f"❌ 管线失败：{e}")
            meta = json.loads((base / "meta.json").read_text(encoding="utf-8"))
            meta["stage"] = "error"
            _write_meta_atomic(base, meta)
        except Exception:  # noqa: BLE001
            pass
    finally:
        with RUN_LOCK:
            RUNNING.pop(pid, None)


def _log_project(base: Path, msg: str) -> None:
    try:
        with open(base / "run.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------- 试玩三件套（可玩性 迭代1）
class TrialBody(BaseModel):
    subject: str
    exam: str = "期末"
    requirements: str = ""
    knobs: dict[str, str] = {}
    ratios: dict[str, int] = {"A1": 40, "A2": 30, "B1": 20, "X": 10}
    slice_sid: str = "TRIAL"
    slice_title: str = ""
    slice_text: str
    teacher_text: str = ""


@app.post("/api/trial")
def trial(body: TrialBody) -> dict[str, Any]:
    """试出一题：不创建项目/不落盘/不跑管线；门禁即检，30~90 秒返回。"""
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先在「① 连接服务商」保存 API Key，再试出题")
    if not c.get("model_gen"):
        raise HTTPException(400, "请先配置生成模型")
    if not body.slice_text.strip():
        raise HTTPException(400, "切片内容为空")
    from .agents import medgen
    from .core import usage as usage_mod
    client = LLMClient(c.get("base_url", ""), resolve_key(c.get("api_key", "")),
                       c.get("model_gen", ""), timeout=90)
    slice_ = {"sid": body.slice_sid, "title": body.slice_title, "text": body.slice_text[:6000]}
    with usage_mod.context() as uctx:  # v0.5：试出独立记账（与并行管线互不串账）
        try:
            qs, _ = medgen.generate_slice(
                client, body.subject, body.exam, slice_, 1, body.ratios,
                body.teacher_text[:2000], requirements=body.requirements[:500],
                knobs=body.knobs)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"试出题失败：{e}") from e
    if not qs:
        raise HTTPException(502, "模型未返回有效题目，请重试或检查模型配置")
    issues = options_check.check_all(qs)["issues"]
    issues += trace_check.check_trace(qs, {body.slice_sid})["issues"]
    return {"question": qs[0], "issues": issues,
            "from_slice": f"{body.slice_sid} · {body.slice_title}",
            "usage": uctx.snapshot()}


# ---------------------------------------------------------------- 提示词（迭代1 只读 / 迭代3 编辑）
PROMPT_ROLES = {"medgen.md": "MedGen · 出题", "medqc.md": "MedQC · 质检",
                "medfix.md": "MedFix · 修复", "medreview.md": "MedReview · 复习手册"}


def _prompt_meta() -> dict[str, Any]:
    p = cfg.PROMPTS_DIR_USER / ".meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _save_prompt_meta(meta: dict[str, Any]) -> None:
    cfg.PROMPTS_DIR_USER.mkdir(parents=True, exist_ok=True)
    (cfg.PROMPTS_DIR_USER / ".meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _builtin_prompt(name: str) -> str:
    from .agents import PROMPTS_DIR
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{[a-z_]+\}", text)))


@app.get("/api/prompts")
def prompts() -> dict[str, Any]:
    from .agents import load_prompt
    meta = _prompt_meta()
    out = []
    for name, role in PROMPT_ROLES.items():
        builtin = _builtin_prompt(name)
        user_path = cfg.PROMPTS_DIR_USER / name
        custom = user_path.read_text(encoding="utf-8") if user_path.exists() else None
        base_hash = hashlib.sha256(builtin.encode("utf-8")).hexdigest()[:16]
        drifted = bool(custom and meta.get(name, {}).get("base_hash", "") != base_hash)
        out.append({
            "name": name, "role": role,
            "builtin": builtin, "custom": custom,
            "using": "custom" if custom else "builtin",
            "drifted": drifted,
            "placeholders": _placeholders(builtin),
            "content": load_prompt(name),  # 实际生效版（影子优先）
        })
    return {"prompts": out}


class PromptBody(BaseModel):
    content: str


@app.put("/api/prompts/{name}")
def put_prompt(name: str, body: PromptBody) -> dict[str, Any]:
    if name not in PROMPT_ROLES:
        raise HTTPException(404, "未知提示词")
    builtin = _builtin_prompt(name)
    required = _placeholders(builtin)
    missing = [p for p in required if p not in body.content]
    if missing:
        raise HTTPException(400, "缺少必需占位符：" + "、".join(missing)
                            + "（占位符会被运行时替换，删除将导致管线失败）")
    cfg.PROMPTS_DIR_USER.mkdir(parents=True, exist_ok=True)
    (cfg.PROMPTS_DIR_USER / name).write_text(body.content, encoding="utf-8")
    meta = _prompt_meta()
    meta[name] = {"base_hash": hashlib.sha256(builtin.encode("utf-8")).hexdigest()[:16]}
    _save_prompt_meta(meta)
    return {"ok": True, "using": "custom"}


@app.delete("/api/prompts/{name}")
def delete_prompt(name: str) -> dict[str, Any]:
    if name not in PROMPT_ROLES:
        raise HTTPException(404, "未知提示词")
    (cfg.PROMPTS_DIR_USER / name).unlink(missing_ok=True)
    return {"ok": True, "using": "builtin"}


# ---------------------------------------------------------------- 配置预设（迭代2C）
class PresetBody(BaseModel):
    name: str
    desc: str = ""
    payload: dict[str, Any] = {}


@app.get("/api/presets")
def list_presets() -> dict[str, Any]:
    return prs.list_presets()


@app.post("/api/presets")
def create_preset(body: PresetBody) -> dict[str, Any]:
    if not body.name.strip():
        raise HTTPException(400, "预设名称不能为空")
    return prs.save_preset(body.name, body.desc, body.payload)


@app.delete("/api/presets/{pid}")
def delete_preset(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)  # v0.5：路径穿越消毒（旧实现可删任意 ~/.medkit 下 .json）
    ok = prs.delete_preset(pid)
    if not ok:
        raise HTTPException(400, "内置预设不可删除（或预设不存在）")
    return {"ok": True}


# ---------------------------------------------------------------- 网络检索（§5.4）
class SearchTestBody(BaseModel):
    backend: str = "bocha"
    api_key: str = ""
    query: str = "儿科学 儿童生长发育 考试大纲"


@app.get("/api/search/backends")
def search_backends() -> dict[str, Any]:
    """检索后端注册表（含「自带 / 需外部搜索」能力标注，供 UI 动态渲染）。
    2026-08 官方核查：DeepSeek（Responses API web_search）/ 智谱（Web Search API）/ 千问（enable_search）
    三家均自带；自定义 OpenAI 兼容端点多数不带联网工具 → 需外部（博查）或手动。"""
    return {"backends": ws.BACKENDS,
            "note": ("自带网络搜索（2026-08 官方核查）：DeepSeek（Responses API web_search，服务端托管）· "
                     "智谱 GLM（专用 Web Search API）· 通义千问（enable_search，"
                     "qwen3-max 系列已支持，现行代际至 Qwen3.8 Max/Plus/Flash）。"
                     "自定义 OpenAI 兼容端点：多数不含联网工具，"
                     "必须搭配「博查」外部搜索或「手动粘贴」。"),
            "builtin_backend_by_provider": ws.BUILTIN_BACKEND_BY_PROVIDER}


@app.post("/api/search/test")
def search_test(body: SearchTestBody) -> dict[str, Any]:
    """单次检索连通性测试（不记账、不落盘）。manual 不支持在线测试。"""
    backend = ws.resolve_backend(body.backend, cfg.load().get("provider", "deepseek"),
                                 body.api_key or resolve_key(
                                     (cfg.load().get("web_search", {}) or {}).get("api_key", "")))
    if backend == "manual":
        return {"ok": False, "msg": "手动粘贴模式不需要测试；直接在「② 新建课题」粘贴素材即可"}
    try:
        fn = ws.build_backend_fn(backend, body.api_key or resolve_key(
            (cfg.load().get("web_search", {}) or {}).get("api_key", "")),
            cfg.load().get("model_gen", ""))
        results = fn(body.query[:60])
        return {"ok": True, "backend": backend, "count": len(results),
                "samples": results[:3],
                "msg": f"{ws.BACKEND_LABELS.get(backend, backend)} 连通（{len(results)} 条）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "backend": backend, "msg": f"测试失败：{e}"}


# ---------------------------------------------------------------- 迭代4：逐题审核台
@app.get("/api/projects/{pid}/questions")
def project_questions(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = Path(cfg.load()["projects_dir"]) / pid
    _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成，请先「开始生成」")
    return {"questions": json.loads(f.read_text(encoding="utf-8"))}


def _rerender_project(base: Path, questions: list[dict[str, Any]], meta: dict[str, Any]) -> list[str]:
    """审核后重渲染全部产物（复用渲染层；复习手册若有旧 MD 则重转 HTML）。"""
    from .core.orchestrator import _sample_paper
    from .render import qbank_html, review_html
    subject = meta.get("subject", "")
    toggles = meta.get("toggles", {})
    out_dir = base / "最终产物"
    out_dir.mkdir(exist_ok=True)
    qbank_md = qbank_html.export_md(questions, f"{subject} 题库")
    (out_dir / "qbank.md").write_text(qbank_md, encoding="utf-8")
    (out_dir / "qbank.html").write_text(
        qbank_html.export_html(questions, f"{subject} 题库"), encoding="utf-8")
    rendered = ["qbank.md", "qbank.html"]
    if toggles.get("paper", True):
        paper_qs = _sample_paper(questions, min(50, len(questions)))
        (out_dir / "押题卷.html").write_text(
            qbank_html.export_paper_html(paper_qs, f"{subject} 押题卷"), encoding="utf-8")
        rendered.append("押题卷.html")
    review_md_path = out_dir / "复习手册.md"
    if review_md_path.exists():
        (out_dir / "复习手册.html").write_text(
            review_html.review_to_html(review_md_path.read_text(encoding="utf-8"),
                                       f"{subject} 复习手册"), encoding="utf-8")
        rendered.append("复习手册.html")
    (out_dir / "anki_export.txt").write_text(
        qbank_html.export_anki(questions, f"{subject} 题库"), encoding="utf-8")
    rendered.append("anki_export.txt")
    meta["final_count"] = len(questions)
    _write_meta_atomic(base, meta)
    _log_project(base, f"✏️ 审核后重渲染：{len(questions)} 题（{', '.join(rendered)}）")
    return rendered


class ReviewBody(BaseModel):
    keep: list[str] = []
    drop: list[str] = []
    edits: list[dict[str, Any]] = []


@app.post("/api/projects/{pid}/questions/review")
def review_questions(pid: str, body: ReviewBody) -> dict[str, Any]:
    """keep/drop/edits → 覆盖题库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中审核 → 409（旧实现与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可审核（请等待完成或先停止）")
    base = Path(cfg.load()["projects_dir"]) / pid
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    keep_set = set(body.keep) if body.keep else None          # 空 = 全保留
    drop_set = set(body.drop)
    edits = {e.get("id"): e for e in body.edits if e.get("id")}
    out: list[dict[str, Any]] = []
    for q in questions:
        qid = q.get("id")
        if keep_set is not None and qid not in keep_set:
            continue
        if qid in drop_set:
            continue
        if qid in edits and edits[qid]:
            e = edits[qid]
            for k in ("question", "options", "answer", "analysis", "bloom", "type", "subtopic"):
                if k in e:
                    q[k] = e[k]
        out.append(q)
    if not out:
        raise HTTPException(400, "保留题数为 0，请至少保留一题")
    from .core.fsutil import write_json_atomic

    write_json_atomic(f, out)  # v0.5：原子写（旧实现裸 write_text）
    rendered = _rerender_project(base, out, meta)
    return {"ok": True, "questions": len(out), "rendered": rendered}


class RegenBody(BaseModel):
    id: str


@app.post("/api/projects/{pid}/regen")
def regen_question(pid: str, body: RegenBody) -> dict[str, Any]:
    """按 q.sid 找回原切片，单题重掷（generate_slice count=1），替换入库并重渲染。"""
    pid = _safe_pid(pid)
    if RUNNING.get(pid):  # v0.5：运行中重掷 → 409（避免与出题线程并发写盘）
        raise HTTPException(409, "项目正在生成中，暂不可重掷（请等待完成或先停止）")
    base = Path(cfg.load()["projects_dir"]) / pid
    meta = _read_meta_checked(base)
    f = base / "最终产物" / "questions_final.json"
    if not f.exists():
        raise HTTPException(404, "题库尚未生成")
    questions = json.loads(f.read_text(encoding="utf-8"))
    q = next((x for x in questions if x.get("id") == body.id), None)
    if q is None:
        raise HTTPException(404, f"题目 {body.id} 不存在")
    slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    sid = q.get("sid", "")
    slice_ = next((s for s in slices if s.get("sid") == sid), None)
    if slice_ is None:
        raise HTTPException(400, f"原切片 {sid} 不存在，无法重掷（可按编辑改用同章节其他题）")
    c = cfg.load()
    if not resolve_key(c.get("api_key", "")):
        raise HTTPException(400, "请先配置 API Key")
    from .agents import medgen
    from .core import usage as usage_mod
    teacher_text = "\n".join(s.get("text", "") for s in slices if s.get("role") == "teacher")
    client = medgen.make_client()
    with usage_mod.context() as uctx:  # v0.5：重掷独立记账（不再污染下一轮 run）
        try:
            new_qs, _ = medgen.generate_slice(
                client, meta.get("subject", ""), meta.get("exam", "期末"), slice_, 1,
                meta.get("ratios", {}), teacher_text,
                requirements=meta.get("requirements", ""), knobs=meta.get("knobs", {}),
                bloom=meta.get("bloom", None))
        except Exception as e:  # noqa: BLE001
            raise HTTPException(502, f"重掷失败：{e}") from e
    if not new_qs:
        raise HTTPException(502, "模型未返回有效题目，请重试")
    new_q = new_qs[0]
    new_q["id"] = q["id"]
    idx = questions.index(q)
    questions[idx] = new_q
    from .core.fsutil import write_json_atomic

    write_json_atomic(f, questions)  # v0.5：原子写（旧实现裸 write_text）
    rendered = _rerender_project(base, questions, meta)
    return {"ok": True, "question": new_q, "rendered": rendered,
            "usage": uctx.snapshot(),  # v0.5：重掷独立记账随响应返回
            "issues": options_check.check_all([new_q])["issues"]}


# ---------------------------------------------------------------- 静态前端
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


# ---------------------------------------------------------------- 启动后开浏览器
@app.on_event("startup")
def _open_browser_after_startup() -> None:
    if os.environ.get("MEDKIT_NO_BROWSER") == "1":
        return
    port = _local_port()

    def _open() -> None:
        try:
            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:  # noqa: BLE001
            pass
    threading.Timer(0.6, _open).start()
