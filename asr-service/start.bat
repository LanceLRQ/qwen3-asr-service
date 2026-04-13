@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYTHONPATH=%~dp0
set PATH=%~dp0bin;%~dp0bin\python;%PATH%

:: Detect Python environment: portable first, then venv
set PYTHON_BIN=

if exist "bin\python\python.exe" (
    set PYTHON_BIN=bin\python\python.exe
    echo [INFO] 使用便携版 Python
) else if exist "venv\Scripts\python.exe" (
    call venv\Scripts\activate.bat
    set PYTHON_BIN=venv\Scripts\python.exe
    echo [INFO] 使用 venv 虚拟环境
) else (
    echo [ERROR] 未检测到 Python 环境（便携版或 venv 均不存在）
    echo 请先运行 setup.bat 配置环境。
    pause
    exit /b 1
)

%PYTHON_BIN% -m app.main %*
