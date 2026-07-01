@echo off
REM Scheduled-task entry: run the headless crawl using the venv Python directly.
REM All comments are ASCII to avoid GBK/UTF-8 mojibake breaking the batch file.

setlocal
cd /d "%~dp0\.."

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo [ERROR] venv python not found: %VENV_PY%
    exit /b 1
)

set "PYTHONPATH=%cd%\src;%PYTHONPATH%"
"%VENV_PY%" -m nhk_tool.run_crawl
set "RC=%ERRORLEVEL%"

endlocal & exit /b %RC%
