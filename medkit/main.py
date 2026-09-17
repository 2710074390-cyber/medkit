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
import time
import urllib.request
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .core import db as dbs
from .core import errors as errs
from .core.llm import LLMError
from .core.mineru import MinerUError
from .core.orchestrator import PipelineError
from .core.websearch import SearchError
from .logging_setup import setup_logging
from .routers import config as r_config
from .routers import data as r_data
from .routers import diagnostics as r_diagnostics
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
    except Exception as e:  # noqa: BLE001  日志失败不阻塞启动
        errs.record("main._lifespan", "静默容错（U-15 留痕）", e=e)
    # V-11（ADR-006「迁移路径 §3」）：启动即建库 + JSON→SQLite 幂等补导。
    # 为什么必须放在启动路径：
    #   ① ADR-006「退役条件 1」明确要求 `import_from_json()` 在**启动路径**幂等执行；
    #   ② 库域（mistakes/knowledge）自己从不建库，db 只由 syllabus/realexams/cards 按需建立
    #      → 只用错题本/学习中心的用户**永远停在 JSON 轨**，每次单行写入都是整文件原子重写
    #      （实测单条 add_mistake：JSON 轨 31.7ms 且 O(N) vs SQL 轨 0.3ms，97×）；
    #   ③ 也让「轨」不再取决于用户碰过哪个功能（V-10 修的是切换时的数据可见性，这里把
    #      切换时机定死）。
    # 幂等：migrate() 升级前自动备份、重复调用直接返回；import_from_json() 以 id 为键
    # INSERT OR REPLACE，导入成功后原 JSON 改名 `*.pre-db-import-*.bak` 留档（可回滚）。
    # 失败不阻断启动：留痕后退回 JSON 轨（数据始终可读），下次启动重试。
    try:
        dbs.migrate()
        dbs.import_from_json()
    except Exception as e:  # noqa: BLE001  迁移/补导失败 → 退回 JSON 轨，不阻断启动
        errs.record("main._lifespan", "启动建库/补导失败（本次回落 JSON 轨）", e=e)
    # B34：启动时恢复 OCR 任务记录（jobs.json）并清理无记录的孤儿 tmp 文件
    try:
        from .routers.ocr import restore_ocr_persistence
        restore_ocr_persistence()
    except Exception as e:  # noqa: BLE001  持久化恢复失败不阻塞启动
        errs.record("main._lifespan", "静默容错（U-15 留痕）", e=e)
    if os.environ.get("MEDKIT_NO_BROWSER") != "1":
        port = _local_port()

        def _open() -> None:
            try:
                # A-新19：浏览器打开前轮询等待服务监听（HTTP GET 重试，上限约 15s），不再固定 Timer(0.6)
                deadline = time.time() + 15
                while time.time() < deadline:
                    try:
                        with urllib.request.urlopen(
                                f"http://127.0.0.1:{port}/api/health", timeout=1) as resp:
                            if resp.status == 200:
                                break
                    except Exception as e:  # noqa: BLE001  服务未就绪 → 继续轮询
                        errs.record("main._open", "静默容错（U-15 留痕）", e=e)
                    time.sleep(0.3)
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception as e:  # noqa: BLE001
                # 审查（2026-08）：打开失败不再静默——打印可访问地址到控制台
                print(f"⚠️ 未能自动打开浏览器（{e}），请手动访问 http://127.0.0.1:{port}")
        threading.Thread(target=_open, daemon=True).start()
    yield
    # shutdown：无全局资源需清理（线程均为 daemon；文件写均原子）


app = FastAPI(title="MedKit · 医学题库工坊", version=APP_VERSION, lifespan=_lifespan)

# S2-4（R8+W）：应用侧 CSP。放行同源资源与内联（零构建前端 + 内联事件处理器），
# 其余一律封死——重点是 `connect-src 'self'` 与 `default-src 'self'`，
# 让注入内容无法把数据发到外部主机。
APP_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
           "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
           "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
           "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


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
    else:
        # S2-6（R8+W）：GET/HEAD 也要挡跨站触发——原实现只查非 GET 的 Origin，于是
        # 用户浏览的任意网页都能用 `<img src="http://127.0.0.1:4880/api/library/cards/export/apkg?subject=x">`
        # 触发**带副作用的 GET**（该端点会真的写文件；`/api/update/check` 会真的外呼 GitHub）。
        # `Sec-Fetch-Site` 由浏览器强制标注、页面脚本无法伪造（跨站触发必为 `cross-site`）；
        # 无该头的客户端（curl / 测试 / 本机工具）不受影响——威胁模型是「用户访问的恶意网页」。
        if (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
            return JSONResponse({"detail": "forbidden cross-site request"}, status_code=403)
        org = (request.headers.get("origin") or "").rstrip("/")
        if org and org not in _allowed_origins():
            return JSONResponse({"detail": "forbidden origin"}, status_code=403)
    resp = await call_next(request)
    # S2-4（R8+W）：响应头加固。前端是零构建的原生脚本 + 内联 onclick 范式，因此 script-src
    # 必须放行 'unsafe-inline'；但**外部加载与连接一律封死**——即便有内容注入成功，
    # 也无法把本机数据外带（这正是原报告"无 CSP/安全响应头"缺口的实际风险面）。
    resp.headers.setdefault("Content-Security-Policy", APP_CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    return resp


# ---------------------------------------------------------------- 统一异常体系（S2）
def _err_response(status: int, exc: Exception, code: str) -> JSONResponse:
    # U-15：留痕 + 计数（原始信息进日志/诊断，对外回显前统一脱敏）
    errs.record(code, str(exc))
    return JSONResponse(status_code=status,
                        content={"detail": errs.redact(str(exc)), "error_code": code})


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
    # U-15：进入错误计数与诊断清单（用户可查「按钮点了没反应」的原因）
    errs.record("INTERNAL_ERROR", str(exc),
                path=request.url.path, method=request.method)
    return JSONResponse(status_code=500, content={
        "detail": f"服务器内部错误（{exc.__class__.__name__}），详情已写入日志，可查看 ~/.medkit/logs/medkit.log",
        "error_code": "INTERNAL_ERROR",
    })


# ---------------------------------------------------------------- 路由装配
app.include_router(r_config.router)
app.include_router(r_data.router)
app.include_router(r_diagnostics.router)
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
