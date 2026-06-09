#Requires -Version 5.1
<#
.SYNOPSIS
    Qwen3-ASR Service Interactive Management Tool (PowerShell)
.DESCRIPTION
    Unified management entry point supporting Docker Compose / Docker / venv modes.
    Reference: cli.sh (v2 version with speaker, stream, task-store features).
#>

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# ============================================================
# Constants
# ============================================================
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = $ScriptDir
$ServiceDir = Join-Path $ScriptDir 'asr-service'
$ComposeFile = Join-Path $ProjectRoot 'docker\docker-compose.yml'
$ComposeFileCpu = Join-Path $ProjectRoot 'docker\docker-compose.cpu.yml'
$ServiceConfig = Join-Path $ServiceDir 'config.yaml'
$ServiceConfigExample = Join-Path $ServiceDir 'config.example.yaml'
$ConfigFile = Join-Path $ServiceDir '.cli_launch_config'
$ImageName = 'lancelrq/qwen3-asr-service'
$ImageTag = 'latest'
$ContainerName = 'qwen3-asr-service'

# ============================================================
# ANSI Colors (ESC = 0x1B)
# ============================================================
$ESC = [char]27
$RED_FG = "$ESC[91m"
$GREEN_FG = "$ESC[92m"
$YELLOW_FG = "$ESC[93m"
$CYAN_FG = "$ESC[96m"
$BOLD_S = "$ESC[1m"
$DIM_S = "$ESC[2m"
$REV_S = "$ESC[7m"
$NC_S = "$ESC[0m"

# ============================================================
# Global State
# ============================================================
$script:HasDocker = $false
$script:HasCompose = $false
$script:ComposeCmd = ''
$script:HasVenv = $false
$script:HasGpu = $false
$script:VenvPythonVersion = ''
$script:HasPortable = $false
$script:PortablePythonVersion = ''
$script:ActiveComposeVariant = 'gpu'

# Launch config (defaults)
$script:Launch = @{
    ModelSize        = 'auto'
    Device           = 'auto'
    ModelSource      = 'modelscope'
    EnableAlign      = 'yes'
    UsePunc          = 'no'
    Web              = 'yes'
    MaxSegment       = '5'
    Host             = '127.0.0.1'
    Port             = '8765'
    ApiKey           = ''
    EnableStream     = 'yes'
    EnableTaskStore  = 'yes'
    EnableSpeaker    = 'no'
    EnableSpeakerDb  = 'no'
    Method           = ''
}

# ============================================================
# Helper Functions
# ============================================================
function Write-Info($msg)  { Write-Host "${CYAN_FG}[INFO]${NC_S} $msg" }
function Write-Ok($msg)    { Write-Host "${GREEN_FG}[OK]${NC_S} $msg" }
function Write-Warn($msg)  { Write-Host "${YELLOW_FG}[WARN]${NC_S} $msg" }
function Write-Err($msg)   { Write-Host "${RED_FG}[ERROR]${NC_S} $msg" }

function Press-AnyKey {
    Write-Host
    Write-Host "${DIM_S}按任意键继续...${NC_S}" -NoNewline
    $null = [Console]::ReadKey($true)
    Write-Host
}

function Confirm-Action($prompt = '确认操作？') {
    Write-Host "${YELLOW_FG}$prompt (y/N): ${NC_S}" -NoNewline
    $answer = Read-Host
    $answer -in @('y', 'Y', 'yes', 'Yes', 'YES')
}

function Read-InputWithDefault($prompt, $default = '') {
    if ($default) {
        Write-Host "$prompt [${DIM_S}$default${NC_S}]: " -NoNewline
    }
    else {
        Write-Host "${prompt}: " -NoNewline
    }
    $answer = Read-Host
    if ($answer) { return $answer } else { return $default }
}

# ============================================================
# Interactive Menu (arrow-key navigation)
# ============================================================
function Show-Menu {
    param(
        [string]$Title,
        [string[]]$Options
    )
    $count = $Options.Count
    $selected = 0
    $firstDraw = $true
    $oldVisible = [Console]::CursorVisible
    [Console]::CursorVisible = $false
    try {
        while ($true) {
            if (-not $firstDraw) {
                $cur = [Console]::CursorTop
                [Console]::SetCursorPosition(0, $cur - ($count + 2))
            }
            $firstDraw = $false

            Write-Host "$ESC[2K${BOLD_S}${CYAN_FG}$Title${NC_S}"
            Write-Host "$ESC[2K"

            for ($i = 0; $i -lt $count; $i++) {
                Write-Host -NoNewline "$ESC[2K"
                if ($i -eq $selected) {
                    Write-Host "  ${REV_S} > $($Options[$i]) ${NC_S}"
                }
                else {
                    Write-Host "    $($Options[$i])"
                }
            }

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'UpArrow'   { $selected = ($selected - 1 + $count) % $count }
                'DownArrow' { $selected = ($selected + 1) % $count }
                'Enter'     { return $selected }
            }
            if ($key.KeyChar -ge '0' -and $key.KeyChar -le '9') {
                $num = [int]::Parse($key.KeyChar.ToString())
                if ($num -eq 0) { return ($count - 1) }
                elseif ($num -le $count) { return ($num - 1) }
            }
        }
    }
    finally {
        [Console]::CursorVisible = $oldVisible
    }
}

# ============================================================
# Environment Detection
# ============================================================
function Check-Prerequisites {
    # Docker
    $script:HasDocker = $null -ne (Get-Command 'docker' -ErrorAction SilentlyContinue)

    if ($script:HasDocker) {
        $script:HasCompose = $false
        try {
            $null = docker compose version 2>$null
            if ($LASTEXITCODE -eq 0) {
                $script:HasCompose = $true
                $script:ComposeCmd = 'docker compose'
            }
        }
        catch { }
        if (-not $script:HasCompose) {
            $dc = Get-Command 'docker-compose' -ErrorAction SilentlyContinue
            if ($dc) {
                $script:HasCompose = $true
                $script:ComposeCmd = 'docker-compose'
            }
        }
    }

    # GPU
    $script:HasGpu = $false
    if (Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue) {
        try {
            $null = nvidia-smi 2>&1
            if ($LASTEXITCODE -eq 0) { $script:HasGpu = $true }
        }
        catch { }
    }

    # Portable Python
    $portableBin = Join-Path $ServiceDir 'bin\python\python.exe'
    $portableLib = Join-Path $ServiceDir 'lib\site-packages'
    if ((Test-Path $portableBin) -and (Test-Path $portableLib)) {
        $script:HasPortable = $true
        try {
            $ver = & $portableBin --version 2>$null
            $script:PortablePythonVersion = ($ver -split ' ')[1]
        }
        catch { $script:PortablePythonVersion = '未知' }
    }

    # Venv
    $venvPython = Join-Path $ServiceDir 'venv\Scripts\python.exe'
    if ((Test-Path (Join-Path $ServiceDir 'venv')) -and (Test-Path $venvPython)) {
        $script:HasVenv = $true
        try {
            $ver = & $venvPython --version 2>$null
            $script:VenvPythonVersion = ($ver -split ' ')[1]
        }
        catch { $script:VenvPythonVersion = '未知' }
    }
}

function Print-StatusSummary {
    Write-Host
    Write-Host "${BOLD_S}环境检测结果：${NC_S}"
    Write-Host "─────────────────────────────────────"

    if ($script:HasDocker) {
        Write-Host "  Docker Engine:      ${GREEN_FG}✔ 已安装${NC_S}"
    }
    else {
        Write-Host "  Docker Engine:      ${RED_FG}✘ 未安装${NC_S}"
    }

    if ($script:HasCompose) {
        $v = if ($script:ComposeCmd -eq 'docker compose') { 'V2' } else { 'V1' }
        Write-Host "  Docker Compose:     ${GREEN_FG}✔ $v${NC_S}"
    }
    else {
        Write-Host "  Docker Compose:     ${RED_FG}✘ 未安装${NC_S}"
    }

    if ($script:HasGpu) {
        Write-Host "  NVIDIA GPU:         ${GREEN_FG}✔ 已检测${NC_S}"
    }
    else {
        Write-Host "  NVIDIA GPU:         ${YELLOW_FG}✘ 未检测${NC_S}"
    }

    if ($script:HasPortable) {
        Write-Host "  便携式 Python:    ${GREEN_FG}✔ Python $($script:PortablePythonVersion)${NC_S}"
    }
    else {
        Write-Host "  便携式 Python:    ${RED_FG}✘ 未安装${NC_S}"
    }

    if ($script:HasVenv) {
        Write-Host "  Python 虚拟环境:    ${GREEN_FG}✔ Python $($script:VenvPythonVersion)${NC_S}"
    }
    else {
        Write-Host "  Python 虚拟环境:    ${RED_FG}✘ 未创建${NC_S}"
    }

    Write-Host "─────────────────────────────────────"
    Write-Host
}

# ============================================================
# Banner
# ============================================================
function Show-Banner {
    Write-Host "${BOLD_S}${CYAN_FG}" -NoNewline
    Write-Host "   ___                    _____          _    ____  ____"
    Write-Host "  / _ \__      _____ _ _ |___ /         / \  / ___||  _ \"
    Write-Host " | | | \ \ /\ / / _ \ '_ \ |_ \  _____ / \___ \| |_) |"
    Write-Host " | |_| |\ V  V /  __/ | | |__) ||___|/ /\ \___) |  _ <"
    Write-Host "  \__\_\ \_/\_/ \___|_| |_|____/      /_/  \_\____/|_| \_\"
    Write-Host "${NC_S}" -NoNewline
    Write-Host "${DIM_S}  Qwen3-ASR Service 管理工具${NC_S}"
    Write-Host
}

# ============================================================
# Docker Management
# ============================================================
function Get-ActiveComposeFile {
    if ($script:ActiveComposeVariant -eq 'cpu') { return $ComposeFileCpu }
    return $ComposeFile
}

function Invoke-Compose {
    param([string[]] $ComposeArgs)
    $file = Get-ActiveComposeFile
    if ($script:ComposeCmd -eq 'docker compose') {
        $allArgs = @('compose', '-f', $file) + $ComposeArgs
        & docker @allArgs
    }
    else {
        $allArgs = @('-f', $file) + $ComposeArgs
        & docker-compose @allArgs
    }
}

function Docker-Pull {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; return }
    Write-Info "拉取镜像 ${ImageName}:${ImageTag} ..."
    Write-Host
    if (docker pull "${ImageName}:${ImageTag}") {
        Write-Host; Write-Ok '镜像拉取完成'
    }
    else { Write-Host; Write-Err '镜像拉取失败' }
    Press-AnyKey
}

function Docker-Build {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; return }
    $buildSh = Join-Path $ProjectRoot 'docker\build.sh'
    if (-not (Test-Path $buildSh)) { Write-Err '未找到 docker/build.sh'; Press-AnyKey; return }
    Write-Info '构建镜像（从 Dockerfile）...'
    Write-Host
    try {
        Push-Location $ProjectRoot
        bash docker/build.sh
        $buildOk = $LASTEXITCODE -eq 0
    }
    finally {
        Pop-Location
    }
    if ($buildOk) { Write-Host; Write-Ok '镜像构建完成' }
    else { Write-Host; Write-Err '镜像构建失败' }
    Press-AnyKey
}

function Docker-Up {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; Press-AnyKey; return }
    # Check already running
    $running = docker ps --format '{{.Names}}' 2>$null | Select-String -Pattern "^$ContainerName$" -SimpleMatch
    if ($running) {
        Write-Warn "容器 $ContainerName 已在运行中，请勿重复启动"
        Press-AnyKey
        return
    }
    Launch-Wizard 'docker'
}

function Docker-Down {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; Press-AnyKey; return }
    $running = docker ps --format '{{.Names}}' 2>$null | Select-String -Pattern "^$ContainerName$" -SimpleMatch
    if (-not $running) {
        Write-Warn "容器 $ContainerName 未在运行"
        Press-AnyKey
        return
    }
    Write-Info "停止容器 $ContainerName ..."
    Write-Host
    if ((docker stop $ContainerName) -and (docker rm $ContainerName)) {
        Write-Host; Write-Ok '容器已停止并移除'
    }
    else { Write-Host; Write-Err '停止失败' }
    Press-AnyKey
}

function Docker-Status {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; Press-AnyKey; return }
    Write-Host
    Write-Host "${BOLD_S}容器状态 [$ContainerName]：${NC_S}"
    Write-Host "─────────────────────────────────────"
    $info = docker ps -a --filter "name=^${ContainerName}$" --format "table {{.Status}}`t{{.Ports}}`t{{.CreatedAt}}" 2>$null
    if (-not $info -or ($info -split "`n").Count -le 1) {
        Write-Host "  ${DIM_S}容器未创建${NC_S}"
    }
    else { Write-Host $info }
    Write-Host "─────────────────────────────────────"
    Press-AnyKey
}

function Docker-Logs {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; Press-AnyKey; return }
    $exists = docker ps -a --format '{{.Names}}' 2>$null | Select-String -Pattern "^$ContainerName$" -SimpleMatch
    if (-not $exists) { Write-Warn "容器 $ContainerName 不存在"; Press-AnyKey; return }
    Write-Info '查看日志（Ctrl+C 返回）...'
    Write-Host
    try { docker logs --tail=50 -f $ContainerName } catch { }
    Press-AnyKey
}

function Docker-Images {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; Press-AnyKey; return }
    Write-Host
    Write-Host "${BOLD_S}本地镜像：${NC_S}"
    docker images $ImageName --format "table {{.Repository}}`t{{.Tag}}`t{{.Size}}`t{{.CreatedSince}}" 2>$null
    Write-Host
    Write-Host "${BOLD_S}所有相关镜像：${NC_S}"
    docker images --filter "reference=${ImageName}*" --format "table {{.Repository}}`t{{.Tag}}`t{{.ID}}`t{{.Size}}`t{{.CreatedSince}}" 2>$null
    Press-AnyKey
}

function Menu-Docker {
    while ($true) {
        Clear-Host
        if (-not $script:HasDocker) { Write-Warn 'Docker 未安装，部分功能不可用'; Write-Host }

        $choice = Show-Menu 'Docker 容器方式（docker run，参数向导）' @(
            '1. 启动容器（参数向导）',
            '2. 停止容器',
            '3. 查看容器状态',
            '4. 查看日志',
            '5. 拉取镜像',
            '6. 构建镜像',
            '7. 查看镜像状态',
            '0. 返回主菜单'
        )

        switch ($choice) {
            0 { Docker-Up }
            1 { Docker-Down }
            2 { Docker-Status }
            3 { Docker-Logs }
            4 { Docker-Pull }
            5 { Docker-Build }
            6 { Docker-Images }
            7 { return }
        }
    }
}

# ============================================================
# Docker Compose Management
# ============================================================
function Ensure-ServiceConfig {
    if (Test-Path $ServiceConfig) { return $true }
    if (-not (Test-Path $ServiceConfigExample)) {
        Write-Err '未找到 config.example.yaml，无法生成 config.yaml'
        return $false
    }
    Write-Info '首次使用：从 config.example.yaml 生成 config.yaml ...'
    Copy-Item $ServiceConfigExample $ServiceConfig
    Write-Ok '已生成 asr-service/config.yaml'
    return $true
}

function Compose-Prereq {
    if (-not $script:HasDocker) { Write-Err 'Docker 未安装'; return $false }
    if (-not $script:HasCompose) { Write-Err 'Docker Compose 未安装'; return $false }
    return $true
}

function Compose-Up {
    if (-not (Compose-Prereq)) { Press-AnyKey; return }
    if (-not (Ensure-ServiceConfig)) { Press-AnyKey; return }
    $file = Get-ActiveComposeFile
    Write-Info "启动容器（$($script:ActiveComposeVariant) · $(Split-Path $file -Leaf) · 配置以 config.yaml 为准）..."
    Write-Host
    Invoke-Compose @('up', '-d')
    if ($LASTEXITCODE -eq 0) {
        Write-Host; Write-Ok '容器已启动'
        Write-Info 'Web UI 默认 http://<host>:<port>/web-ui（host/port 以 config.yaml 为准）'
    }
    else { Write-Host; Write-Err '启动失败' }
    Press-AnyKey
}

function Compose-Down {
    if (-not (Compose-Prereq)) { Press-AnyKey; return }
    Write-Info "停止并移除容器..."
    Write-Host
    Invoke-Compose @('down')
    if ($LASTEXITCODE -eq 0) { Write-Host; Write-Ok '容器已停止并移除' }
    else { Write-Host; Write-Err '停止失败' }
    Press-AnyKey
}

function Compose-Restart {
    if (-not (Compose-Prereq)) { Press-AnyKey; return }
    if (-not (Ensure-ServiceConfig)) { Press-AnyKey; return }
    Write-Info '重启容器...'
    Write-Host
    Invoke-Compose @('down') 2>$null | Out-Null
    Invoke-Compose @('up', '-d')
    if ($LASTEXITCODE -eq 0) { Write-Host; Write-Ok '容器已重启' }
    else { Write-Host; Write-Err '重启失败' }
    Press-AnyKey
}

function Compose-Logs {
    if (-not (Compose-Prereq)) { Press-AnyKey; return }
    Write-Info '实时日志（Ctrl+C 返回菜单）...'
    Write-Host
    try { Invoke-Compose @('logs', '--tail=50', '-f') } catch { }
    Press-AnyKey
}

function Edit-ServiceConfig {
    if (-not (Ensure-ServiceConfig)) { Press-AnyKey; return }
    $editor = $env:EDITOR
    if (-not $editor) {
        if (Get-Command 'code' -ErrorAction SilentlyContinue) { $editor = 'code' }
        elseif (Get-Command 'notepad' -ErrorAction SilentlyContinue) { $editor = 'notepad' }
        else { $editor = 'notepad' }
    }
    Write-Info "使用 $editor 打开 asr-service/config.yaml ..."
    & $editor $ServiceConfig
    Press-AnyKey
}

function Toggle-ComposeVariant {
    $choice = Show-Menu '选择 Compose 编排' @(
        'GPU（docker-compose.yml）',
        'CPU（docker-compose.cpu.yml）'
    )
    switch ($choice) {
        0 { $script:ActiveComposeVariant = 'gpu' }
        1 { $script:ActiveComposeVariant = 'cpu' }
    }
    Write-Ok "已切换为 $($script:ActiveComposeVariant) 编排"
    Press-AnyKey
}

function Menu-Compose {
    while ($true) {
        Clear-Host
        if (-not $script:HasCompose) { Write-Warn 'Docker Compose 未安装，部分功能不可用'; Write-Host }

        $choice = Show-Menu "Docker Compose 管理（当前：$($script:ActiveComposeVariant) · config.yaml 驱动）" @(
            '1. 启动容器（up -d）',
            '2. 停止容器（down）',
            '3. 重启容器',
            '4. 查看日志',
            '5. 编辑 config.yaml 配置',
            '6. 切换 GPU / CPU 编排',
            '0. 返回主菜单'
        )

        switch ($choice) {
            0 { Compose-Up }
            1 { Compose-Down }
            2 { Compose-Restart }
            3 { Compose-Logs }
            4 { Edit-ServiceConfig }
            5 { Toggle-ComposeVariant }
            6 { return }
        }
    }
}

# ============================================================
# Venv Management
# ============================================================
function Venv-InstallOrReinstall {
    $setupPs1 = Join-Path $ServiceDir 'setup.ps1'
    if (-not (Test-Path $setupPs1)) {
        Write-Err '未找到 setup.ps1'
        Press-AnyKey
        return
    }

    if ($script:HasVenv) {
        Write-Warn '检测到已有虚拟环境'
        if (-not (Confirm-Action '将删除现有虚拟环境并重新安装，是否继续？')) {
            Write-Info '已取消'
            return
        }
        Write-Info '删除现有虚拟环境...'
        Remove-Item -Recurse -Force (Join-Path $ServiceDir 'venv') -ErrorAction SilentlyContinue
        $script:HasVenv = $false
        $script:VenvPythonVersion = ''
    }

    Write-Info '运行安装脚本...'
    Write-Host
    try { Push-Location $ServiceDir; & $setupPs1 } finally { Pop-Location }

    # Refresh detection
    $venvPython = Join-Path $ServiceDir 'venv\Scripts\python.exe'
    if ((Test-Path (Join-Path $ServiceDir 'venv')) -and (Test-Path $venvPython)) {
        $script:HasVenv = $true
        try {
            $ver = & $venvPython --version 2>$null
            $script:VenvPythonVersion = ($ver -split ' ')[1]
        }
        catch { $script:VenvPythonVersion = '未知' }
    }
    Press-AnyKey
}

function Venv-Remove {
    if (-not $script:HasVenv) { Write-Warn '虚拟环境未创建'; Press-AnyKey; return }
    if (-not (Confirm-Action '确定要删除虚拟环境？')) { Write-Info '已取消'; return }
    Write-Info '删除虚拟环境...'
    Remove-Item -Recurse -Force (Join-Path $ServiceDir 'venv') -ErrorAction SilentlyContinue
    $script:HasVenv = $false
    $script:VenvPythonVersion = ''
    Write-Ok '虚拟环境已删除'
    Press-AnyKey
}

function Venv-Info {
    Write-Host
    if (-not $script:HasVenv) { Write-Warn '虚拟环境未创建'; Press-AnyKey; return }

    $pythonBin = Join-Path $ServiceDir 'venv\Scripts\python.exe'

    Write-Host "${BOLD_S}虚拟环境信息：${NC_S}"
    Write-Host "─────────────────────────────────────"
    Write-Host "  路径:         $ServiceDir\venv"
    $pyVer = & $pythonBin --version 2>$null
    Write-Host "  Python 版本:  $pyVer"
    $pipVer = & $pythonBin -m pip --version 2>$null
    $pipVerNum = if ($pipVer -match '(\d+\.\d+\.\d+)') { $Matches[1] } else { '未知' }
    Write-Host "  Pip 版本:     $pipVerNum"

    $torchInfo = & $pythonBin -m pip show torch 2>$null
    $torchVer = if ($torchInfo -match 'Version: (.+)') { $Matches[1] } else { '未安装' }
    Write-Host "  PyTorch:      $torchVer"

    $qwenInfo = & $pythonBin -m pip show qwen-asr 2>$null
    $qwenVer = if ($qwenInfo -match 'Version: (.+)') { $Matches[1] } else { '未安装' }
    Write-Host "  qwen-asr:     $qwenVer"

    $pkgList = & $pythonBin -m pip list 2>$null
    $pkgCount = ($pkgList | Measure-Object -Line).Lines - 2
    if ($pkgCount -lt 0) { $pkgCount = 0 }
    Write-Host "  已安装包:     $pkgCount 个"
    Write-Host "─────────────────────────────────────"

    Press-AnyKey
}

function Menu-Venv {
    while ($true) {
        Clear-Host
        if ($script:HasVenv) {
            $choice = Show-Menu 'Venv 虚拟环境方式（本地 start.ps1，参数向导）' @(
                '1. 启动服务（参数向导）',
                '2. 重新安装虚拟环境',
                '3. 卸载删除',
                '4. 检测版本信息',
                '0. 返回主菜单'
            )
            switch ($choice) {
                0 { Venv-Start }
                1 { Venv-InstallOrReinstall }
                2 { Venv-Remove }
                3 { Venv-Info }
                4 { return }
            }
        }
        else {
            $choice = Show-Menu 'Venv 虚拟环境方式' @(
                '1. 安装虚拟环境',
                '0. 返回主菜单'
            )
            switch ($choice) {
                0 { Venv-InstallOrReinstall }
                1 { return }
            }
        }
    }
}

# ============================================================
# Portable Python Management
# ============================================================
function Portable-Info {
    Write-Host
    if (-not $script:HasPortable) { Write-Warn '便携式 Python 未安装'; Press-AnyKey; return }

    $pythonBin = Join-Path $ServiceDir 'bin\python\python.exe'

    Write-Host "${BOLD_S}便携式 Python 信息：${NC_S}"
    Write-Host "─────────────────────────────────────"
    Write-Host "  路径:         $ServiceDir\bin\python"
    $pyVer = & $pythonBin --version 2>$null
    Write-Host "  Python 版本:  $pyVer"
    $pipVer = & $pythonBin -m pip --version 2>$null
    $pipVerNum = if ($pipVer -match '(\d+\.\d+\.\d+)') { $Matches[1] } else { '未知' }
    Write-Host "  Pip 版本:     $pipVerNum"

    $torchInfo = & $pythonBin -m pip show torch 2>$null
    $torchVer = if ($torchInfo -match 'Version: (.+)') { $Matches[1] } else { '未安装' }
    Write-Host "  PyTorch:      $torchVer"

    $qwenInfo = & $pythonBin -m pip show qwen-asr 2>$null
    $qwenVer = if ($qwenInfo -match 'Version: (.+)') { $Matches[1] } else { '未安装' }
    Write-Host "  qwen-asr:     $qwenVer"

    $pkgList = & $pythonBin -m pip list 2>$null
    $pkgCount = ($pkgList | Measure-Object -Line).Lines - 2
    if ($pkgCount -lt 0) { $pkgCount = 0 }
    Write-Host "  已安装包:     $pkgCount 个"
    Write-Host "─────────────────────────────────────"

    Press-AnyKey
}

function Portable-Guide {
    Write-Host
    Write-Info '请从以下地址下载便携式 Python 包：'
    Write-Host
    Write-Host '  百度网盘: https://pan.baidu.com/s/1ahqW1mxIoNJTG2k6b4PkkA?pwd=6cth'
    Write-Host '  提取码: 6cth'
    Write-Host
    Write-Host '  下载文件: qwen3-asr-service-python3.12-pytorch2.6-cu124-bin.7z'
    Write-Host
    Write-Info '解压后，将 bin 和 lib 目录放入 asr-service 目录：'
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
    Write-Info '完成后运行 setup.ps1 进行环境配置，然后通过本菜单启动服务'
    Write-Host
    Press-AnyKey
}

function Portable-Setup {
    $setupPs1 = Join-Path $ServiceDir 'setup.ps1'
    if (-not (Test-Path $setupPs1)) {
        Write-Err '未找到 setup.ps1'
        Press-AnyKey
        return
    }

    Write-Info '运行安装脚本...'
    Write-Host
    try { Push-Location $ServiceDir; & $setupPs1 } finally { Pop-Location }

    # Refresh detection
    $portableBin = Join-Path $ServiceDir 'bin\python\python.exe'
    $portableLib = Join-Path $ServiceDir 'lib\site-packages'
    if ((Test-Path $portableBin) -and (Test-Path $portableLib)) {
        $script:HasPortable = $true
        try {
            $ver = & $portableBin --version 2>$null
            $script:PortablePythonVersion = ($ver -split ' ')[1]
        }
        catch { $script:PortablePythonVersion = '未知' }
    }
    Press-AnyKey
}

function Portable-Remove {
    if (-not $script:HasPortable) { Write-Warn '便携式 Python 未安装'; Press-AnyKey; return }
    if (-not (Confirm-Action '确定要删除便携式 Python 环境？（将删除 bin 和 lib 目录）')) { Write-Info '已取消'; return }
    Write-Info '删除便携式 Python 环境...'
    Remove-Item -Recurse -Force (Join-Path $ServiceDir 'bin') -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force (Join-Path $ServiceDir 'lib') -ErrorAction SilentlyContinue
    $script:HasPortable = $false
    $script:PortablePythonVersion = ''
    Write-Ok '便携式 Python 环境已删除'
    Press-AnyKey
}

function Portable-UpdateDeps {
    if (-not $script:HasPortable) { Write-Warn '便携式 Python 未安装'; Press-AnyKey; return }

    $pythonBin = Join-Path $ServiceDir 'bin\python\python.exe'
    $sitePkgs = Join-Path $ServiceDir 'lib\site-packages'
    $pipTarget = @('--target', $sitePkgs)

    # Step 1: Upgrade pip
    Write-Host
    Write-Info '升级 pip...'
    & $pythonBin -m pip install --upgrade pip @pipTarget 2>$null
    Write-Ok 'pip 已升级'

    # Step 2: Detect GPU for PyTorch
    Write-Host
    Write-Info '检测 NVIDIA GPU...'
    $hasGpu = $false
    if (Get-Command 'nvidia-smi' -ErrorAction SilentlyContinue) {
        try {
            $null = nvidia-smi 2>&1
            if ($LASTEXITCODE -eq 0) { $hasGpu = $true }
        }
        catch { }
    }

    if ($hasGpu) {
        Write-Ok 'NVIDIA GPU 已检测，将安装 CUDA 版 PyTorch'
    }
    else {
        Write-Warn '未检测到 GPU，将安装 CPU 版 PyTorch'
    }

    # Step 3: Update dependencies from requirements.txt first
    #    (includes CPU torch — will be overwritten in step 4 if GPU)
    $reqFile = Join-Path $ServiceDir 'requirements.txt'
    if (Test-Path $reqFile) {
        Write-Host
        Write-Info '更新项目依赖...'
        $depArgs = @('-m', 'pip', 'install', '--upgrade') + $pipTarget + @('-r', $reqFile)
        & $pythonBin @depArgs
        if ($LASTEXITCODE -eq 0) { Write-Ok '项目依赖已更新' }
        else { Write-Err '项目依赖更新失败（部分包可能不兼容）' }
    }
    else {
        Write-Warn '未找到 requirements.txt，跳过项目依赖更新'
    }

    # Step 4: Force reinstall CUDA PyTorch (overwrite CPU version from requirements.txt)
    #    requirements.txt 中的 torch==2.6.0 会被 pip 解析为 CPU 版。
    #    --target 模式下 pip uninstall 不支持 --target，--force-reinstall 也不替换包文件，
    #    必须手动删除旧的 torch 目录和 dist-info，再重新安装。
    if ($hasGpu) {
        Write-Host
        Write-Info '清理旧版 PyTorch...'
        $torchItems = @('torch', 'torchgen', 'torchaudio', 'torchvision')
        foreach ($item in $torchItems) {
            $dir = Join-Path $sitePkgs $item
            if (Test-Path $dir) { Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue }
        }
        # Remove all torch dist-info directories (may have multiple versions)
        Get-ChildItem $sitePkgs -Directory -Filter 'torch*.dist-info' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^torch(-[a-z]+)?-\d' } |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

        Write-Info '安装 CUDA 版 PyTorch...'
        $cudaArgs = @('-m', 'pip', 'install', '--no-deps') + $pipTarget + @(
            'torch==2.6.0+cu124', 'torchaudio==2.6.0+cu124', 'torchvision==0.21.0+cu124',
            '--index-url', 'https://download.pytorch.org/whl/cu124'
        )
        & $pythonBin @cudaArgs
        if ($LASTEXITCODE -eq 0) { Write-Ok 'CUDA 版 PyTorch 已安装' }
        else { Write-Err 'CUDA 版 PyTorch 安装失败' }
    }

    Write-Host
    Write-Ok '依赖更新完成'
    Press-AnyKey
}

function Portable-Start {
    if (-not $script:HasPortable) {
        Write-Err '便携式 Python 未安装，请先下载并配置'
        Press-AnyKey
        return
    }
    Launch-Wizard 'portable'
}

function Menu-Portable {
    while ($true) {
        Clear-Host
        if ($script:HasPortable) {
            $choice = Show-Menu '便携式 Python 方式（本地 start.ps1，参数向导）' @(
                '1. 启动服务（参数向导）',
                '2. 更新依赖（pip + PyTorch + 项目依赖）',
                '3. 运行环境配置（setup.ps1）',
                '4. 卸载删除',
                '5. 检测版本信息',
                '6. 查看下载指南',
                '0. 返回主菜单'
            )
            switch ($choice) {
                0 { Portable-Start }
                1 { Portable-UpdateDeps }
                2 { Portable-Setup }
                3 { Portable-Remove }
                4 { Portable-Info }
                5 { Portable-Guide }
                6 { return }
            }
        }
        else {
            $choice = Show-Menu '便携式 Python 方式' @(
                '1. 查看下载指南',
                '2. 运行环境配置（setup.ps1）',
                '0. 返回主菜单'
            )
            switch ($choice) {
                0 { Portable-Guide }
                1 { Portable-Setup }
                2 { return }
            }
        }
    }
}

# ============================================================
# Launch Config Management
# ============================================================
function Set-DefaultConfig {
    $script:Launch = @{
        ModelSize        = 'auto'
        Device           = 'auto'
        ModelSource      = 'modelscope'
        EnableAlign      = 'yes'
        UsePunc          = 'no'
        Web              = 'yes'
        MaxSegment       = '5'
        Host             = '127.0.0.1'
        Port             = '8765'
        ApiKey           = ''
        EnableStream     = 'yes'
        EnableTaskStore  = 'yes'
        EnableSpeaker    = 'no'
        EnableSpeakerDb  = 'no'
        Method           = ''
    }
}

function Import-LaunchConfig {
    if (-not (Test-Path $ConfigFile)) { return $false }
    $lines = Get-Content $ConfigFile -Encoding UTF8
    foreach ($line in $lines) {
        if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
        if ($line -match '^(\w+)\s*=\s*"?(.*?)"?\s*$') {
            $key = $Matches[1]
            $val = $Matches[2]
            # Map CLI-style keys to Launch hashtable keys
            $map = @{
                'LAUNCH_MODEL_SIZE'        = 'ModelSize'
                'LAUNCH_DEVICE'            = 'Device'
                'LAUNCH_MODEL_SOURCE'      = 'ModelSource'
                'LAUNCH_ENABLE_ALIGN'      = 'EnableAlign'
                'LAUNCH_USE_PUNC'          = 'UsePunc'
                'LAUNCH_WEB'               = 'Web'
                'LAUNCH_MAX_SEGMENT'       = 'MaxSegment'
                'LAUNCH_HOST'              = 'Host'
                'LAUNCH_PORT'              = 'Port'
                'LAUNCH_API_KEY'           = 'ApiKey'
                'LAUNCH_ENABLE_STREAM'     = 'EnableStream'
                'LAUNCH_ENABLE_TASK_STORE' = 'EnableTaskStore'
                'LAUNCH_ENABLE_SPEAKER'    = 'EnableSpeaker'
                'LAUNCH_ENABLE_SPEAKER_DB' = 'EnableSpeakerDb'
                'LAUNCH_METHOD'            = 'Method'
            }
            if ($map.ContainsKey($key)) {
                $script:Launch[$map[$key]] = $val
            }
        }
    }
    return $true
}

function Export-LaunchConfig {
    $content = @(
        '# Qwen3-ASR CLI 启动配置'
        '# 由 cli.ps1 自动生成，可手动编辑'
        "LAUNCH_MODEL_SIZE=`"$($script:Launch.ModelSize)`""
        "LAUNCH_DEVICE=`"$($script:Launch.Device)`""
        "LAUNCH_MODEL_SOURCE=`"$($script:Launch.ModelSource)`""
        "LAUNCH_ENABLE_ALIGN=`"$($script:Launch.EnableAlign)`""
        "LAUNCH_USE_PUNC=`"$($script:Launch.UsePunc)`""
        "LAUNCH_WEB=`"$($script:Launch.Web)`""
        "LAUNCH_MAX_SEGMENT=`"$($script:Launch.MaxSegment)`""
        "LAUNCH_HOST=`"$($script:Launch.Host)`""
        "LAUNCH_PORT=`"$($script:Launch.Port)`""
        "LAUNCH_API_KEY=`"$($script:Launch.ApiKey)`""
        "LAUNCH_ENABLE_STREAM=`"$($script:Launch.EnableStream)`""
        "LAUNCH_ENABLE_TASK_STORE=`"$($script:Launch.EnableTaskStore)`""
        "LAUNCH_ENABLE_SPEAKER=`"$($script:Launch.EnableSpeaker)`""
        "LAUNCH_ENABLE_SPEAKER_DB=`"$($script:Launch.EnableSpeakerDb)`""
        "LAUNCH_METHOD=`"$($script:Launch.Method)`""
    )
    $content | Set-Content $ConfigFile -Encoding UTF8
}

function Print-ConfigSummary {
    Write-Host "${BOLD_S}当前启动配置：${NC_S}"
    Write-Host "─────────────────────────────────────"
    Write-Host "  模型大小:     $($script:Launch.ModelSize)"
    Write-Host "  运行设备:     $($script:Launch.Device)"
    Write-Host "  模型下载源:   $($script:Launch.ModelSource)"
    Write-Host "  对齐模型:     $(if ($script:Launch.EnableAlign -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  标点恢复:     $(if ($script:Launch.UsePunc -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  Web UI:       $(if ($script:Launch.Web -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  最大切片时长: $($script:Launch.MaxSegment) 秒"
    Write-Host "  监听地址:     $($script:Launch.Host)"
    Write-Host "  监听端口:     $($script:Launch.Port)"
    Write-Host "  API 密钥:     $(if ($script:Launch.ApiKey) { '已设置' } else { '未设置（无需认证）' })"
    Write-Host "  实时转写:     $(if ($script:Launch.EnableStream -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  任务持久化:   $(if ($script:Launch.EnableTaskStore -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  说话人分离:   $(if ($script:Launch.EnableSpeaker -eq 'yes') { '启用' } else { '禁用' })"
    Write-Host "  声纹库:       $(if ($script:Launch.EnableSpeakerDb -eq 'yes') { '启用' } else { '禁用' })"
    if ($script:Launch.Method) {
        Write-Host "  启动方式:     $($script:Launch.Method)"
    }
    Write-Host "─────────────────────────────────────"
}

# ============================================================
# Config Panel (interactive, arrow-key navigation)
# ============================================================
function Toggle-Bool($key) {
    if ($script:Launch[$key] -eq 'yes') { $script:Launch[$key] = 'no' }
    else { $script:Launch[$key] = 'yes' }
}

function Edit-ModelSize {
    $choice = Show-Menu '选择模型大小' @(
        'auto (根据显存自动选择)',
        '0.6b (轻量，显存需求低)',
        '1.7b (完整，效果更好)'
    )
    $script:Launch.ModelSize = @('auto', '0.6b', '1.7b')[$choice]
}

function Edit-Device {
    $choice = Show-Menu '选择运行设备' @(
        'auto (自动检测)',
        'cuda (GPU)',
        'cpu'
    )
    $script:Launch.Device = @('auto', 'cuda', 'cpu')[$choice]
}

function Edit-ModelSource {
    $choice = Show-Menu '选择模型下载源' @(
        'modelscope (国内推荐)',
        'huggingface (国外)'
    )
    $script:Launch.ModelSource = @('modelscope', 'huggingface')[$choice]
}

function Show-ConfigPanel($method) {
    $startLabel = switch ($method) {
        'docker'   { '▶ 启动 Docker 容器（按当前配置）' }
        'portable' { '▶ 启动服务（便携式 Python，按当前配置）' }
        default    { '▶ 启动服务（按当前配置）' }
    }

    # Panel rows: key → display logic
    $panelKeys = @(
        @{ Name = 'start';     Label = $startLabel;         Value = '' }
        @{ Name = 'model_size'; Label = '模型大小      ';    Value = { $script:Launch.ModelSize } }
        @{ Name = 'device';     Label = '运行设备      ';    Value = { $script:Launch.Device } }
        @{ Name = 'model_source'; Label = '模型下载源    ';  Value = { $script:Launch.ModelSource } }
        @{ Name = 'align';      Label = '对齐模型      ';    Value = { if ($script:Launch.EnableAlign -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'punc';       Label = '标点恢复      ';    Value = { if ($script:Launch.UsePunc -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'web';        Label = 'Web UI        ';   Value = { if ($script:Launch.Web -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'max_segment'; Label = '最大切片时长  ';   Value = { "$($script:Launch.MaxSegment) 秒" } }
        @{ Name = 'host';       Label = '监听地址      ';    Value = { $script:Launch.Host } }
        @{ Name = 'port';       Label = '监听端口      ';    Value = { $script:Launch.Port } }
        @{ Name = 'api_key';    Label = 'API 密钥      ';    Value = { if ($script:Launch.ApiKey) { '已设置' } else { '未设置' } } }
        @{ Name = 'stream';     Label = '实时转写      ';    Value = { if ($script:Launch.EnableStream -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'task_store'; Label = '任务持久化    ';    Value = { if ($script:Launch.EnableTaskStore -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'speaker';    Label = '说话人分离    ';    Value = { if ($script:Launch.EnableSpeaker -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'speaker_db'; Label = '声纹库        ';    Value = { if ($script:Launch.EnableSpeakerDb -eq 'yes') { '启用' } else { '禁用' } } }
        @{ Name = 'back';       Label = '返回（不启动）';    Value = '' }
    )

    $nrows = $panelKeys.Count
    $selected = 0
    $firstDraw = $true
    $hint = ''
    $oldVisible = [Console]::CursorVisible

    [Console]::CursorVisible = $false
    Clear-Host

    try {
        while ($true) {
            # Redraw
            if (-not $firstDraw) {
                $cur = [Console]::CursorTop
                [Console]::SetCursorPosition(0, $cur - ($nrows + 3))
            }
            $firstDraw = $false

            Write-Host "$ESC[2K${BOLD_S}${CYAN_FG}配置启动参数${NC_S} ${DIM_S}· ↑↓ 选择 · 空格/回车 修改 · 选「启动」运行${NC_S}"
            Write-Host "$ESC[2K"

            for ($i = 0; $i -lt $nrows; $i++) {
                $row = $panelKeys[$i]
                $val = if ($row.Value -is [scriptblock]) { & $row.Value } else { $row.Value }
                if ($val) { $line = "$($row.Label): $val" } else { $line = $row.Label }
                Write-Host -NoNewline "$ESC[2K"
                if ($i -eq $selected) {
                    Write-Host "  ${REV_S} $line ${NC_S}"
                }
                else {
                    Write-Host "    $line"
                }
            }

            Write-Host -NoNewline "$ESC[2K"
            Write-Host "  ${DIM_S}$hint${NC_S}"
            $hint = ''

            $key = [Console]::ReadKey($true)
            switch ($key.Key) {
                'UpArrow'   { $selected = ($selected - 1 + $nrows) % $nrows }
                'DownArrow' { $selected = ($selected + 1) % $nrows }
                'Enter'     { # fall through to action
                }
                default {
                    if ($key.KeyChar -eq ' ') {
                        # Space: same as Enter for toggles
                    }
                    else { continue }
                }
            }

            # Handle Enter or Space
            if ($key.Key -eq 'Enter' -or $key.KeyChar -eq ' ') {
                $action = $panelKeys[$selected].Name

                switch ($action) {
                    'start' {
                        [Console]::CursorVisible = $oldVisible
                        Export-LaunchConfig
                        Write-Host; Print-ConfigSummary; Write-Host
                        switch ($method) {
                            'docker'   { Launch-ViaDocker }
                            'venv'     { Launch-ViaVenv }
                            'portable' { Launch-ViaPortable }
                        }
                        return
                    }
                    'back' {
                        [Console]::CursorVisible = $oldVisible
                        return
                    }
                    'align'      { Toggle-Bool 'EnableAlign'; Export-LaunchConfig }
                    'punc'       { Toggle-Bool 'UsePunc'; Export-LaunchConfig }
                    'web'        { Toggle-Bool 'Web'; Export-LaunchConfig }
                    'stream'     { Toggle-Bool 'EnableStream'; Export-LaunchConfig }
                    'task_store' { Toggle-Bool 'EnableTaskStore'; Export-LaunchConfig }
                    'speaker' {
                        Toggle-Bool 'EnableSpeaker'
                        if ($script:Launch.EnableSpeaker -ne 'yes' -and $script:Launch.EnableSpeakerDb -eq 'yes') {
                            $script:Launch.EnableSpeakerDb = 'no'
                            $hint = '声纹库依赖说话人分离，已一并关闭'
                        }
                        Export-LaunchConfig
                    }
                    'speaker_db' {
                        Toggle-Bool 'EnableSpeakerDb'
                        if ($script:Launch.EnableSpeakerDb -eq 'yes') {
                            if ($script:Launch.EnableSpeaker -ne 'yes') {
                                $script:Launch.EnableSpeaker = 'yes'
                                $hint = '已自动开启说话人分离（声纹库依赖）'
                            }
                            if (-not $script:Launch.ApiKey) {
                                $hint += if ($hint) { '；' } else { '' }
                                $hint += '声纹库需配置 API 密钥，否则启动会被拒绝'
                            }
                        }
                        Export-LaunchConfig
                    }
                    default {
                        # Value-type items: enter sub-editor
                        [Console]::CursorVisible = $oldVisible
                        Write-Host
                        switch ($action) {
                            'model_size'   { Edit-ModelSize }
                            'device'       { Edit-Device }
                            'model_source' { Edit-ModelSource }
                            'max_segment'  { $script:Launch.MaxSegment = Read-InputWithDefault 'VAD 切片合并最大时长（秒）' $script:Launch.MaxSegment }
                            'host'         { $script:Launch.Host = Read-InputWithDefault '监听地址' $script:Launch.Host }
                            'port'         { $script:Launch.Port = Read-InputWithDefault '监听端口' $script:Launch.Port }
                            'api_key'      { $script:Launch.ApiKey = Read-InputWithDefault 'API 密钥（留空则不启用认证）' $script:Launch.ApiKey }
                        }
                        Export-LaunchConfig
                        # Redraw full panel
                        [Console]::CursorVisible = $false
                        Clear-Host
                        $firstDraw = $true
                    }
                }
            }
        }
    }
    finally {
        [Console]::CursorVisible = $oldVisible
    }
}

# ============================================================
# Build Launch Args
# ============================================================
function Build-LaunchArgs {
    $args = @()
    if ($script:Launch.ModelSize -ne 'auto') { $args += @('--model-size', $script:Launch.ModelSize) }
    $args += @('--device', $script:Launch.Device)
    $args += @('--model-source', $script:Launch.ModelSource)

    if ($script:Launch.EnableAlign -eq 'yes') { $args += '--enable-align' } else { $args += '--no-align' }
    if ($script:Launch.UsePunc -eq 'yes') { $args += '--use-punc' }
    if ($script:Launch.Web -eq 'yes') { $args += '--web' }

    $args += @('--max-segment', $script:Launch.MaxSegment)
    $args += @('--host', $script:Launch.Host)
    $args += @('--port', $script:Launch.Port)

    if ($script:Launch.ApiKey) { $args += @('--api-key', $script:Launch.ApiKey) }

    # v2 features
    if ($script:Launch.EnableStream -eq 'yes') { $args += '--enable-stream' } else { $args += '--no-stream' }
    if ($script:Launch.EnableTaskStore -eq 'yes') { $args += '--enable-task-store' } else { $args += '--no-task-store' }
    if ($script:Launch.EnableSpeaker -eq 'yes') { $args += '--enable-speaker' } else { $args += '--no-speaker' }
    if ($script:Launch.EnableSpeakerDb -eq 'yes') { $args += '--enable-speaker-db' } else { $args += '--no-speaker-db' }

    return $args
}

function Launch-ViaVenv {
    if (-not $script:HasVenv) {
        Write-Err '虚拟环境未创建，请先安装'
        Press-AnyKey
        return
    }

    $launchArgs = Build-LaunchArgs
    $startPs1 = Join-Path $ServiceDir 'start.ps1'

    Write-Host
    Write-Host "${BOLD_S}启动命令：${NC_S}"
    Write-Host "  powershell -File $startPs1 $($launchArgs -join ' ')"
    Write-Host

    try { Push-Location $ServiceDir; & $startPs1 @launchArgs } finally { Pop-Location }
}

function Launch-ViaPortable {
    if (-not $script:HasPortable) {
        Write-Err '便携式 Python 未安装，请先下载并配置'
        Press-AnyKey
        return
    }

    $launchArgs = Build-LaunchArgs
    $startPs1 = Join-Path $ServiceDir 'start.ps1'

    Write-Host
    Write-Host "${BOLD_S}启动命令：${NC_S}"
    Write-Host "  powershell -File $startPs1 $($launchArgs -join ' ')"
    Write-Host

    try { Push-Location $ServiceDir; & $startPs1 @launchArgs } finally { Pop-Location }
}

function Launch-ViaDocker {
    if (-not $script:HasDocker) {
        Write-Err 'Docker 未安装'
        Press-AnyKey
        return
    }

    $launchArgs = Build-LaunchArgs

    # Check existing container
    $existing = docker ps -a --format '{{.Names}}' 2>$null | Select-String -Pattern "^$ContainerName$" -SimpleMatch
    if ($existing) {
        Write-Warn "容器 $ContainerName 已存在"
        $choice = Show-Menu '如何处理？' @(
            '停止并删除旧容器，重新启动',
            '取消启动'
        )
        switch ($choice) {
            0 {
                Write-Info '停止并删除旧容器...'
                docker stop $ContainerName 2>$null | Out-Null
                docker rm $ContainerName 2>$null | Out-Null
            }
            1 { Write-Info '已取消'; return }
        }
    }

    # Build docker run args
    $dockerArgs = @('run', '-d')
    if ($script:HasGpu) { $dockerArgs += @('--gpus', 'all') }
    $dockerArgs += @('-p', "$($script:Launch.Port):$($script:Launch.Port)")
    $dockerArgs += @('-v', "$($ServiceDir)\models:/app/models")
    $dockerArgs += @('-v', "$($ServiceDir)\logs:/app/logs")
    $dockerArgs += @('-v', "$($ServiceDir)\data:/app/data")
    if ($script:Launch.ApiKey) {
        $dockerArgs += @('-e', "ASR_API_KEY=$($script:Launch.ApiKey)")
    }
    $dockerArgs += @('--name', $ContainerName)
    $dockerArgs += "${ImageName}:${ImageTag}"

    # Replace host for docker (must be 0.0.0.0)
    $finalLaunchArgs = @()
    $skip = $false
    for ($i = 0; $i -lt $launchArgs.Count; $i++) {
        if ($skip) { $skip = $false; continue }
        if ($launchArgs[$i] -eq '--host' -and $i + 1 -lt $launchArgs.Count) {
            $finalLaunchArgs += @('--host', '0.0.0.0')
            $skip = $true
        }
        else {
            $finalLaunchArgs += $launchArgs[$i]
        }
    }
    $dockerArgs += $finalLaunchArgs

    $cmdDisplay = "docker $($dockerArgs -join ' ')"
    Write-Host
    Write-Host "${BOLD_S}启动命令：${NC_S}"
    Write-Host "  $cmdDisplay"
    Write-Host

    & docker @dockerArgs
    if ($LASTEXITCODE -eq 0) {
        Write-Host
        Write-Ok '容器已启动'
        Write-Info "使用 docker logs -f $ContainerName 查看日志"
    }
    else {
        Write-Host
        Write-Err '启动失败'
    }
    Press-AnyKey
}

function Launch-Wizard($method) {
    Write-Host
    Set-DefaultConfig
    Import-LaunchConfig | Out-Null
    $script:Launch.Method = $method
    Show-ConfigPanel $method
}

function Venv-Start {
    if (-not $script:HasVenv) {
        Write-Err '虚拟环境未创建，请先安装'
        Press-AnyKey
        return
    }
    Launch-Wizard 'venv'
}

# ============================================================
# .gitignore maintenance
# ============================================================
function Ensure-Gitignore {
    $gitignore = Join-Path $ServiceDir '.gitignore'
    if (Test-Path $gitignore) {
        $content = Get-Content $gitignore -Raw
        if ($content -notmatch '\.cli_launch_config') {
            Add-Content $gitignore '.cli_launch_config'
        }
    }
}

# ============================================================
# Main Menu
# ============================================================
function Menu-Main {
    while ($true) {
        Clear-Host
        Show-Banner
        Print-StatusSummary

        $choice = Show-Menu '请选择启动方式' @(
            '1. Docker Compose 方式（推荐）',
            '2. Docker 容器方式',
            '3. 便携式 Python 方式',
            '4. Venv 虚拟环境方式',
            '0. 退出'
        )

        switch ($choice) {
            0 { Menu-Compose }
            1 { Menu-Docker }
            2 { Menu-Portable }
            3 { Menu-Venv }
            4 {
                Clear-Host
                Write-Info '再见！'
                exit 0
            }
        }
    }
}

# ============================================================
# Entry Point
# ============================================================
Clear-Host
Show-Banner
Check-Prerequisites
Print-StatusSummary
Ensure-Gitignore
Menu-Main
