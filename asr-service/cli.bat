@echo off
chcp 65001 >nul 2>&1
setlocal enabledelayedexpansion

:: Qwen3-ASR Service Windows 交互式管理脚本
:: 支持便携版 / venv 两种运行方式

cd /d "%~dp0"
set "SCRIPT_DIR=%~dp0"
set "CONFIG_FILE=%SCRIPT_DIR%.cli_launch_config"

:: ANSI 颜色（Windows 10+ 支持）
set "RED=[91m"
set "GREEN=[92m"
set "YELLOW=[93m"
set "CYAN=[96m"
set "BOLD=[1m"
set "DIM=[2m"
set "NC=[0m"

:: 全局状态
set "HAS_PORTABLE=0"
set "HAS_VENV=0"
set "HAS_GPU=0"
set "PORTABLE_PYTHON_VER="
set "VENV_PYTHON_VER="

goto :main

:: ============================================================
:: 辅助函数
:: ============================================================
:info_msg
echo %CYAN%[INFO]%NC% %~1
goto :eof

:success_msg
echo %GREEN%[OK]%NC% %~1
goto :eof

:warn_msg
echo %YELLOW%[WARN]%NC% %~1
goto :eof

:error_msg
echo %RED%[ERROR]%NC% %~1
goto :eof

:press_any_key
echo.
echo %DIM%按任意键继续...%NC%
pause >nul
goto :eof

:: ============================================================
:: 环境检测
:: ============================================================
:check_prerequisites
:: 便携版
set "HAS_PORTABLE=0"
if exist "bin\python\python.exe" (
    if exist "lib\site-packages" (
        set "HAS_PORTABLE=1"
        for /f "tokens=*" %%v in ('bin\python\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2^>nul') do set "PORTABLE_PYTHON_VER=%%v"
    )
)

:: venv
set "HAS_VENV=0"
if exist "venv\Scripts\python.exe" (
    set "HAS_VENV=1"
    for /f "tokens=*" %%v in ('venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2^>nul') do set "VENV_PYTHON_VER=%%v"
)

:: GPU
set "HAS_GPU=0"
nvidia-smi >nul 2>&1
if !errorlevel!==0 set "HAS_GPU=1"

goto :eof

:: ============================================================
:: 状态摘要
:: ============================================================
:print_status
echo.
echo %BOLD%环境检测结果：%NC%
echo -----------------------------------------
if "!HAS_PORTABLE!"=="1" (
    echo   便携版 Python:    %GREEN%+ 已安装 ^(Python !PORTABLE_PYTHON_VER!^)%NC%
) else (
    echo   便携版 Python:    %RED%x 未安装%NC%
)
if "!HAS_VENV!"=="1" (
    echo   虚拟环境 venv:    %GREEN%+ 已安装 ^(Python !VENV_PYTHON_VER!^)%NC%
) else (
    echo   虚拟环境 venv:    %RED%x 未创建%NC%
)
if "!HAS_GPU!"=="1" (
    echo   NVIDIA GPU:       %GREEN%+ 已检测%NC%
) else (
    echo   NVIDIA GPU:       %YELLOW%x 未检测%NC%
)
echo -----------------------------------------
echo.
goto :eof

:: ============================================================
:: Banner
:: ============================================================
:show_banner
echo %BOLD%%CYAN%
echo    ___                    _____          _    ____  ____
echo   / _ \__      _____ _ _ ^|___ /         / \  / ___^|^|  _ \
echo  ^| ^| ^| \ \ /\ / / _ \ '_ \ ^|_ \  _____^|  / \_\___ \^| ^|_^) ^|
echo  ^| ^|_^| ^|\ V  V /  __/ ^| ^| ^|__^) ^|^|___^| / /\ \___^) ^|  _ ^<
echo   \__\_\ \_/\_/ \___^|_^| ^|_^|____/      /_/  \_\____/^|_^| \_\
echo %NC%
echo %DIM%  Qwen3-ASR Service Windows 管理工具%NC%
echo.
goto :eof

:: ============================================================
:: 主入口
:: ============================================================
:main
cls
call :show_banner
call :check_prerequisites
call :print_status

:menu_main
echo %BOLD%%CYAN%主菜单%NC%
echo.
echo   1. Python 环境管理
echo   2. 启动服务
echo   0. 退出
echo.
set /p "CHOICE=请选择 [0-2]: "
if "!CHOICE!"=="1" goto :menu_env
if "!CHOICE!"=="2" goto :menu_start_service
if "!CHOICE!"=="0" goto :quit
goto :main

:: ============================================================
:: Python 环境管理
:: ============================================================
:menu_env
cls
call :show_banner
call :print_status

echo %BOLD%%CYAN%Python 环境管理%NC%
echo.

:: 根据状态动态构建菜单
set "ENV_ITEMS=0"

if "!HAS_PORTABLE!"=="1" (
    echo   1. 查看便携版信息
    set "ENV_OPT1=portable_info"
    set /a ENV_ITEMS+=1
) else (
    echo   1. 安装便携版（查看下载指引）
    set "ENV_OPT1=portable_guide"
    set /a ENV_ITEMS+=1
)

if "!HAS_VENV!"=="1" (
    echo   2. 重新安装 venv
    echo   3. 卸载 venv
    echo   4. 查看 venv 信息
    set "ENV_OPT2=venv_reinstall"
    set "ENV_OPT3=venv_remove"
    set "ENV_OPT4=venv_info"
) else (
    echo   2. 安装 venv（运行 setup.bat）
    set "ENV_OPT2=venv_install"
    set "ENV_OPT3="
    set "ENV_OPT4="
)

echo   0. 返回主菜单
echo.
set /p "CHOICE=请选择: "

if "!CHOICE!"=="1" goto :env_!ENV_OPT1!
if "!CHOICE!"=="2" goto :env_!ENV_OPT2!
if "!HAS_VENV!"=="1" (
    if "!CHOICE!"=="3" goto :env_!ENV_OPT3!
    if "!CHOICE!"=="4" goto :env_!ENV_OPT4!
)
if "!CHOICE!"=="0" goto :main
goto :menu_env

:: --- 便携版下载指引 ---
:env_portable_guide
cls
echo.
call :info_msg "便携版 Python 环境下载指引"
echo.
echo   请前往以下地址下载便携包：
echo.
echo   百度网盘: https://pan.baidu.com/s/1ahqW1mxIoNJTG2k6b4PkkA?pwd=6cth
echo   提取码: 6cth
echo.
echo   下载文件: qwen3-asr-service-python3.12-pytorch2.6-cu124-bin.7z
echo.
echo   解压后将 bin 和 lib 目录放置到 asr-service 目录下：
echo.
echo   asr-service\
echo   +-- bin\
echo   ^|   +-- python\
echo   ^|   ^|   +-- python.exe
echo   ^|   +-- ...
echo   +-- lib\
echo   ^|   +-- site-packages\
echo   ^|       +-- ...
echo   +-- cli.bat
echo   +-- start.bat
echo   +-- ...
echo.
call :info_msg "放置完成后重新打开此工具即可检测到"
call :press_any_key
goto :menu_env

:: --- 便携版信息 ---
:env_portable_info
cls
echo.
echo %BOLD%便携版环境信息：%NC%
echo -----------------------------------------
echo   路径:         %SCRIPT_DIR%bin\python
echo   Python 版本:  !PORTABLE_PYTHON_VER!

:: 检测 PyTorch
set "TORCH_VER=未安装"
set "PYTHONPATH=%SCRIPT_DIR%"
set "PATH=%SCRIPT_DIR%bin;%SCRIPT_DIR%bin\python;%PATH%"
for /f "tokens=*" %%v in ('bin\python\python.exe -c "import torch; print(torch.__version__)" 2^>nul') do set "TORCH_VER=%%v"
echo   PyTorch:      !TORCH_VER!

:: 检测 qwen-asr
set "QWEN_VER=未安装"
for /f "tokens=*" %%v in ('bin\python\python.exe -c "import importlib.metadata; print(importlib.metadata.version(\"qwen-asr\"))" 2^>nul') do set "QWEN_VER=%%v"
echo   qwen-asr:     !QWEN_VER!

echo -----------------------------------------
call :press_any_key
goto :menu_env

:: --- venv 安装 ---
:env_venv_install
cls
echo.
if not exist "setup.bat" (
    call :error_msg "未找到 setup.bat"
    call :press_any_key
    goto :menu_env
)
call :info_msg "运行 setup.bat 安装虚拟环境..."
echo.
call setup.bat
:: 刷新检测
call :check_prerequisites
call :press_any_key
goto :menu_env

:: --- venv 重新安装 ---
:env_venv_reinstall
cls
echo.
call :warn_msg "将删除现有虚拟环境并重新安装"
set /p "YN=是否继续？(y/N): "
if /i not "!YN!"=="y" if /i not "!YN!"=="yes" (
    call :info_msg "已取消"
    call :press_any_key
    goto :menu_env
)
call :info_msg "删除现有虚拟环境..."
rmdir /s /q venv 2>nul
set "HAS_VENV=0"
set "VENV_PYTHON_VER="
goto :env_venv_install

:: --- venv 卸载 ---
:env_venv_remove
cls
echo.
set /p "YN=确定要删除虚拟环境？(y/N): "
if /i not "!YN!"=="y" if /i not "!YN!"=="yes" (
    call :info_msg "已取消"
    call :press_any_key
    goto :menu_env
)
call :info_msg "删除虚拟环境..."
rmdir /s /q venv 2>nul
set "HAS_VENV=0"
set "VENV_PYTHON_VER="
call :success_msg "虚拟环境已删除"
call :press_any_key
goto :menu_env

:: --- venv 信息 ---
:env_venv_info
cls
echo.
echo %BOLD%虚拟环境信息：%NC%
echo -----------------------------------------
echo   路径:         %SCRIPT_DIR%venv
for /f "tokens=*" %%v in ('venv\Scripts\python.exe --version 2^>nul') do echo   Python 版本:  %%v
for /f "tokens=2" %%v in ('venv\Scripts\pip.exe --version 2^>nul') do echo   Pip 版本:     %%v

:: 检测 PyTorch
set "TORCH_VER=未安装"
for /f "tokens=*" %%v in ('venv\Scripts\python.exe -c "import torch; print(torch.__version__)" 2^>nul') do set "TORCH_VER=%%v"
echo   PyTorch:      !TORCH_VER!

:: 检测 qwen-asr
set "QWEN_VER=未安装"
for /f "tokens=*" %%v in ('venv\Scripts\python.exe -c "import importlib.metadata; print(importlib.metadata.version(\"qwen-asr\"))" 2^>nul') do set "QWEN_VER=%%v"
echo   qwen-asr:     !QWEN_VER!

:: 包数量
set "PKG_COUNT=0"
for /f %%n in ('venv\Scripts\pip.exe list 2^>nul ^| find /c /v ""') do set /a PKG_COUNT=%%n-2
echo   已安装包:     !PKG_COUNT! 个

echo -----------------------------------------
call :press_any_key
goto :menu_env

:: ============================================================
:: 启动服务
:: ============================================================
:menu_start_service
cls

:: 默认配置
set "LAUNCH_MODEL_SIZE=auto"
set "LAUNCH_DEVICE=auto"
set "LAUNCH_MODEL_SOURCE=modelscope"
set "LAUNCH_ENABLE_ALIGN=yes"
set "LAUNCH_USE_PUNC=no"
set "LAUNCH_WEB=yes"
set "LAUNCH_MAX_SEGMENT=5"
set "LAUNCH_HOST=127.0.0.1"
set "LAUNCH_PORT=8765"
set "LAUNCH_API_KEY="
set "LAUNCH_METHOD="

:: 尝试加载配置
if exist "!CONFIG_FILE!" (
    for /f "usebackq tokens=1,* delims==" %%a in ("!CONFIG_FILE!") do (
        set "LINE=%%a"
        if not "!LINE:~0,1!"=="#" (
            if not "!LINE!"=="" (
                set "%%a=%%~b"
            )
        )
    )

    echo %GREEN%检测到已保存的启动配置：%NC%
    echo.
    call :print_config
    echo.
    echo   1. 使用已保存配置启动
    echo   2. 重新配置
    echo   0. 返回主菜单
    echo.
    set /p "CHOICE=请选择 [0-2]: "
    if "!CHOICE!"=="0" goto :main
    if "!CHOICE!"=="1" goto :start_choose_method
    if "!CHOICE!"=="2" goto :start_configure
    goto :menu_start_service
) else (
    goto :start_configure
)

:: --- 配置参数 ---
:start_configure
cls
echo.
echo %BOLD%%CYAN%配置启动参数%NC%
echo.

:: 模型大小
echo %BOLD%选择模型大小：%NC%
echo   1. auto（根据显存自动选择）
echo   2. 0.6b（轻量，显存需求低）
echo   3. 1.7b（完整，效果更好）
echo.
set /p "C=请选择 [1-3]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_MODEL_SIZE=auto"
if "!C!"=="2" set "LAUNCH_MODEL_SIZE=0.6b"
if "!C!"=="3" set "LAUNCH_MODEL_SIZE=1.7b"
echo.

:: 运行设备
echo %BOLD%选择运行设备：%NC%
echo   1. auto（自动检测）
echo   2. cuda（GPU）
echo   3. cpu
echo.
set /p "C=请选择 [1-3]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_DEVICE=auto"
if "!C!"=="2" set "LAUNCH_DEVICE=cuda"
if "!C!"=="3" set "LAUNCH_DEVICE=cpu"
echo.

:: 模型下载源
echo %BOLD%选择模型下载源：%NC%
echo   1. modelscope（国内推荐）
echo   2. huggingface（国外）
echo.
set /p "C=请选择 [1-2]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_MODEL_SOURCE=modelscope"
if "!C!"=="2" set "LAUNCH_MODEL_SOURCE=huggingface"
echo.

:: 对齐模型
echo %BOLD%对齐模型：%NC%
echo   1. 启用（默认，支持字级时间戳）
echo   2. 禁用
echo.
set /p "C=请选择 [1-2]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_ENABLE_ALIGN=yes"
if "!C!"=="2" set "LAUNCH_ENABLE_ALIGN=no"
echo.

:: 标点恢复
echo %BOLD%标点恢复：%NC%
echo   1. 禁用（默认）
echo   2. 启用
echo.
set /p "C=请选择 [1-2]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_USE_PUNC=no"
if "!C!"=="2" set "LAUNCH_USE_PUNC=yes"
echo.

:: Web UI
echo %BOLD%Web UI：%NC%
echo   1. 启用（访问 /web-ui）
echo   2. 禁用
echo.
set /p "C=请选择 [1-2]（默认 1）: "
if "!C!"=="" set "C=1"
if "!C!"=="1" set "LAUNCH_WEB=yes"
if "!C!"=="2" set "LAUNCH_WEB=no"
echo.

:: 最大切片时长
set /p "C=VAD 切片合并最大时长（秒）[%LAUNCH_MAX_SEGMENT%]: "
if not "!C!"=="" set "LAUNCH_MAX_SEGMENT=!C!"

:: 监听地址
set /p "C=监听地址 [%LAUNCH_HOST%]: "
if not "!C!"=="" set "LAUNCH_HOST=!C!"

:: 监听端口
set /p "C=监听端口 [%LAUNCH_PORT%]: "
if not "!C!"=="" set "LAUNCH_PORT=!C!"

:: API 密钥
set /p "C=API 密钥（留空则不启用认证）[!LAUNCH_API_KEY!]: "
if not "!C!"=="" set "LAUNCH_API_KEY=!C!"
echo.

goto :start_choose_method

:: --- 选择启动方式 ---
:start_choose_method
:: 刷新环境检测
call :check_prerequisites

set "AVAIL_COUNT=0"
if "!HAS_PORTABLE!"=="1" set /a AVAIL_COUNT+=1
if "!HAS_VENV!"=="1" set /a AVAIL_COUNT+=1

if "!AVAIL_COUNT!"=="0" (
    call :error_msg "未检测到可用的 Python 环境（便携版 / venv），请先安装"
    call :press_any_key
    goto :main
)

:: 只有便携版
if "!HAS_PORTABLE!"=="1" if "!HAS_VENV!"=="0" (
    call :info_msg "检测到便携版环境，将使用便携版启动"
    set "LAUNCH_METHOD=portable"
    goto :start_save_and_launch
)

:: 只有 venv
if "!HAS_PORTABLE!"=="0" if "!HAS_VENV!"=="1" (
    call :info_msg "检测到 venv 环境，将使用虚拟环境启动"
    set "LAUNCH_METHOD=venv"
    goto :start_save_and_launch
)

:: 两种都有
echo %BOLD%选择启动方式：%NC%
echo   1. 便携版 Python
echo   2. 虚拟环境 venv
echo.
set /p "C=请选择 [1-2]: "
if "!C!"=="1" set "LAUNCH_METHOD=portable"
if "!C!"=="2" set "LAUNCH_METHOD=venv"
if "!LAUNCH_METHOD!"=="" set "LAUNCH_METHOD=portable"

:start_save_and_launch
:: 保存配置
(
    echo # Qwen3-ASR CLI 启动配置
    echo # 由 cli.bat 自动生成，可手动编辑
    echo LAUNCH_MODEL_SIZE=!LAUNCH_MODEL_SIZE!
    echo LAUNCH_DEVICE=!LAUNCH_DEVICE!
    echo LAUNCH_MODEL_SOURCE=!LAUNCH_MODEL_SOURCE!
    echo LAUNCH_ENABLE_ALIGN=!LAUNCH_ENABLE_ALIGN!
    echo LAUNCH_USE_PUNC=!LAUNCH_USE_PUNC!
    echo LAUNCH_WEB=!LAUNCH_WEB!
    echo LAUNCH_MAX_SEGMENT=!LAUNCH_MAX_SEGMENT!
    echo LAUNCH_HOST=!LAUNCH_HOST!
    echo LAUNCH_PORT=!LAUNCH_PORT!
    echo LAUNCH_API_KEY=!LAUNCH_API_KEY!
    echo LAUNCH_METHOD=!LAUNCH_METHOD!
) > "!CONFIG_FILE!"
call :success_msg "配置已保存到 .cli_launch_config"
echo.

:: 显示配置摘要
call :print_config
echo.

:: 构建启动参数
set "ARGS="
if not "!LAUNCH_MODEL_SIZE!"=="auto" set "ARGS=!ARGS! --model-size !LAUNCH_MODEL_SIZE!"
set "ARGS=!ARGS! --device !LAUNCH_DEVICE!"
set "ARGS=!ARGS! --model-source !LAUNCH_MODEL_SOURCE!"
if "!LAUNCH_ENABLE_ALIGN!"=="yes" (set "ARGS=!ARGS! --enable-align") else (set "ARGS=!ARGS! --no-align")
if "!LAUNCH_USE_PUNC!"=="yes" set "ARGS=!ARGS! --use-punc"
if "!LAUNCH_WEB!"=="yes" set "ARGS=!ARGS! --web"
set "ARGS=!ARGS! --max-segment !LAUNCH_MAX_SEGMENT!"
set "ARGS=!ARGS! --host !LAUNCH_HOST!"
set "ARGS=!ARGS! --port !LAUNCH_PORT!"
if not "!LAUNCH_API_KEY!"=="" set "ARGS=!ARGS! --api-key !LAUNCH_API_KEY!"

:: 启动
if "!LAUNCH_METHOD!"=="portable" goto :launch_portable
if "!LAUNCH_METHOD!"=="venv" goto :launch_venv
call :error_msg "未知的启动方式: !LAUNCH_METHOD!"
call :press_any_key
goto :main

:: --- 便携版启动 ---
:launch_portable
if not exist "bin\python\python.exe" (
    call :error_msg "便携版 Python 不存在"
    call :press_any_key
    goto :main
)

set "PYTHONPATH=%SCRIPT_DIR%"
set "PATH=%SCRIPT_DIR%bin;%SCRIPT_DIR%bin\python;%PATH%"

echo %BOLD%启动命令：%NC%
echo   bin\python\python.exe -m app.main!ARGS!
echo.

bin\python\python.exe -m app.main !ARGS!
call :press_any_key
goto :main

:: --- venv 启动 ---
:launch_venv
if not exist "venv\Scripts\python.exe" (
    call :error_msg "虚拟环境不存在"
    call :press_any_key
    goto :main
)

call venv\Scripts\activate.bat

echo %BOLD%启动命令：%NC%
echo   venv\Scripts\python.exe -m app.main!ARGS!
echo.

venv\Scripts\python.exe -m app.main !ARGS!
call :press_any_key
goto :main

:: ============================================================
:: 配置摘要显示
:: ============================================================
:print_config
echo %BOLD%当前启动配置：%NC%
echo -----------------------------------------
echo   模型大小:     !LAUNCH_MODEL_SIZE!
echo   运行设备:     !LAUNCH_DEVICE!
echo   模型下载源:   !LAUNCH_MODEL_SOURCE!
if "!LAUNCH_ENABLE_ALIGN!"=="yes" (echo   对齐模型:     启用) else (echo   对齐模型:     禁用)
if "!LAUNCH_USE_PUNC!"=="yes" (echo   标点恢复:     启用) else (echo   标点恢复:     禁用)
if "!LAUNCH_WEB!"=="yes" (echo   Web UI:       启用) else (echo   Web UI:       禁用)
echo   最大切片时长: !LAUNCH_MAX_SEGMENT! 秒
echo   监听地址:     !LAUNCH_HOST!
echo   监听端口:     !LAUNCH_PORT!
if not "!LAUNCH_API_KEY!"=="" (echo   API 密钥:     已设置) else (echo   API 密钥:     未设置（无需认证）)
if not "!LAUNCH_METHOD!"=="" echo   启动方式:     !LAUNCH_METHOD!
echo -----------------------------------------
goto :eof

:: ============================================================
:: 退出
:: ============================================================
:quit
cls
call :info_msg "再见！"
exit /b 0
