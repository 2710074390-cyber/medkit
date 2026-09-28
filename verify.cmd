@echo off
REM ============================================================
REM  MedKit one-click verify: ruff + mypy + pytest + Playwright browser
REM                            + eslint + pip-audit + 打包纯净检查
REM  Usage: double-click or run "verify.cmd" from a terminal
REM  Exit code 0 = all green; 1 = something failed
REM  SKIP_BROWSER=1  -> skip the Playwright browser step (no browser env)
REM  SKIP_MYPY=1     -> skip the mypy step (mypy not installed)
REM  未构建 dist 时打包纯净检查自动跳过（check-package.py 自带该分支）
REM ============================================================
REM  与 CI 的等价性（2026-09-29 修）：本文件此前只跑 4 步，而 CI 的 verify job
REM  跑 8 个阻断步骤——**本地「一键全绿」并不等于 CI 绿**，这是门禁不对称。
REM  实测代价：88b647f（EP-01 阶段 2/3）引入 16 个 mypy 类型错误，
REM  因 mypy 只在 CI 跑、本地无从发现，静默存在 2 天后才被本文件补齐时发现。
REM  现补齐本地可跑且影响结果的项：mypy、eslint、pip-audit。
REM  未补项及理由：
REM    · `pytest -m migration`：那 6 个用例已含在 pytest 全量里，单列只是 CI 的
REM      「先失败先报」分组，不增加覆盖。
REM    · `--cov-fail-under=80`：覆盖率门槛需 pytest-cov 且显著拖慢本地；
REM      要卡覆盖率请直接用 CI 那条命令。
REM    · `pip check`：CI 在**干净 runner** 上跑它才有意义；开发者本机常装有
REM      无关包（实测本机 crawl4ai 0.8.9 要 lxml~=5.3 而项目锁 6.1.0），
REM      纳入总闸会稳定误红 -> 逼人学会忽略总闸。**故有意不纳入**。
REM ============================================================
cd /d "%~dp0"

echo [1/7] ruff check ...
python -m ruff check . || goto :fail

REM  P3：mypy 基线「只升不降」。mypy 不在 requirements.lock（那是运行时闭包），
REM  故未安装时**跳过并明示**，不伪装通过。
echo [2/7] mypy (type check, P3 baseline) ...
if "%SKIP_MYPY%"=="1" (
  echo   SKIP_MYPY=1 detected - skipping mypy
  goto :mypy_done
)
python -m mypy --version >nul 2>&1
if errorlevel 1 (
  echo   [跳过] mypy 未安装 - 请运行: python -m pip install -r requirements-dev.txt
  goto :mypy_done
)
python -m mypy medkit || goto :fail
:mypy_done

echo [3/7] pytest ...
REM R6-01：显式排除 tests/browser——浏览器层与单测同进程收集时，session 级 Playwright
REM 同步上下文会占住主线程事件循环，导致直接 asyncio.run() 的用例必失败（本地红 / CI 绿）。
REM 浏览器层由第 [4/7] 步单独在独立进程中运行。
python -m pytest -q --ignore=tests/browser || goto :fail

echo [4/7] browser verify (Playwright, tests/browser) ...
if "%SKIP_BROWSER%"=="1" (
  echo   SKIP_BROWSER=1 detected - skipping browser tests
  goto :browser_done
)
python -m pytest tests/browser -q || goto :fail
:browser_done

echo [5/7] frontend lint (npm run lint) ...
REM  U-17：前端静态防线。CI 用 `npm ci`；本地无 node_modules 时跳过并提示——
REM  不用 `npm install` 兜底，那会弱化 lock 约束（CI 注释已说明该理由）。
if not exist "node_modules" (
  echo   [跳过] 未安装 node_modules - 请运行: npm ci
  goto :npm_done
)
call npm run lint || goto :fail
:npm_done

echo [6/7] dependency audit (pip-audit -r requirements.lock --strict) ...
REM  S2-16：审计对象必须是**产物实际闭包**（requirements.lock），而非浮动的
REM  requirements.txt——否则「审计图 ≠ 产物图」（starlette CVE 就是这样漏网的）。
python -m pip_audit --version >nul 2>&1
if errorlevel 1 (
  echo   [跳过] pip-audit 未安装 - 请运行: python -m pip install -r requirements-dev.txt
  goto :audit_done
)
python -m pip_audit -r requirements.lock --strict || goto :fail
:audit_done

REM R6-11（borrow-rules §5）：总闸须覆盖打包纯净检查，此前只在 build.bat 里接线。
echo [7/7] package purity check (pack/check-package.py) ...
python pack\check-package.py || goto :fail

echo.
echo ============ ALL GREEN ============
exit /b 0

:fail
echo.
echo ============ VERIFY FAILED ============
exit /b 1
