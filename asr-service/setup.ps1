#Requires -Version 5.1
<#
.SYNOPSIS
    Qwen3-ASR Service Environment Setup (PowerShell)
.DESCRIPTION
    Sets up the Python environment for the ASR service.
    Supports portable Python and system Python + venv modes.
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

$script:PythonMode = ''
$script:PythonBin = ''
$script:PipTarget = @()
$script:ModelSource = ''

# ============================================================
# Functions (must be defined before use in PowerShell)
# ============================================================

function Show-PortableGuide {
    Write-Host
    Write-Host '[INFO] Please download the portable package from:' -ForegroundColor Cyan
    Write-Host
    Write-Host '  Baidu Pan: https://pan.baidu.com/s/1ahqW1mxIoNJTG2k6b4PkkA?pwd=6cth'
    Write-Host '  Access code: 6cth'
    Write-Host
    Write-Host '  Download file: qwen3-asr-service-python3.12-pytorch2.6-cu124-bin.7z'
    Write-Host
    Write-Host '[INFO] After extracting, place the bin and lib directories into the asr-service directory:' -ForegroundColor Cyan
    Write-Host
    Write-Host '  asr-service\'
    Write-Host '  +-- bin\'
    Write-Host '  ^|   +-- python\'
    Write-Host '  ^|       +-- python.exe'
    Write-Host '  +-- lib\'
    Write-Host '  ^|   +-- site-packages\'
    Write-Host '  +-- setup.ps1'
    Write-Host '  +-- start.ps1'
    Write-Host '  +-- ...'
    Write-Host
    Write-Host '[INFO] Once done, run start.ps1 to launch the service' -ForegroundColor Cyan
    Write-Host
    Read-Host 'Press Enter to exit'
}

function Activate-Venv {
    $script:PythonMode = 'venv'
    $script:PythonBin = Join-Path $PSScriptRoot 'venv\Scripts\python.exe'
    $script:PipTarget = @()
    Write-Host '[INFO] venv virtual environment activated' -ForegroundColor Green
}

function Initialize-Venv {
    # Detect system Python
    $sysPython = $null
    $pyCmd = Get-Command 'python' -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $sysPython = 'python'
    }
    else {
        $py3Cmd = Get-Command 'python3' -ErrorAction SilentlyContinue
        if ($py3Cmd) { $sysPython = 'python3' }
    }

    if (-not $sysPython) {
        Write-Host '[ERROR] System Python not found. Please install Python 3.12 first' -ForegroundColor Red
        Write-Host '[ERROR] Download: https://www.python.org/downloads/' -ForegroundColor Red
        Read-Host 'Press Enter to exit'
        exit 1
    }

    # Check version
    $pyVer = & $sysPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    Write-Host "[INFO] Detected system Python version: $pyVer" -ForegroundColor Cyan

    if ($pyVer -ne '3.12') {
        Write-Host
        Write-Host "[ERROR] Current Python version is $pyVer, but 3.12 is required" -ForegroundColor Red
        Write-Host '[ERROR] Please download Python 3.12: https://www.python.org/downloads/release/python-31213/' -ForegroundColor Red
        Write-Host '[ERROR] Or use the portable package method - re-run setup.ps1 and select option 1' -ForegroundColor Red
        Read-Host 'Press Enter to exit'
        exit 1
    }

    # Check existing venv
    if (Test-Path 'venv') {
        Write-Host '[INFO] Existing venv virtual environment detected' -ForegroundColor Yellow
        $reinstall = Read-Host 'Delete and reinstall? [y/N]'
        if ($reinstall -in @('y', 'Y', 'yes', 'Yes', 'YES')) {
            Write-Host '[INFO] Removing old virtual environment...' -ForegroundColor Cyan
            Remove-Item -Recurse -Force 'venv'
        }
        else {
            Write-Host '[INFO] Keeping existing virtual environment, skipping creation' -ForegroundColor Cyan
            Activate-Venv
            return
        }
    }

    Write-Host '[INFO] Creating virtual environment...' -ForegroundColor Cyan
    & $sysPython -m venv venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] Failed to create virtual environment' -ForegroundColor Red
        Read-Host 'Press Enter to exit'
        exit 1
    }

    Activate-Venv

    # Upgrade pip in venv
    Write-Host '[INFO] Upgrading pip...' -ForegroundColor Cyan
    & $script:PythonBin -m pip install --upgrade pip 2>$null
}

function Install-PipIfNeeded {
    if ($script:PythonMode -ne 'portable') { return }

    # Ensure lib\site-packages exists
    $sitePkg = Join-Path $PSScriptRoot 'lib\site-packages'
    if (-not (Test-Path $sitePkg)) {
        New-Item -ItemType Directory -Path $sitePkg -Force | Out-Null
        Write-Host '[INFO] Created lib\site-packages' -ForegroundColor Cyan
    }

    # Check pip in portable
    $pipExe = Join-Path $PSScriptRoot 'bin\python\Scripts\pip.exe'
    if (-not (Test-Path $pipExe)) {
        Write-Host '[INFO] Installing pip...' -ForegroundColor Cyan
        $getPip = Join-Path $PSScriptRoot 'bin\get-pip.py'
        if (-not (Test-Path $getPip)) {
            Write-Host '[INFO] Downloading get-pip.py...' -ForegroundColor Cyan
            Invoke-WebRequest -Uri 'https://bootstrap.pypa.io/get-pip.py' -OutFile $getPip
        }
        & $script:PythonBin $getPip
        Write-Host '[INFO] pip installed' -ForegroundColor Green
    }
    else {
        Write-Host '[INFO] pip already installed' -ForegroundColor Green
    }
}

# 须在 Install-Dependencies（按 requirements.txt 装入 CPU 版 torch）之后调用：
# GPU 环境下用 CUDA 版覆盖 requirements.txt 装入的 CPU 版 torch/torchaudio/torchvision。
function Install-PyTorch {
    Write-Host
    Write-Host '[INFO] Checking NVIDIA GPU...' -ForegroundColor Cyan

    $hasGpu = $false
    if (Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue) {
        try {
            $null = nvidia-smi 2>&1
            $hasGpu = $LASTEXITCODE -eq 0
        }
        catch { }
    }

    if (-not $hasGpu) {
        # CPU 版 torch/torchaudio/torchvision 已由 requirements.txt 安装，无需重复
        Write-Host '[WARN] No GPU detected, using CPU PyTorch from requirements.txt' -ForegroundColor Yellow
        return
    }

    # GPU：requirements.txt 中的 torch==2.6.0 被 pip 解析为 CPU 版，需替换为 CUDA 版。
    # --target（便携）模式下 pip 既不会用 CUDA 版覆盖已存在的包，--force-reinstall 也不替换
    # 包文件，必须先手动删除旧 torch 目录与 dist-info，再以 --no-deps 安装（与 cli.ps1 一致）。
    Write-Host
    Write-Host '[INFO] Installing CUDA PyTorch (this may take several minutes)...' -ForegroundColor Cyan

    $isTarget = $script:PipTarget.Count -gt 0
    if ($isTarget) {
        $sitePkgs = $script:PipTarget[1]
        foreach ($item in @('torch', 'torchgen', 'torchaudio', 'torchvision')) {
            $dir = Join-Path $sitePkgs $item
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue }
        }
        Get-ChildItem $sitePkgs -Directory -Filter 'torch*.dist-info' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^torch[a-z]*-\d' } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }

    # requirements.txt 已装好全部依赖，故 --no-deps 仅替换 torch 三件套；
    # venv 模式（非 --target）pip 能正常替换，补 --force-reinstall 确保覆盖。
    $pipArgs = @('-m', 'pip', 'install', '--no-deps')
    if (-not $isTarget) { $pipArgs += '--force-reinstall' }
    $pipArgs += $script:PipTarget + @(
        'torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124', 'torchvision==0.21.0+cu124',
        '--index-url', 'https://download.pytorch.org/whl/cu124'
    )

    & $script:PythonBin @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] PyTorch installation failed' -ForegroundColor Red
    }
    else {
        Write-Host '[INFO] CUDA PyTorch installed' -ForegroundColor Green
    }
}

function Install-Dependencies {
    $reqFile = Join-Path $PSScriptRoot 'requirements.txt'
    if (-not (Test-Path $reqFile)) {
        Write-Host '[WARN] requirements.txt not found, skipping dependency installation' -ForegroundColor Yellow
        return
    }

    Write-Host
    Write-Host '[INFO] Installing project dependencies...' -ForegroundColor Cyan
    $pipArgs = @('-m', 'pip', 'install') + $script:PipTarget + @('-r', $reqFile)
    & $script:PythonBin @pipArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host '[ERROR] Dependency installation failed' -ForegroundColor Red
    }
}

function New-Directories {
    $dirs = @(
        'models\asr\0.6b', 'models\asr\1.7b',
        'models\align\0.6b',
        'models\vad\fsmn', 'models\vad\fsmn-onnx',
        'models\punc\ct-transformer', 'models\punc\ct-transformer-onnx',
        'logs', 'data'
    )
    foreach ($dir in $dirs) {
        $path = Join-Path $PSScriptRoot $dir
        if (-not (Test-Path $path)) {
            New-Item -ItemType Directory -Path $path -Force | Out-Null
        }
    }
    Write-Host '[INFO] Directories ready' -ForegroundColor Green
}

function Select-ModelSource {
    Write-Host
    Write-Host '==========================================' -ForegroundColor Cyan
    Write-Host '  Model Configuration' -ForegroundColor Cyan
    Write-Host '==========================================' -ForegroundColor Cyan
    Write-Host
    Write-Host 'Select model source:'
    Write-Host '  1) ModelScope (recommended for China, faster download)'
    Write-Host '  2) HuggingFace'
    Write-Host '  3) Manual (skip download, prepare model files yourself)'
    Write-Host

    $choice = Read-Host 'Enter choice [1/2/3] (default 1)'
    if (-not $choice) { $choice = '1' }

    switch ($choice) {
        '1' {
            $script:ModelSource = 'modelscope'
            Write-Host '[INFO] Selected ModelScope' -ForegroundColor Green
        }
        '2' {
            $script:ModelSource = 'huggingface'
            Write-Host '[INFO] Selected HuggingFace' -ForegroundColor Green
        }
        '3' {
            $script:ModelSource = 'manual'
            Write-Host '[INFO] Selected manual mode' -ForegroundColor Cyan
            Write-Host
            Write-Host '==========================================' -ForegroundColor Cyan
            Write-Host '  Manual Model Placement Guide' -ForegroundColor Cyan
            Write-Host '==========================================' -ForegroundColor Cyan
            Write-Host
            Write-Host 'Place model files in these directories:'
            Write-Host
            Write-Host "  ASR 0.6B: $($PSScriptRoot)\models\asr\0.6b\"
            Write-Host "  ASR 1.7B: $($PSScriptRoot)\models\asr\1.7b\"
            Write-Host "  Align:    $($PSScriptRoot)\models\align\0.6b\"
            Write-Host "  VAD:      $($PSScriptRoot)\models\vad\fsmn\"
            Write-Host "  Punc:     $($PSScriptRoot)\models\punc\ct-transformer\"
            Write-Host
            Write-Host 'Download from:'
            Write-Host '  https://modelscope.cn/models/Qwen/Qwen3-ASR-0.6B'
            Write-Host '  https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B'
            Write-Host '  https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B'
            Write-Host '  https://huggingface.co/Qwen/Qwen3-ASR-0.6B'
            Write-Host '  https://huggingface.co/Qwen/Qwen3-ASR-1.7B'
            Write-Host '  https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B'
            Write-Host
        }
        default {
            $script:ModelSource = 'modelscope'
            Write-Host '[INFO] Invalid option, using ModelScope' -ForegroundColor Yellow
        }
    }
}

function Show-SetupComplete {
    $src = if ($script:ModelSource) { $script:ModelSource } else { 'modelscope' }
    Write-Host
    Write-Host '==========================================' -ForegroundColor Green
    Write-Host '  Setup Complete' -ForegroundColor Green
    Write-Host '==========================================' -ForegroundColor Green
    Write-Host
    Write-Host 'To start the service:'
    Write-Host "  .\start.ps1 --model-source $src" -ForegroundColor White
    Write-Host
    Write-Host 'Or with custom options:'
    Write-Host "  .\start.ps1 --device cuda --model-size 0.6b --model-source $src" -ForegroundColor White
    Write-Host
    Read-Host 'Press Enter to exit'
}

# ============================================================
# Main Flow
# ============================================================

Write-Host '==========================================' -ForegroundColor Cyan
Write-Host '  Qwen3-ASR Service Environment Setup' -ForegroundColor Cyan
Write-Host '==========================================' -ForegroundColor Cyan
Write-Host

# --- 1. Detect Python environment ---
$portableDetected = (Test-Path 'bin\python\python.exe') -and (Test-Path 'lib\site-packages')

if ($portableDetected) {
    Write-Host '[INFO] Portable Python environment detected, using portable mode' -ForegroundColor Cyan
    $script:PythonMode = 'portable'
    $script:PythonBin = Join-Path $PSScriptRoot 'bin\python\python.exe'
    $script:PipTarget = @('--target', (Join-Path $PSScriptRoot 'lib\site-packages'))
}
else {
    Write-Host
    Write-Host '[INFO] Portable Python environment not detected (bin + lib directories missing)'
    Write-Host
    Write-Host 'Select Python environment setup method:'
    Write-Host '  1) Download portable package (recommended, ready to use)'
    Write-Host '  2) Use system Python + venv'
    Write-Host

    $envChoice = Read-Host 'Enter choice [1/2] (default 1)'
    if (-not $envChoice) { $envChoice = '1' }

    switch ($envChoice) {
        '1' {
            Show-PortableGuide
            exit 0
        }
        '2' {
            Initialize-Venv
        }
        default {
            Write-Host '[INFO] Invalid option, showing portable guide' -ForegroundColor Yellow
            Show-PortableGuide
            exit 0
        }
    }
}

# --- Common setup (portable or venv) ---
Install-PipIfNeeded
# 先按 requirements.txt 装入 CPU 版 torch，再由 Install-PyTorch 在 GPU 环境下替换为 CUDA 版
Install-Dependencies
Install-PyTorch
New-Directories
Select-ModelSource
Show-SetupComplete
