"""routers：项目 CRUD / 状态 / 产物访问 / Anki 导出。"""

import json
import random
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..core import config as cfg
from ..core.quota import allocate
from ..state import RUNNING
from ._common import STAGE_LABELS, _read_meta_checked, _safe_pid, proj_dir, require_flag

_ALLOW_IMG = (".png", ".jpg", ".jpeg", ".webp", ".gif")
_IMG_MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
             ".webp": "image/webp", ".gif": "image/gif"}

router = APIRouter()

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
    bloom = _validate_bloom(body.bloom)
    req = (body.requirements or "").strip()
    if len(req) > 500:
        raise HTTPException(400, "附加生成要求超过 500 字，请精简")

    # 项目 ID 防呆：仅保留中英数字与 -_，避免特殊字符进路径
    safe_subject = re.sub(r"[^\w\u4e00-\u9fff-]", "_", body.subject.strip())
    pid = f"{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    proj_dir_path = proj_dir(pid)
    # F3（v0.5）：同秒同名项目不再静默合并（旧实现 mkdir(exist_ok=True) 直接覆盖同一目录）
    for _try in range(10):
        if not proj_dir_path.exists():
            break
        pid = f"{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{_try + 1}"
        proj_dir_path = proj_dir(pid)
    proj_dir_path.mkdir(parents=True, exist_ok=False)

    all_slices = ([{**s, "role": "textbook"} for s in body.textbook_slices]
                  + [{**s, "role": "teacher"} for s in body.teacher_slices]
                  + [{**s, "role": "exam"} for s in body.exam_slices]
                  + [{**s, "role": "extra"} for s in body.extra_slices])
    (proj_dir_path / "slices.json").write_text(
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
        "exam_chars": sum(len(s.get("text", "") or "") for s in body.exam_slices),   # v0.5.2
        "extra_chars": sum(len(s.get("text", "") or "") for s in body.extra_slices),
        "stages": {"parsing": "done"},
        "stage": "quota",
        "quota": quota,
        "seed": int(random.random() * 1_000_000),  # 每项目固定种子：可复现 + 每次不同
        "created": datetime.now().isoformat(),
    }
    from ._common import _write_meta_atomic

    _write_meta_atomic(proj_dir_path, meta)
    (proj_dir_path / "stage.json").write_text(
        json.dumps({"stage": "quota", "updated": datetime.now().isoformat()}),
        encoding="utf-8")
    return {"pid": pid, "quota": quota}


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
        except Exception:  # noqa: BLE001
            pass
    return meta


def _project_artifacts(base: Path) -> list[str]:
    names: list[str] = []
    for d in (base, base / "最终产物"):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and p.suffix in (".md", ".html", ".json", ".txt", ".apkg") \
                    and p.name not in ("slices.json", "meta.json", "stage.json",
                                       "questions_raw.json", "questions_gate1.json",
                                       "checkpoint.json"):
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
            "progress": progress,
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
    if (base / "meta.json").exists():
        shutil.rmtree(base, ignore_errors=True)
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
    meta = _read_meta_checked(base)
    if meta.get("stage") != "done":
        raise HTTPException(409, "项目尚未生成完成，无题库可导出")
    f = base / "最终产物" / "anki_export.txt"
    if not f.exists():
        raise HTTPException(404, "anki_export.txt 不存在，请重新生成")
    return FileResponse(f, media_type="text/plain; charset=utf-8",
                        filename="anki_export.txt")


@router.get("/api/projects/{pid}/export/apkg")
def export_apkg_file(pid: str) -> FileResponse:
    """S3：Anki .apkg 真包导出（genanki；由管线渲染阶段生成，此处直接下发）。"""
    pid = _safe_pid(pid)
    base = proj_dir(pid)
    meta = _read_meta_checked(base)
    if meta.get("stage") != "done":
        raise HTTPException(409, "项目尚未生成完成，无题库可导出")
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
        tmp = out_dir / f"{meta.get('subject', '题库')} 题库.apkg"
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
    ext = Path(file.filename or "").suffix.lower()
    if ext not in _ALLOW_IMG:
        raise HTTPException(400, f"仅支持图片：{' / '.join(_ALLOW_IMG)}")
    asset_dir = base / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    n = _next_fig_no(base)
    fname = f"fig_{n}{ext}"
    (asset_dir / fname).write_bytes(raw)
    sid = f"IMG{n}"
    cap = (caption or "").strip() or file.filename or f"图{n}"
    try:
        slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        slices = []
    slices.append({"sid": sid, "role": "image", "title": cap, "text": cap,
                   "image": {"path": f"assets/{fname}", "name": file.filename or fname,
                             "caption": cap, "source": "upload"}})
    (base / "slices.json").write_text(json.dumps(slices, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
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
    s = next((x for x in _image_slices(base) if x.get("sid") == sid), None)
    if not s:
        raise HTTPException(404, "图片不存在")
    f = base / str((s.get("image") or {}).get("path") or "")
    f.unlink(missing_ok=True)
    try:
        slices = json.loads((base / "slices.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        slices = []
    (base / "slices.json").write_text(
        json.dumps([x for x in slices if x.get("sid") != sid], ensure_ascii=False, indent=1),
        encoding="utf-8")
    return {"ok": True}
