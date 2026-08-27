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

_CHROMIUM_OK: bool | None = None


def _skipped_by_env() -> bool:
    return os.environ.get("SKIP_BROWSER") == "1"


def _chromium_available() -> bool:
    """探测 chromium 是否已安装（只跑一次，缓存结果）。"""
    global _CHROMIUM_OK
    if _CHROMIUM_OK is not None:
        return _CHROMIUM_OK
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        _CHROMIUM_OK = True
    except Exception:  # pragma: no cover - 缺 chromium 二进制时走这条
        _CHROMIUM_OK = False
    return _CHROMIUM_OK


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
def server_url(tmp_path_factory):
    """启动独立后端子进程，返回 base_url。任一旁路条件命中即整体 skip。"""
    if _skipped_by_env():
        pytest.skip("SKIP_BROWSER=1：浏览器层被显式旁路（verify.cmd 已跳过该步骤）")
    if not _HAS_PLAYWRIGHT:
        pytest.skip("未安装 playwright：pip install -r requirements-dev.txt 后再跑浏览器用例")
    if not _chromium_available():
        pytest.skip("未安装 chromium：python -m playwright install chromium 后再跑浏览器用例")

    home = tmp_path_factory.mktemp("medkit-browser-home")
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
    """会话级 chromium 实例（无头）。"""
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture
def page(browser):
    """函数级独立 context/page（默认 1280×800，互不串扰；每个测试自行 goto）。"""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)
    yield pg
    ctx.close()
