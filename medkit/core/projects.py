"""core：项目创建与共享原语（U-09 分层修复）。

**背景**：`core/gap.py` 此前通过函数级懒导入引用 `routers._common` / `routers.projects`
（`create_project` / `_subject_lock` / `proj_dir` / `_read_meta_checked`），形成
`core → routers` **反向依赖**，违反项目自定规矩 P1（单向 `routers → core`）。

**本模块**把「建课」能力与项目原语下沉到 core：路由层只保留 HTTP 校验与异常语义
（`routers/projects.py::create_project` 校验后调用本模块），core 层负责落盘。
core 层**不抛 HTTPException**（`read_meta` 容忍缺失/损坏，返回 `{}`）。
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config as cfg
from .fsutil import write_json_atomic
from .quota import allocate

# 默认 Bloom 配比（单一来源；routers/projects.py 与 main.py 再导出以保持兼容）
DEFAULT_BLOOM_RATIOS: dict[str, int] = {"记忆": 30, "理解": 40, "应用": 25, "创造": 5}

# R3-08：建项目并发安全——per-subject 可重入锁 + client_token 幂等去重。
# F3「同秒同名不合并」语义保留：不带 token 的两次提交仍各自建项目。
_CREATE_LOCK_GUARD = threading.Lock()
_SUBJECT_LOCKS: dict[str, threading.RLock] = {}
_TOKEN_PIDS: dict[str, tuple[float, str]] = {}
TOKEN_WINDOW = 10.0


def project_dir(pid: str) -> Path:
    """项目目录（单源：config.projects_dir）。"""
    return Path(cfg.load()["projects_dir"]) / pid


def subject_lock(subject: str) -> threading.RLock:
    """按科目串行化创建（RLock——同线程内 gap 建卷可重入）。"""
    key = (subject or "").strip() or "__empty__"
    with _CREATE_LOCK_GUARD:
        lock = _SUBJECT_LOCKS.get(key)
        if lock is None:
            lock = _SUBJECT_LOCKS[key] = threading.RLock()
        return lock


def read_meta(base: Path) -> dict[str, Any]:
    """读 meta.json；不存在 / 损坏 / 非 dict → 返回 `{}`（HTTP 语义归路由层）。"""
    try:
        data = json.loads((base / "meta.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  缺失或损坏均按空处理
        return {}
    return data if isinstance(data, dict) else {}


def write_meta_atomic(base: Path, meta: dict[str, Any]) -> None:
    """原子写 meta.json（唯一 tmp 名 + 重试，见 core/fsutil.py）。"""
    write_json_atomic(base / "meta.json", meta)


def create_project_record(payload: dict[str, Any]) -> dict[str, Any]:
    """已校验数据的项目落盘（**无 HTTP 语义**）。返回 `{"pid": ..., "quota": [...]}`。

    `payload` 字段与 `routers.projects.ProjectBody` 一致（由路由层 `model_dump()` 传入）；
    `bloom` 须为已校验的配比（路由层 `_validate_bloom` 的产物）。
    """
    subject = (payload.get("subject") or "").strip()
    target = int(payload.get("target") or 100)
    req = (payload.get("requirements") or "").strip()
    bloom = payload.get("bloom") or dict(DEFAULT_BLOOM_RATIOS)
    textbook_slices = list(payload.get("textbook_slices") or [])
    teacher_slices = list(payload.get("teacher_slices") or [])
    exam_slices = list(payload.get("exam_slices") or [])
    extra_slices = list(payload.get("extra_slices") or [])
    teacher_text = payload.get("teacher_text") or ""
    client_token = payload.get("client_token") or ""

    with subject_lock(subject):
        # R3-08：client_token 幂等——相同创建意图的重复提交复用已建项目（只扣一次配额）
        if client_token:
            hit = _TOKEN_PIDS.get(client_token)
            if hit and time.time() - hit[0] <= TOKEN_WINDOW and project_dir(hit[1]).exists():
                try:
                    hit_quota = json.loads(
                        (project_dir(hit[1]) / "meta.json").read_text(encoding="utf-8")
                    ).get("quota", [])
                except Exception:  # noqa: BLE001
                    hit_quota = []
                return {"pid": hit[1], "quota": hit_quota, "reused": True}

        # 项目 ID 防呆：仅保留中英数字与 -_，避免特殊字符进路径
        safe_subject = re.sub(r"[^\w\u4e00-\u9fff-]", "_", subject)
        pid = f"{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        proj_path = project_dir(pid)
        # F3（v0.5）：同秒同名项目不再静默合并（旧实现 mkdir(exist_ok=True) 直接覆盖同一目录）
        for _try in range(10):
            if not proj_path.exists():
                break
            pid = f"{safe_subject}_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_{_try + 1}"
            proj_path = project_dir(pid)
        proj_path.mkdir(parents=True, exist_ok=False)

        # F4（全链路审查）：多教材会话合并后各会话 sid 均从 S001 起始 → 去重前统一重编号，
        # 防止 orchestrator slice_by_sid 按 sid 覆盖导致前一教材切片静默丢失/配额错配。
        sids = [s.get("sid", "") or "" for s in textbook_slices]
        if len(set(sids)) != len(sids):
            textbook_slices = [{**s, "sid": f"S{i + 1:03d}"}
                               for i, s in enumerate(textbook_slices)]

        all_slices = ([{**s, "role": "textbook"} for s in textbook_slices]
                      + [{**s, "role": "teacher"} for s in teacher_slices]
                      + [{**s, "role": "exam"} for s in exam_slices]
                      + [{**s, "role": "extra"} for s in extra_slices])
        (proj_path / "slices.json").write_text(
            json.dumps(all_slices, ensure_ascii=False, indent=1), encoding="utf-8")

        title_by_sid = {s["sid"]: s.get("title", "") for s in textbook_slices}
        quota = [{**q, "title": title_by_sid.get(q["sid"], "")} for q in
                 allocate(textbook_slices, teacher_text, target)]
        meta = {
            "pid": pid,
            "subject": subject,
            "exam": payload.get("exam", "期末"),
            "target": target,
            "ratios": payload.get("ratios") or {},
            "toggles": payload.get("toggles") or {},
            "requirements": req,
            "knobs": payload.get("knobs") or {},
            "bloom": bloom,
            "web_search": bool(payload.get("web_search")),
            "web_backend": payload.get("web_backend") or "auto",
            "web_ref_quota": int(payload.get("web_ref_quota") or 0),
            "official_quota": int(payload.get("official_quota") or 0),
            "web_manual_text": (payload.get("web_manual_text") or "")[:20000],
            "exam_chars": sum(len(s.get("text", "") or "") for s in exam_slices),
            "extra_chars": sum(len(s.get("text", "") or "") for s in extra_slices),
            "stages": {"parsing": "done"},
            "stage": "quota",
            "quota": quota,
            "seed": int(random.random() * 1_000_000),  # 每项目固定种子：可复现 + 每次不同
            "created": datetime.now().isoformat(),
        }
        write_meta_atomic(proj_path, meta)
        (proj_path / "stage.json").write_text(
            json.dumps({"stage": "quota", "updated": datetime.now().isoformat()}),
            encoding="utf-8")
        if client_token:
            _TOKEN_PIDS[client_token] = (time.time(), pid)
        _now = time.time()   # 机会式清理过期键
        for _k in [k for k, v in _TOKEN_PIDS.items() if _now - v[0] > TOKEN_WINDOW]:
            _TOKEN_PIDS.pop(_k, None)
        return {"pid": pid, "quota": quota}
