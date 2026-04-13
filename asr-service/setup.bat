@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ==========================================
echo   Qwen3-ASR Service Windows Setup
echo ==========================================
echo.

:: 1. Detect Python environment
set PYTHON_MODE=
set PYTHON_BIN=

:: Check portable Python (bin\python + lib)
if exist "bin\python\python.exe" (
    if exist "lib\site-packages" (
        echo [INFO] 检测到便携版 Python 环境，使用便携版
        set PYTHON_MODE=portable
        set PYTHON_BIN=bin\python\python.exe
        set PIP_TARGET=--target=lib\site-packages
        goto :python_ready
    )
)

:: No portable environment, ask user
echo.
echo [INFO] 未检测到便携版 Python 环境（bin + lib 目录）
echo.
echo 请选择 Python 环境安装方式：
echo   1) 下载便携包（推荐，开箱即用）
echo   2) 使用系统 Python + venv 安装
echo.
set /p ENV_CHOICE="请输入选项 [1/2]（默认 1）: "
if "%ENV_CHOICE%"=="" set ENV_CHOICE=1

if "%ENV_CHOICE%"=="2" goto :setup_venv

:: --- Option 1: Portable package ---
echo.
echo [INFO] 请前往以下地址下载便携包：
echo.
echo   百度网盘: https://pan.baidu.com/s/1ahqW1mxIoNJTG2k6b4PkkA?pwd=6cth
echo   提取码: 6cth
echo.
echo   下载文件: qwen3-asr-service-python3.12-pytorch2.6-cu124-bin.7z
echo.
echo [INFO] 解压后将 bin 和 lib 目录放置到 asr-service 目录下：
echo.
echo   asr-service\
echo   ├── bin\
echo   │   ├── python\
echo   │   │   └── python.exe
echo   │   └── ...
echo   ├── lib\
echo   │   └── site-packages\
echo   │       └── ...
echo   ├── setup.bat
echo   ├── start.bat
echo   └── ...
echo.
echo [INFO] 放置完成后直接运行 start.bat 启动服务
echo.
pause
exit /b 0

:: --- Option 2: venv ---
:setup_venv
echo.
:: Check system python3/python version
set SYS_PYTHON=
where python >nul 2>&1
if %errorlevel%==0 (
    set SYS_PYTHON=python
) else (
    where python3 >nul 2>&1
    if %errorlevel%==0 (
        set SYS_PYTHON=python3
    )
)

if "%SYS_PYTHON%"=="" (
    echo [ERROR] 未找到系统 Python，请先安装 Python 3.12
    echo [ERROR] 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

:: Check version is 3.12
for /f "tokens=*" %%v in ('%SYS_PYTHON% -c "import sys; print(f\"{sys.version_info.major}.{sys.version_info.minor}\")"') do set PY_VER=%%v
echo [INFO] 检测到系统 Python 版本: %PY_VER%

if not "%PY_VER%"=="3.12" (
    echo.
    echo [ERROR] 当前 Python 版本为 %PY_VER%，需要 3.12
    echo [ERROR] 请下载 Python 3.12: https://www.python.org/downloads/release/python-31213/
    echo [ERROR] 或选择便携包方式安装（重新运行 setup.bat 选择选项 1）
    pause
    exit /b 1
)

:: Check existing venv
if exist "venv" (
    echo [INFO] 检测到已有 venv 虚拟环境
    set /p REINSTALL_VENV="是否删除并重新安装？[y/N]: "
    if /i "!REINSTALL_VENV!"=="y" (
        echo [INFO] 删除旧虚拟环境...
        rmdir /s /q venv
    ) else if /i "!REINSTALL_VENV!"=="yes" (
        echo [INFO] 删除旧虚拟环境...
        rmdir /s /q venv
    ) else (
        echo [INFO] 保留已有虚拟环境，跳过创建
        goto :venv_activate
    )
)

echo [INFO] 创建虚拟环境...
%SYS_PYTHON% -m venv venv

:venv_activate
call venv\Scripts\activate.bat
set PYTHON_MODE=venv
set PYTHON_BIN=venv\Scripts\python.exe
set PIP_TARGET=
echo [INFO] 已激活 venv 虚拟环境

:: Upgrade pip in venv
echo [INFO] 升级 pip...
%PYTHON_BIN% -m pip install --upgrade pip
goto :python_ready

:python_ready
:: Create necessary directories
if not exist "lib\site-packages" (
    if "%PYTHON_MODE%"=="portable" (
        mkdir lib\site-packages
        echo [INFO] Created lib\site-packages
    )
)

:: Install pip for portable mode
if "%PYTHON_MODE%"=="portable" (
    if not exist "bin\python\Scripts\pip.exe" (
        echo [INFO] Installing pip...
        if not exist "bin\get-pip.py" (
            echo [INFO] Downloading get-pip.py...
            powershell -Command "Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile 'bin\get-pip.py'"
        )
        bin\python\python.exe bin\get-pip.py
        echo [INFO] pip installed
    ) else (
        echo [INFO] pip already installed
    )
)

:: 4. Check CUDA
echo.
echo [INFO] Checking NVIDIA GPU...
nvidia-smi >nul 2>&1
if %errorlevel%==0 (
    echo [INFO] NVIDIA GPU detected, will install CUDA PyTorch
    set TORCH_INDEX=https://download.pytorch.org/whl/cu124
) else (
    echo [WARN] No GPU detected, will install CPU PyTorch
    set TORCH_INDEX=https://download.pytorch.org/whl/cpu
)

:: 5. Model source selection
echo.
echo ==========================================
echo   Model Configuration
echo ==========================================
echo.
echo Select model source:
echo   1) ModelScope (recommended for China)
echo   2) HuggingFace
echo   3) Manual (skip download)
echo.
set /p MODEL_CHOICE="Enter choice [1/2/3] (default 1): "
if "%MODEL_CHOICE%"=="" set MODEL_CHOICE=1

if "%MODEL_CHOICE%"=="1" (
    set MODEL_SOURCE=modelscope
    echo [INFO] Selected ModelScope
) else if "%MODEL_CHOICE%"=="2" (
    set MODEL_SOURCE=huggingface
    echo [INFO] Selected HuggingFace
) else if "%MODEL_CHOICE%"=="3" (
    set MODEL_SOURCE=manual
    echo [INFO] Selected manual mode
    echo.
    echo ==========================================
    echo   Manual Model Placement Guide
    echo ==========================================
    echo.
    echo Place model files in these directories:
    echo.
    echo   ASR 0.6B: %CD%\models\asr\0.6b\
    echo   ASR 1.7B: %CD%\models\asr\1.7b\
    echo   Align:    %CD%\models\align\0.6b\
    echo   VAD:      %CD%\models\vad\fsmn\
    echo   Punc:     %CD%\models\punc\ct-transformer\
    echo.
    echo Download from:
    echo   https://modelscope.cn/models/Qwen/Qwen3-ASR-0.6B
    echo   https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B
    echo.
    goto :end
) else (
    set MODEL_SOURCE=modelscope
    echo [INFO] Invalid option, using ModelScope
)

:: 6. Install PyTorch
echo.
echo [INFO] Installing PyTorch 2.6.0 (this may take several minutes)...
if "%TORCH_INDEX%"=="https://download.pytorch.org/whl/cu124" (
    %PYTHON_BIN% -m pip install %PIP_TARGET% torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url %TORCH_INDEX%
) else (
    %PYTHON_BIN% -m pip install %PIP_TARGET% torch torchaudio --index-url %TORCH_INDEX%
)

:: 7. Install other dependencies
echo.
echo [INFO] Installing project dependencies...
%PYTHON_BIN% -m pip install %PIP_TARGET% -r requirements.txt

:end
echo.
echo ==========================================
echo   Setup Complete
echo ==========================================
echo.
echo To start the service:
echo   start.bat --model-source %MODEL_SOURCE%
echo.
echo Or with custom options:
echo   start.bat --device cuda --model-size 0.6b --model-source %MODEL_SOURCE%
echo.
pause
