@echo off
REM Use UTF-8 code page so Python stdout (Chinese) displays correctly.
REM Bat echo lines below are ASCII-only to avoid cmd mis-decoding non-UTF-8 BOM files.
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

where python >nul 2>&1
if errorlevel 1 goto TRY_PY
python -X utf8 extract_items.py
goto DONE

:TRY_PY
where py >nul 2>&1
if errorlevel 1 goto NO_PY
py -3 -X utf8 extract_items.py
goto DONE

:NO_PY
echo.
echo Python not found. Install Python and enable "Add python.exe to PATH",
echo or ensure "python" / "py" works in cmd.
echo.

:DONE
echo.
echo ========================================
echo   Click this window, then press any key to close.
echo ========================================
pause >nul
