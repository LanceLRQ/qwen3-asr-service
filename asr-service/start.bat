@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PYTHONPATH=%~dp0
set PATH=%~dp0bin;%~dp0bin\python;%PATH%

if not exist "bin\python\python.exe" (
    echo [ERROR] Python embeddable not found at bin\python\python.exe
    echo Please run setup.bat first.
    pause
    exit /b 1
)

bin\python\python.exe -m app.main %*
