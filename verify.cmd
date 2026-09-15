@echo off
REM ============================================================
REM  MedKit one-click verify: ruff + pytest + Playwright browser
REM  Usage: double-click or run "verify.cmd" from a terminal
REM  Exit code 0 = all green; 1 = something failed
REM  SKIP_BROWSER=1  -> skip the Playwright browser step (no browser env)
REM ============================================================
cd /d "%~dp0"

echo [1/3] ruff check ...
python -m ruff check . || goto :fail

echo [2/3] pytest ...
REM R6-01：显式排除 tests/browser——浏览器层与单测同进程收集时，session 级 Playwright
REM 同步上下文会占住主线程事件循环，导致直接 asyncio.run() 的用例必失败（本地红 / CI 绿）。
REM 浏览器层由第 [3/3] 步单独在独立进程中运行。
python -m pytest -q --ignore=tests/browser || goto :fail

echo [3/3] browser verify (Playwright, tests/browser) ...
if "%SKIP_BROWSER%"=="1" (
  echo   SKIP_BROWSER=1 detected - skipping browser tests
  goto :browser_done
)
python -m pytest tests/browser -q || goto :fail
:browser_done

echo.
echo ============ ALL GREEN ============
exit /b 0

:fail
echo.
echo ============ VERIFY FAILED ============
exit /b 1
