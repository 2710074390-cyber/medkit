"""routers：项目 CRUD / 状态 / 产物访问 / Anki 导出。"""

import asyncio
import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import config as cfg
from ..core import errors as _errs
from ..core import projects as core_projects
from ..core.fsutil import safe_filename, write_json_atomic
from ..state import CANCELLING, RUNNING
from ._common import STAGE_LABELS, _read_meta_checked, _safe_pid, proj_dir, require_flag

_ALLOW_IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_MAX_ASSET_BYTES = 200 * 1024 * 1024   # R4-06：单图上传体积上限 200MB（超限 400，不落盘）
# R5-B-01：单项目 assets/ 累计容量上限（单文件拦不住“小文件无限堆积写满磁盘”——进与出的总量也要关门）
_MAX_ASSETS_TOTAL = 600 * 1024 * 1024
_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}

router = APIRouter()

# U-09：默认 Bloom 配比与建课并发原语已下沉 core（core/projects.py）；
# 此处保留同名再导出（main.py 与既有调用方引用不变）。
DEFAULT_BLOOM_RATIOS = core_projects.DEFAULT_BLOOM_RATIOS

# R3-17：图片素材 slices.json 读-改-原子写 整段 per-pid RLock（并发上传/删除不丢切片索引）
_ASSET_LOCK_GUARD = threading.Lock()
_ASSET_PID_LOCKS: dict[str, threading.RLock] = {}


def _asset_lock(pid: str) -> threading.RLock:
    with _ASSET_LOCK_GUARD:
        lock = _ASSET_PID_LOCKS.get(pid)
        if lock is None:
            lock = _ASSET_PID_LOCKS[pid] = threading.RLock()
        return lock


def _subject_lock(subject: str) -> threading.RLock:
    """U-09：委托 core（单一实现；保留既有私有名以兼容调用方）。"""
    return core_projects.subject_lock(subject)


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
    extra_slices: list[dict[str, Any]] = []             # v0.5.2：自备资料（课件/笔记/大纲）
    # 可玩性 1A/2A/2B
    requirements: str = ""                              # 自由文本附加要求（≤500 字）
    knobs: dict[str, str] = {}                          # 结构化旋钮
    bloom: dict[str, int] = {}                          # Bloom 配比（空 = 默认 30/40/25/5）
    # §5.4 多轮网络检索（默认关）
    web_search: bool = False
    web_backend: str = "auto"
    web_ref_quota: int = 0                              # 引用配额 0~30%，默认 0
    web_manual_text: str = ""                           # manual 模式：用户粘贴素材
    official_quota: int = 0                             # WP-10：官方306 补充条目配额（0 = 仅教师重点）
    client_token: str = ""                               # R3-08：创建意图幂等令牌（双击/双标签去重）


def _validate_bloom(bloom: dict[str, int]) -> dict[str, int]:
    if not bloom:
        return dict(DEFAULT_BLOOM_RATIOS)
    clean = {k: int(v or 0) for k, v in bloom.items() if k in DEFAULT_BLOOM_RATIOS}
    if sum(clean.values()) != 100:
        raise HTTPException(400, f"Bloom 配比合计应为 100%（当前 {sum(clean.values())}%）")
    return clean


@router.post("/api/projects")
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
    # R4-12：official_quota 越界由静默钳制改为显式 400（与 web_ref_quota/bloom 口径一致，meta 存原值不钳制）
    if not (0 <= int(body.official_quota or 0) <= 30):
        raise HTTPException(400, "官方大纲补充配额需在 0~30% 之间")
    bloom = _validate_bloom(body.bloom)
    req = (body.requirements or "").strip()
    if len(req) > 500:
        raise HTTPException(400, "附加生成要求超过 500 字，请精简")

    # U-09：HTTP 校验留在路由层；落盘委托 core（分层单向 routers → core）。
    # R3-08 幂等 / F3 同秒不合并 / F4 sid 重编号等语义已随实现下沉 core/projects.py。
    payload = body.model_dump()
    payload["bloom"] = bloom
    payload["requirements"] = req
    return core_projects.create_project_record(payload)


@router.get("/api/projects")
def list_projects() -> dict[str, Any]:
    base = Path(cfg.load()["projects_dir"])
    items = []
    if base.exists():
        for d in sorted(base.iterdir(), reverse=True):
            if not d.is_dir():
                continue
            meta_path = d / "meta.json"
            if not meta_path.exists():
                # R3-20：无 meta 的目录 → 孤儿条目（前端可显示可删除，不再永久残留）
                # R5-B-05：孤儿项明确标注「残留目录，可清理」——与“删了又冒出来”的用户困惑对齐
                items.append({"pid": d.name,
                              "subject": "（元数据缺失）",
                              "exam": "", "target": 0, "stage": "",
                              "running": False, "stage_label": "残留目录，可清理",
                              "created": "", "meta_missing": True})
                continue
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001  损坏 meta 同样按孤儿处理（可删除）
                # B-16：与单项目接口 422 口径对齐——列表虽不能抛错（断一个坏项目不该拖垮列表），
                # 但明确标记损坏原因，前端可区分「缺失」与「损坏」并提示手动清理
                items.append({"pid": d.name,
                              "subject": "（元数据缺失）",
                              "exam": "", "target": 0, "stage": "",
                              "running": False, "stage_label": "残留目录，可清理",
                              "created": "", "meta_missing": True, "meta_error": "corrupt"})
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


@router.get("/api/projects/{pid}")
def get_project(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    meta["stage_label"] = STAGE_LABELS.get(meta.get("stage", ""), meta.get("stage", ""))
    artifacts = _project_artifacts(base)
    meta["artifacts"] = artifacts
    meta["running"] = bool(RUNNING.get(pid))
    progress_path = base / "progress.json"
    if progress_path.exists():
        try:
            meta["progress"] = json.loads(progress_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            _errs.record("projects.get_project", "静默容错（U-15 留痕）", e=e)
    meta["substeps"] = _read_substeps(base)
    return meta


def _read_substeps(base: Path, limit: int = 50) -> list[dict[str, Any]]:
    """WP-3：读 substeps.jsonl 最近 limit 条子步骤事件（前端子步骤面板）。"""
    path = base / "substeps.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001  单行损坏跳过，不拖垮 status
                continue
    except Exception:  # noqa: BLE001
        return []
    return rows[-limit:]


def _project_artifacts(base: Path) -> list[str]:
    names: list[str] = []
    for d in (base, base / "最终产物"):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in (".md", ".html", ".json", ".txt", ".apkg") \
                    and p.name not in ("slices.json", "meta.json", "stage.json",
                                       "questions_raw.json", "questions_gate1.json",
                                       "checkpoint.json", "paper_ids.json"):
                names.append(p.name)
    return names


@router.get("/api/projects/{pid}/status")
def project_status(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = proj_dir(pid)
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
            "cancelling": bool(CANCELLING.get(pid)),   # R3-09：前端「正在取消中…」展示
            "progress": progress,
            "substeps": _read_substeps(base),
            "artifacts": _project_artifacts(base),
            "log": log_lines}


@router.delete("/api/projects/{pid}")
def delete_project(pid: str) -> dict[str, Any]:
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    if not base.exists():
        raise HTTPException(404, "项目不存在")
    if RUNNING.get(pid):
        raise HTTPException(400, "项目正在生成中：请先「停止」后再删除")
    meta_missing = not (base / "meta.json").exists()
    try:
        shutil.rmtree(base, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        # R5-B-15：删除失败不再吞掉——显式上抛
        raise HTTPException(500, f"项目删除失败：{e}") from e
    if base.exists():
        # R5-B-05/15：删后复核——部分文件被占用时 rmtree 会静默跳过（ignore_errors=True），
        # 此时绝不能返回 {"ok": true}（用户看到“删了又冒出来”）；显式报错并提示手动清理
        raise HTTPException(
            500, "项目目录未能完全删除（部分文件可能被占用）——请关闭占用该目录的程序后重试，"
                 f"或手动删除残留目录「{pid}」")
    if meta_missing:
        # R3-20：base 存在但 meta 缺失 → 无条件删目录，并明确提示
        return {"ok": True, "msg": "元数据缺失，已直接删除目录"}
    return {"ok": True}


@router.get("/api/projects/{pid}/files/{name}")
def project_file(pid: str, name: str) -> FileResponse:
    pid = _safe_pid(pid)
    if Path(name).name != name:  # 防路径穿越
        raise HTTPException(400, "非法文件名")
    if Path(name).suffix.lower() not in (".md", ".html", ".json", ".txt"):
        raise HTTPException(400, "仅支持预览 md/html/json/txt 产物")
    if name in ("meta.json", "slices.json", "stage.json", "progress.json"):  # 内部文件不对外预览
        raise HTTPException(404, "文件不存在")
    base = proj_dir(pid)
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


@router.get("/api/projects/{pid}/export/anki")
def export_anki(pid: str) -> FileResponse:
    """U8：Anki 文本导入文件（正面/反面 Tab 分隔 + HTML 换行）。"""
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    _read_meta_checked(base)  # 项目存在性校验（不依赖 stage=done：error/部分完成时已生成产物应可取）
    f = base / "最终产物" / "anki_export.txt"
    if not f.exists():  # 产物存在即可下载（不依赖 stage=done：error/部分完成时已生成的产物应可取）
        raise HTTPException(404, "anki_export.txt 不存在，请重新生成")
    return FileResponse(f, media_type="text/plain; charset=utf-8",
                        filename="anki_export.txt")


@router.get("/api/projects/{pid}/export/apkg")
def export_apkg_file(pid: str) -> FileResponse:
    """S3：Anki .apkg 真包导出（genanki；由管线渲染阶段生成，此处直接下发）。"""
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    out_dir = base / "最终产物"
    apkg = None
    if out_dir.exists():
        apkg = next((out_dir / f.name for f in out_dir.iterdir()
                     if f.suffix == ".apkg" and f.is_file()), None)
    if apkg is None:
        # 兜底：老项目无 apkg 产物 → 现场生成并落盘（后续重渲染会更新）
        qs_path = out_dir / "questions_final.json"
        if not qs_path.exists():
            raise HTTPException(404, "题库未生成，无法导出 .apkg")
        import json

        from ..render.apkg import export_apkg

        questions = json.loads(qs_path.read_text(encoding="utf-8"))
        tmp = out_dir / f"{safe_filename(meta.get('subject', '题库'))} 题库.apkg"
        export_apkg(questions, meta.get("subject", ""), pid, tmp)
        apkg = tmp
    return FileResponse(apkg, media_type="application/octet-stream",
                        filename=apkg.name)


# ---------------------------------------------------------------- 图片素材（WP-04 图/表格题）
def _image_slices(base: Path) -> list[dict[str, Any]]:
    try:
        slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    return [s for s in slices if s.get("role") == "image"]


def _next_fig_no(base: Path) -> int:
    asset_dir = base / "assets"
    if not asset_dir.is_dir():
        return 1
    nums = [int(p.stem.replace("fig_", "")) for p in asset_dir.glob("fig_*")
            if p.stem.replace("fig_", "").isdigit()]
    return (max(nums) if nums else 0) + 1


@router.post("/api/projects/{pid}/assets")
async def upload_asset(pid: str, file: UploadFile = File(...),
                       caption: str = Form("")) -> dict[str, Any]:
    """上传教材图片/表格素材 → assets/fig_N.ext + 追加 image 切片（生成时可出图题）。"""
    require_flag("image_q")
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    if not base.exists():
        raise HTTPException(404, "项目不存在")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "文件为空")
    # R4-06：体积限界在落盘/建索引之前——超限即 400，不产生任何磁盘副作用
    if len(raw) > _MAX_ASSET_BYTES:
        raise HTTPException(400, "图片过大（限 200MB）")
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOW_IMG:
        raise HTTPException(400, f"仅支持图片：{' / '.join(_ALLOW_IMG)}")
    # R3-17：slices.json 读-改-原子写 整段 per-pid RLock（并发上传两图不丢索引）
    with _asset_lock(pid):
        asset_dir = base / "assets"
        # R5-B-01：累计容量检查在创建目录/写文件/改索引**之前**——超限 400 且零磁盘副作用；
        # 附当前占用/上限，让用户知道差多少（单文件上限之外的“总量”视角）
        cur = sum(p.stat().st_size for p in asset_dir.iterdir() if p.is_file()) \
            if asset_dir.is_dir() else 0
        if cur + len(raw) > _MAX_ASSETS_TOTAL:
            raise HTTPException(
                400, f"图片素材总容量超限（当前占用 {cur / 1048576:.1f}MB / 上限 "
                     f"{_MAX_ASSETS_TOTAL / 1048576:.0f}MB），请删除部分素材或压缩图片后重试")
        asset_dir.mkdir(parents=True, exist_ok=True)
        n = _next_fig_no(base)
        fname = f"fig_{n}{ext}"
        # U-05：单图最大 200MB，write_bytes 为秒级阻塞写盘 → 放线程池
        await asyncio.to_thread((asset_dir / fname).write_bytes, raw)
        sid = f"IMG{n}"
        cap = (caption or "").strip() or file.filename or f"图{n}"
        # B-04：slices.json 读失败（损坏/非 JSON）不再静默重置为空表再追加——
        # 那会把已有有效索引覆盖为「只剩新图」；缺失文件（首次上传）才允许初始化 []
        sp = base / "slices.json"
        if sp.exists():
            try:
                slices = json.loads(sp.read_text(encoding="utf-8"))
                if not isinstance(slices, list):
                    raise ValueError("根节点不是数组")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, "项目切片索引损坏（slices.json 无法解析）——"
                                         "请勿继续上传；可在开发者工具/slices.json 恢复或重建项目") from e
        else:
            slices = []
        slices.append({"sid": sid, "role": "image", "title": cap, "text": cap,
                       "image": {"path": f"assets/{fname}", "name": file.filename or fname,
                                 "caption": cap, "source": "upload"}})
        write_json_atomic(base / "slices.json", slices)
    return {"ok": True, "sid": sid, "path": f"assets/{fname}",
            "caption": cap, "name": file.filename or fname}


@router.get("/api/projects/{pid}/assets")
def list_assets(pid: str) -> dict[str, Any]:
    require_flag("image_q")
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    if not base.exists():
        raise HTTPException(404, "项目不存在")
    rows = []
    for s in _image_slices(base):
        img = s.get("image") or {}
        full = base / str(img.get("path") or "")
        rows.append({"sid": s.get("sid"), "caption": s.get("text") or img.get("caption") or "",
                     "path": img.get("path") or "",
                     "bytes": full.stat().st_size if full.exists() else 0})
    return {"assets": rows}


@router.get("/api/projects/{pid}/assets/{sid}")
def asset_file(pid: str, sid: str) -> FileResponse:
    """图片文件服务（学习中心错题/产物预览用）。"""
    require_flag("image_q")
    pid = _safe_pid(pid)
    if not re.match(r"^[A-Za-z0-9_\-]+$", sid):
        raise HTTPException(400, "非法图片标识")
    base = proj_dir(pid)
    s = next((x for x in _image_slices(base) if x.get("sid") == sid), None)
    if not s:
        raise HTTPException(404, "图片不存在")
    f = base / str((s.get("image") or {}).get("path") or "")
    if not f.exists() or f.suffix.lower() not in _ALLOW_IMG:
        raise HTTPException(404, "图片文件缺失")
    return FileResponse(f, media_type=_IMG_MIME.get(f.suffix.lower(), "application/octet-stream"))


@router.delete("/api/projects/{pid}/assets/{sid}")
def delete_asset(pid: str, sid: str) -> dict[str, Any]:
    require_flag("image_q")
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    # R3-17：删除同样在 per-pid RLock 内整段读-改-原子写（防并发上传丢切片）
    with _asset_lock(pid):
        s = next((x for x in _image_slices(base) if x.get("sid") == sid), None)
        if not s:
            raise HTTPException(404, "图片不存在")
        f = base / str((s.get("image") or {}).get("path") or "")
        f.unlink(missing_ok=True)
        # B-04：读失败同样显式 5xx（不静默重置）
        sp = base / "slices.json"
        if sp.exists():
            try:
                slices = json.loads(sp.read_text(encoding="utf-8"))
                if not isinstance(slices, list):
                    raise ValueError("根节点不是数组")
            except Exception as e:  # noqa: BLE001
                raise HTTPException(500, "项目切片索引损坏（slices.json 无法解析）——"
                                         "删除操作已中止，请先修复索引后重试") from e
        else:
            slices = []
        write_json_atomic(base / "slices.json",
                          [x for x in slices if x.get("sid") != sid])
    return {"ok": True}
