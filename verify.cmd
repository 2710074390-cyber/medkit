@echo off
REM ============================================================
REM  MedKit one-click verify: ruff static check + pytest suite
REM  Usage: double-click or run "verify.cmd" from a terminal
REM  Exit code 0 = all green; 1 = something failed
REM ============================================================
cd /d "%~dp0"

echo [1/2] ruff check ...
python -m ruff check . || goto :fail

echo [2/2] pytest ...
python -m pytest -q || goto :fail

echo.
echo ============ ALL GREEN ============
exit /b 0

:fail
echo.
echo ============ VERIFY FAILED ============
exit /b 1
