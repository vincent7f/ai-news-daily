@echo off
rem ============================================================
rem  AI Daily News - Dry Run (fetch feeds only, no report written)
rem  用法: 双击运行，或在命令行加参数，例如:
rem    dry-run.bat --hours 48
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo ============================================
echo   AI Daily News - Dry Run (抓取测试)
echo ============================================
echo.

"%PY%" main.py --dry-run %*

echo.
echo Dry run finished. No report file was written.
echo.
pause
