@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ==========================================
echo   Qwen3-ASR Service Windows Setup
echo ==========================================
echo.

:: 1. Check Python embeddable
if not exist "bin\python\python.exe" (
    echo [INFO] Extracting Python embeddable...
    if exist "bin\python-3.12.10-embed-amd64.zip" (
        powershell -Command "Expand-Archive -Path 'bin\python-3.12.10-embed-amd64.zip' -DestinationPath 'bin\python' -Force"
        echo [INFO] Python extracted
    ) else (
        echo [ERROR] bin\python-3.12.10-embed-amd64.zip not found
        pause
        exit /b 1
    )
) else (
    echo [INFO] Python embeddable already exists
)

:: 2. Create lib directory
if not exist "lib\site-packages" (
    mkdir lib\site-packages
    echo [INFO] Created lib\site-packages
)

:: 3. Install pip
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
    bin\python\python.exe -m pip install --target=lib\site-packages torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url %TORCH_INDEX%
) else (
    bin\python\python.exe -m pip install --target=lib\site-packages torch torchaudio --index-url %TORCH_INDEX%
)

:: 7. Install other dependencies
echo.
echo [INFO] Installing project dependencies...
bin\python\python.exe -m pip install --target=lib\site-packages -r requirements.txt

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
