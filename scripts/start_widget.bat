@echo off
REM 双击启动桌面挂件：自动使用项目 venv 的 Python，无需手动激活环境。
REM 用 pythonw（无控制台窗口）后台启动；若无 pythonw 则回退到 python。

setlocal
cd /d "%~dp0\.."

set "PYW=.venv\Scripts\pythonw.exe"
set "PY=.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo 找不到虚拟环境 .venv，请先按 README 安装依赖。
    pause
    exit /b 1
)

if exist "%PYW%" (
    start "" "%PYW%" "widget\app.py"
) else (
    start "" "%PY%" "widget\app.py"
)

endlocal
