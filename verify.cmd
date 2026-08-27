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
python -m pytest -q || goto :fail

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
