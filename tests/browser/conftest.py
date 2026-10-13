"""tests/browser 基座：独立后端子进程 + Playwright 页面。

设计要点（对应 IMP-05 审查指南 §6.1）：
1. 隔离配置目录：启动独立 uvicorn 子进程（server_launcher.py），把 cfg.CONFIG_DIR /
   CONFIG_FILE / PROMPTS_DIR_USER / PRESETS_DIR / projects_dir 全部重定向到 pytest
   tmp_path 下的 home；绝不写真实 ~/.medkit。与 tests/conftest.py 的 monkeypatch 无关
   （它只作用于同一进程），浏览器层靠独立进程完成真正的隔离。
2. 隔离端口：随机空闲端口（bind 0 探测）+ 仅监听 127.0.0.1；主机的 Host/Origin 中间件
   本就放行回环地址。
3. 可旁路：SKIP_BROWSER=1 置位、或未装 playwright、或未装 chromium → 全部用例
   pytest.skip（退出码 0，不卡死）。chrome 探测只在首次运行一次并缓存。
4. **必须与单测分进程收集（R6-01，2026-09-15）**：本层的 `browser` fixture 是 session 级，
   `sync_playwright()` 上下文在整个会话期间保持打开；Playwright 同步 API 在 greenlet 中运行
   自己的事件循环，会把当前线程的 asyncio running-loop 标记置位——此后同进程内任何
   `asyncio.run()` 都抛 `RuntimeError: asyncio.run() cannot be called from a running event loop`。
   因此单测一律 `pytest -q --ignore=tests/browser`（verify.cmd 第 [2/3] 步与 CI verify job
   已显式如此），本层单独 `pytest tests/browser -q`。单测中需要驱动协程时用
   `tests/conftest.py` 的 `run_coro` fixture（在新线程里跑），不要直接 `asyncio.run()`。
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

_BROWSER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _BROWSER_DIR.parents[1]
_LAUNCHER = _BROWSER_DIR / "server_launcher.py"

# ---- playwright 可用性探测（缺失则不 import，避免收集期报错 / 异常） ----
_HAS_PLAYWRIGHT = False
try:
    from playwright.sync_api import sync_playwright  # noqa: F401
    _HAS_PLAYWRIGHT = True
except Exception:  # pragma: no cover - 缺 playwright 时走这条
    _HAS_PLAYWRIGHT = False

_ENGINE_OK: bool | None = None

# 2026-10-12：跨浏览器实测——引擎由环境变量选择（默认 chromium，保持既有口径不变）。
#   MEDKIT_TEST_BROWSER=firefox|webkit → 同一套用例在别的引擎上跑（窄屏/跨浏览器覆盖）。
_BROWSER_KIND = os.environ.get("MEDKIT_TEST_BROWSER", "chromium").strip().lower()
if _BROWSER_KIND not in ("chromium", "firefox", "webkit"):
    _BROWSER_KIND = "chromium"


def _skipped_by_env() -> bool:
    return os.environ.get("SKIP_BROWSER") == "1"


def _engine_available() -> bool:
    """探测当前引擎是否已安装（只跑一次，缓存结果）。"""
    global _ENGINE_OK
    if _ENGINE_OK is not None:
        return _ENGINE_OK
    try:
        with sync_playwright() as p:
            browser = getattr(p, _BROWSER_KIND).launch(headless=True)
            browser.close()
        _ENGINE_OK = True
    except Exception:  # pragma: no cover - 缺该引擎二进制时走这条
        _ENGINE_OK = False
    return _ENGINE_OK


def _free_port() -> int:
    """找一个随机空闲端口（先 bind 0 取号，再释放；仅监听回环）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _wait_ready(url: str, proc, timeout: float = 30.0) -> None:
    """轮询 GET / 直到 HTTP 200（同时确认已监听 + lifespan 启动完成）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError("browser 后端子进程异常退出")
        try:
            with urllib.request.urlopen(url, timeout=1.0) as r:
                if r.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.2)
    raise RuntimeError("browser 后端未就绪（超时）")


@pytest.fixture(scope="session")
def server_home(tmp_path_factory):
    """隔离配置根目录（server_url 与测试共享；测试可向其 projects 目录播种项目/产物）。"""
    return tmp_path_factory.mktemp("medkit-browser-home")


@pytest.fixture(scope="session")
def server_url(server_home):
    """启动独立后端子进程，返回 base_url。任一旁路条件命中即整体 skip。"""
    if _skipped_by_env():
        pytest.skip("SKIP_BROWSER=1：浏览器层被显式旁路（verify.cmd 已跳过该步骤）")
    if not _HAS_PLAYWRIGHT:
        pytest.skip("未安装 playwright：pip install -r requirements-dev.txt 后再跑浏览器用例")
    if not _engine_available():
        pytest.skip(
            f"未安装 {_BROWSER_KIND}：python -m playwright install {_BROWSER_KIND} 后再跑浏览器用例")

    home = server_home
    port = _free_port()
    env = dict(os.environ)
    env["MEDKIT_PORT"] = str(port)
    env["MEDKIT_NO_BROWSER"] = "1"
    proc = subprocess.Popen(
        [sys.executable, str(_LAUNCHER), str(port), str(home)],
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        _wait_ready(url, proc)
    except Exception:
        proc.kill()
        raise
    yield url
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture(scope="session")
def browser(server_url):
    """会话级浏览器实例（无头）。引擎由 `MEDKIT_TEST_BROWSER` 选择（默认 chromium）。

    `--no-sandbox` 是 chromium 专有参数，firefox/webkit 传了会报错 ⇒ 按引擎分支。
    """
    with sync_playwright() as p:
        kind = getattr(p, _BROWSER_KIND)
        args = ["--no-sandbox"] if _BROWSER_KIND == "chromium" else []
        b = kind.launch(headless=True, args=args)
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """函数级独立 context/page（默认 1280×800，互不串扰；每个测试自行 goto）。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    # v0.15（奥卡姆精简 P1）：首启向导（#wizard_mask）与错题/生成「模式门」（#modegate）
    # 均已移除——首启直接落在「开始」页、无任何遮罩，故不再需要预置「已见过」前置状态。
    # 「首启无弹层」由 tests/browser/test_first_run.py 把守（重新加回遮罩即红）。
    #
    # v0.15（P4）：出题工坊（生成链）**默认关闭**（localStorage `medkit-gen-enabled`）。
    # 生成链自己的浏览器用例（项目详情 / 产物下载 / 审核台…）需要它**开启**才能导航到
    # 「题库」页签，故此处预置为开；「默认关」这一事实由 test_first_run.py 显式清键后验证。
    pg.add_init_script("localStorage.setItem('medkit-gen-enabled','1');")
    yield pg
    ctx.close()
