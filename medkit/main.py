"""FastAPI 入口：装配 + 本地回环守卫 + 静态前端（S2：路由/模型已拆分至 routers/*）。

设计要点（2026-08 用户评审后重构）：
- 本地回环服务（Host/Origin 校验中间件防 CSRF 烧钱 + DNS rebinding 窃产物）
- routers/{config,ocr,parse,projects,pipeline,prompts,presets,search,review}.py
- lifespan 上下文（旧 @app.on_event 已弃用）：启动开浏览器 + 日志初始化
- 统一异常体映射（LLM/Search/MinerU/PipelineError → 结构化 JSONResponse）
- 兼容 re-export：测试与旧调用方仍可 `from medkit.main import ...`
"""

import logging
import os
import threading
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .core.llm import LLMError
from .core.mineru import MinerUError
from .core.orchestrator import PipelineError
from .core.websearch import SearchError
from .logging_setup import setup_logging
from .routers import config as r_config
from .routers import gap as r_gap
from .routers import library as r_library
from .routers import ocr as r_ocr
from .routers import parse as r_parse
from .routers import pipeline as r_pipeline
from .routers import presets as r_presets
from .routers import projects as r_projects
from .routers import prompts as r_prompts
from .routers import realexams as r_realexams
from .routers import review as r_review
from .routers import search as r_search
from .routers import syllabus as r_syllabus
from .routers import update as r_update

APP_VERSION = __version__

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


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """lifespan：启动（日志初始化 + 开浏览器）/ 关闭（预留清理）。"""
    try:
        setup_logging()
    except Exception:  # noqa: BLE001  日志失败不阻塞启动
        pass
    if os.environ.get("MEDKIT_NO_BROWSER") != "1":
        port = _local_port()

        def _open() -> None:
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception as e:  # noqa: BLE001
                # 审查（2026-08）：打开失败不再静默——打印可访问地址到控制台
                print(f"⚠️ 未能自动打开浏览器（{e}），请手动访问 http://127.0.0.1:{port}")
        threading.Timer(0.6, _open).start()
    yield
    # shutdown：无全局资源需清理（线程均为 daemon；文件写均原子）


app = FastAPI(title="MedKit · 医学题库工坊", version=APP_VERSION, lifespan=_lifespan)


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


# ---------------------------------------------------------------- 统一异常体系（S2）
def _err_response(status: int, exc: Exception, code: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"detail": str(exc), "error_code": code})


app.add_exception_handler(LLMError, lambda _r, e: _err_response(502, e, "LLM_ERROR"))
app.add_exception_handler(SearchError, lambda _r, e: _err_response(502, e, "SEARCH_ERROR"))
app.add_exception_handler(MinerUError, lambda _r, e: _err_response(502, e, "MINERU_ERROR"))
app.add_exception_handler(PipelineError, lambda _r, e: _err_response(500, e, "PIPELINE_ERROR"))


# H-3：未捕获异常统一兜底——结构化 500 + 中文可读提示 + 完整 traceback 入日志。
# 注意：HTTPException / RequestValidationError 已由 FastAPI 更具体的 handler 承接，不受影响。
@app.exception_handler(Exception)
async def _unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    logging.getLogger("medkit.main").exception(
        "未捕获异常（%s %s）: %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={
        "detail": f"服务器内部错误（{exc.__class__.__name__}），详情已写入日志，可查看 ~/.medkit/logs/medkit.log",
        "error_code": "INTERNAL_ERROR",
    })


# ---------------------------------------------------------------- 路由装配
app.include_router(r_config.router)
app.include_router(r_gap.router)
app.include_router(r_library.router)
app.include_router(r_ocr.router)
app.include_router(r_parse.router)
app.include_router(r_projects.router)
app.include_router(r_pipeline.router)
app.include_router(r_prompts.router)
app.include_router(r_presets.router)
app.include_router(r_search.router)
app.include_router(r_realexams.router)
app.include_router(r_syllabus.router)
app.include_router(r_review.router)
app.include_router(r_update.router)


# ---------------------------------------------------------------- 静态前端
@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


# ---------------------------------------------------------------- 兼容 re-export（S2）
# 旧测试/调用方契约：from medkit.main import app, cfg, HTTPException, ConfigBody,
# ProjectBody, put_config, create_project, _safe_pid, _analyze_slices, delete_preset,
# _run_ocr_job, MinerUClient, RUNNING, OCR_JOBS ...
# 以下导入仅为命名空间兼容（E402 代码后导入 / F401 仅导出）——勿删。
from fastapi import HTTPException  # noqa: E402, F401

from .core import config as cfg  # noqa: E402, F401
from .core import presets as _prs  # noqa: E402, F401
from .core import websearch as _ws  # noqa: E402, F401
from .core.mineru import MinerUClient  # noqa: E402, F401
from .routers._common import (  # noqa: E402, F401
    _analyze_slices,
    _log_project,
    _mineru_to_result,
    _parse_bytes,
    _read_meta_checked,
    _safe_pid,
    _write_meta_atomic,
    proj_dir,
)
from .routers.config import (  # noqa: E402, F401
    ConfigBody,
    ModelsBody,
    TestBody,
    get_config,
    llm_models,
    llm_test,
    providers,
    put_config,
)
from .routers.ocr import (  # noqa: E402, F401
    MineruTestBody,
    _ocr_job_cleanup,
    _ocr_job_set,
    _run_ocr_job,
    mineru_test,
    ocr_cancel,
    ocr_job,
    ocr_start,
)
from .routers.parse import (  # noqa: E402, F401
    SAMPLE_DIR,
    SAMPLE_TEACHER,
    SAMPLE_TEXTBOOK,
    parse_files,
    sample_materials,
)
from .routers.pipeline import (  # noqa: E402, F401
    CostBody,
    TrialBody,
    _run_pipeline_thread,
    cancel_project,
    cost_estimate,
    run_project,
    trial,
)
from .routers.presets import (  # noqa: E402, F401
    PresetBody,
    create_preset,
    delete_preset,
    list_presets,
)
from .routers.projects import (  # noqa: E402, F401
    DEFAULT_BLOOM_RATIOS,
    ProjectBody,
    _project_artifacts,
    _validate_bloom,
    create_project,
    delete_project,
    export_anki,
    get_project,
    list_projects,
    project_file,
    project_status,
)
from .routers.prompts import (  # noqa: E402, F401
    PROMPT_ROLES,
    PromptBody,
    _builtin_prompt,
    _placeholders,
    _prompt_meta,
    _save_prompt_meta,
    delete_prompt,
    prompts,
    put_prompt,
)
from .routers.review import (  # noqa: E402, F401
    RegenBody,
    ReviewBody,
    _rerender_project,
    project_questions,
    regen_question,
    review_questions,
)
from .routers.search import SearchTestBody, search_backends, search_test  # noqa: E402, F401
from .state import OCR_JOBS, OCR_LOCK, OCR_SEM, RUN_LOCK, RUNNING  # noqa: E402, F401

prs = _prs          # 预设模块
ws = _ws            # websearch 模块
