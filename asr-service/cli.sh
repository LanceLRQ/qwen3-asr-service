#!/usr/bin/env bash
# Qwen3-ASR Service 交互式管理脚本
# 支持 Docker / venv 两种运行方式的统一管理入口

set -euo pipefail

# ============================================================
# 常量定义
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$PROJECT_ROOT/docker-compose.yml"
CONFIG_FILE="$SCRIPT_DIR/.cli_launch_config"
IMAGE_NAME="lancelrq/qwen3-asr-service"
IMAGE_TAG="latest"
CONTAINER_NAME="qwen3-asr-service"

# ANSI 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
REVERSE='\033[7m'
NC='\033[0m'

# 全局状态
HAS_DOCKER=0
HAS_COMPOSE=0
COMPOSE_CMD=""
HAS_VENV=0
HAS_GPU=0
VENV_PYTHON_VERSION=""
MENU_RESULT=0
INPUT_RESULT=""

# ============================================================
# 信号处理：确保退出时恢复光标
# ============================================================
cleanup() {
    printf '\033[?25h'  # 恢复光标
    echo
}
handle_signal() {
    cleanup
    exit 0
}
trap cleanup EXIT
trap handle_signal INT TERM HUP

# ============================================================
# 辅助输出函数
# ============================================================
info_msg()    { printf "${CYAN}[INFO]${NC} %s\n" "$*"; }
success_msg() { printf "${GREEN}[OK]${NC} %s\n" "$*"; }
warn_msg()    { printf "${YELLOW}[WARN]${NC} %s\n" "$*"; }
error_msg()   { printf "${RED}[ERROR]${NC} %s\n" "$*"; }

press_any_key() {
    echo
    printf "${DIM}按任意键继续...${NC}"
    read -rsn1 || exit 0  # Ctrl+D 退出
    echo
}

confirm() {
    local prompt="${1:-确认操作？}"
    printf "${YELLOW}%s (y/N): ${NC}" "$prompt"
    local answer
    read -r answer || exit 0  # Ctrl+D 退出
    case "$answer" in
        y|Y|yes|是|确认) return 0 ;;
        *) return 1 ;;
    esac
}

read_input() {
    local prompt="$1"
    local default="${2:-}"
    if [ -n "$default" ]; then
        printf "%s [${DIM}%s${NC}]: " "$prompt" "$default"
    else
        printf "%s: " "$prompt"
    fi
    local answer
    read -r answer || exit 0  # Ctrl+D 退出
    INPUT_RESULT="${answer:-$default}"
}

# ============================================================
# 菜单系统
# ============================================================
# show_menu "标题" "选项1" "选项2" ...
# 结果存入 MENU_RESULT (0-based index)
show_menu() {
    local title="$1"
    shift
    local options=("$@")
    local count=${#options[@]}
    local selected=0
    local first_draw=1

    # 隐藏光标
    printf '\033[?25l'

    while true; do
        # 非首次绘制时，光标上移清除旧菜单
        if [ "$first_draw" -eq 0 ]; then
            # 上移 count+2 行 (标题 + 空行 + 选项数)
            printf '\033[%dA' $((count + 2))
        fi
        first_draw=0

        # 绘制标题
        printf '\033[2K'  # 清行
        printf "${BOLD}${CYAN}%s${NC}\n" "$title"
        printf '\033[2K\n'  # 空行

        # 绘制选项
        for i in "${!options[@]}"; do
            printf '\033[2K'  # 清行
            if [ "$i" -eq "$selected" ]; then
                printf "  ${REVERSE} > %s ${NC}\n" "${options[$i]}"
            else
                printf "    %s\n" "${options[$i]}"
            fi
        done

        # 读取按键
        local key
        IFS= read -rsn1 key || exit 0  # Ctrl+D 退出

        case "$key" in
            $'\x1b')  # 转义序列开头
                local rest
                read -rsn2 -t 0.1 rest || true
                case "$rest" in
                    '[A')  # 上箭头
                        selected=$(( (selected - 1 + count) % count ))
                        ;;
                    '[B')  # 下箭头
                        selected=$(( (selected + 1) % count ))
                        ;;
                esac
                ;;
            '')  # Enter
                break
                ;;
            [0-9])  # 数字键
                local num=$((key))
                # 0 映射到最后一项（返回/退出），1-9 映射到对应索引-1
                if [ "$num" -eq 0 ]; then
                    selected=$((count - 1))
                elif [ "$num" -le "$count" ]; then
                    selected=$((num - 1))
                fi
                break
                ;;
        esac
    done

    # 恢复光标
    printf '\033[?25h'
    MENU_RESULT=$selected
}

# ============================================================
# 环境检测
# ============================================================
check_prerequisites() {
    # Docker
    if command -v docker &>/dev/null; then
        HAS_DOCKER=1
    fi

    # Docker Compose
    if [ "$HAS_DOCKER" -eq 1 ] && docker compose version &>/dev/null; then
        HAS_COMPOSE=1
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &>/dev/null; then
        HAS_COMPOSE=1
        COMPOSE_CMD="docker-compose"
    fi

    # GPU (nvidia-smi)
    if command -v nvidia-smi &>/dev/null && nvidia-smi &>/dev/null; then
        HAS_GPU=1
    fi

    # venv
    if [ -d "$SCRIPT_DIR/venv" ] && [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
        HAS_VENV=1
        VENV_PYTHON_VERSION=$("$SCRIPT_DIR/venv/bin/python3" --version 2>/dev/null | awk '{print $2}' || echo "未知")
    fi
}

print_status_summary() {
    echo
    printf "${BOLD}环境检测结果：${NC}\n"
    echo "─────────────────────────────────────"

    if [ "$HAS_DOCKER" -eq 1 ]; then
        printf "  Docker Engine:      ${GREEN}✔ 已安装${NC}\n"
    else
        printf "  Docker Engine:      ${RED}✘ 未安装${NC}\n"
    fi

    if [ "$HAS_COMPOSE" -eq 1 ]; then
        if [[ "$COMPOSE_CMD" == "docker compose" ]]; then
            printf "  Docker Compose:     ${GREEN}✔ V2${NC}\n"
        else
            printf "  Docker Compose:     ${GREEN}✔ V1${NC}\n"
        fi
    else
        printf "  Docker Compose:     ${RED}✘ 未安装${NC}\n"
    fi

    if [ "$HAS_GPU" -eq 1 ]; then
        printf "  NVIDIA GPU:         ${GREEN}✔ 已检测${NC}\n"
    else
        printf "  NVIDIA GPU:         ${YELLOW}✘ 未检测${NC}\n"
    fi

    if [ "$HAS_VENV" -eq 1 ]; then
        printf "  Python 虚拟环境:    ${GREEN}✔ Python %s${NC}\n" "$VENV_PYTHON_VERSION"
    else
        printf "  Python 虚拟环境:    ${RED}✘ 未创建${NC}\n"
    fi

    echo "─────────────────────────────────────"
    echo
}

# ============================================================
# Docker 管理
# ============================================================
run_compose() {
    if [ "$HAS_COMPOSE" -eq 0 ]; then
        error_msg "Docker Compose 未安装，无法执行此操作"
        return 1
    fi
    $COMPOSE_CMD -f "$COMPOSE_FILE" "$@"
}

docker_pull() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        return
    fi
    info_msg "拉取镜像 ${IMAGE_NAME}:${IMAGE_TAG} ..."
    echo
    if docker pull "${IMAGE_NAME}:${IMAGE_TAG}"; then
        echo
        success_msg "镜像拉取完成"
    else
        echo
        error_msg "镜像拉取失败"
    fi
    press_any_key
}

docker_build() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        return
    fi
    if [ ! -f "$PROJECT_ROOT/build.sh" ]; then
        error_msg "未找到 build.sh"
        press_any_key
        return
    fi
    info_msg "构建镜像（从 Dockerfile）..."
    echo
    if (cd "$PROJECT_ROOT" && bash build.sh); then
        echo
        success_msg "镜像构建完成"
    else
        echo
        error_msg "镜像构建失败"
    fi
    press_any_key
}

docker_up() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi

    # 检查容器是否已在运行
    if docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        warn_msg "容器 $CONTAINER_NAME 已在运行中，请勿重复启动"
        press_any_key
        return
    fi

    # 检查是否有已保存的启动配置
    if [ ! -f "$CONFIG_FILE" ]; then
        warn_msg "尚未配置启动参数"
        info_msg "请先通过主菜单「启动服务」配置参数后再启动容器"
        press_any_key
        return
    fi

    # 加载配置并以 docker 方式启动
    default_config
    load_launch_config
    LAUNCH_METHOD="docker"

    echo
    print_config_summary
    echo

    launch_via_docker
}

docker_down() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi
    # 检查容器是否在运行
    if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        warn_msg "容器 $CONTAINER_NAME 未在运行"
        press_any_key
        return
    fi
    info_msg "停止容器 $CONTAINER_NAME ..."
    echo
    if docker stop "$CONTAINER_NAME" && docker rm "$CONTAINER_NAME"; then
        echo
        success_msg "容器已停止并移除"
    else
        echo
        error_msg "停止失败"
    fi
    press_any_key
}

docker_status() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi
    echo
    printf "${BOLD}容器状态 [%s]：${NC}\n" "$CONTAINER_NAME"
    echo "─────────────────────────────────────"
    local info
    info=$(docker ps -a --filter "name=^${CONTAINER_NAME}$" --format "table {{.Status}}\t{{.Ports}}\t{{.CreatedAt}}" 2>/dev/null)
    if [ -z "$info" ] || [ "$(echo "$info" | wc -l)" -le 1 ]; then
        printf "  ${DIM}容器未创建${NC}\n"
    else
        echo "$info"
    fi
    echo "─────────────────────────────────────"
    press_any_key
}

docker_logs() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi
    if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        warn_msg "容器 $CONTAINER_NAME 不存在"
        press_any_key
        return
    fi
    info_msg "查看日志（Ctrl+C 返回）..."
    echo
    docker logs --tail=50 -f "$CONTAINER_NAME" || true
    press_any_key
}

docker_images() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi
    echo
    printf "${BOLD}本地镜像：${NC}\n"
    docker images "${IMAGE_NAME}" --format "table {{.Repository}}\t{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}" || true
    echo
    printf "${BOLD}所有相关镜像：${NC}\n"
    docker images --filter "reference=${IMAGE_NAME}*" --format "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}" || true
    press_any_key
}

menu_docker() {
    while true; do
        clear
        if [ "$HAS_DOCKER" -eq 0 ]; then
            warn_msg "Docker 未安装，部分功能不可用"
            echo
        fi

        show_menu "Docker 管理" \
            "1. 拉取镜像" \
            "2. 构建镜像" \
            "3. 启动容器" \
            "4. 停止容器" \
            "5. 查看容器状态" \
            "6. 查看镜像状态" \
            "7. 查看日志" \
            "0. 返回主菜单"

        case $MENU_RESULT in
            0) docker_pull ;;
            1) docker_build ;;
            2) docker_up ;;
            3) docker_down ;;
            4) docker_status ;;
            5) docker_images ;;
            6) docker_logs ;;
            7) return ;;
        esac
    done
}

# ============================================================
# 虚拟环境管理
# ============================================================
venv_install_or_reinstall() {
    if [ ! -f "$SCRIPT_DIR/setup.sh" ]; then
        error_msg "未找到 setup.sh"
        press_any_key
        return
    fi

    # 已有 venv 时作为重新安装处理
    if [ "$HAS_VENV" -eq 1 ]; then
        warn_msg "检测到已有虚拟环境"
        if ! confirm "将删除现有虚拟环境并重新安装，是否继续？"; then
            info_msg "已取消"
            return
        fi
        info_msg "删除现有虚拟环境..."
        rm -rf "$SCRIPT_DIR/venv"
        HAS_VENV=0
        VENV_PYTHON_VERSION=""
    fi

    info_msg "运行安装脚本..."
    echo
    (cd "$SCRIPT_DIR" && bash setup.sh) || true
    # 刷新检测
    if [ -d "$SCRIPT_DIR/venv" ] && [ -f "$SCRIPT_DIR/venv/bin/python3" ]; then
        HAS_VENV=1
        VENV_PYTHON_VERSION=$("$SCRIPT_DIR/venv/bin/python3" --version 2>/dev/null | awk '{print $2}' || echo "未知")
    fi
    press_any_key
}

venv_remove() {
    if [ "$HAS_VENV" -eq 0 ]; then
        warn_msg "虚拟环境未创建"
        press_any_key
        return
    fi
    if ! confirm "确定要删除虚拟环境？"; then
        info_msg "已取消"
        return
    fi
    info_msg "删除虚拟环境..."
    rm -rf "$SCRIPT_DIR/venv"
    HAS_VENV=0
    VENV_PYTHON_VERSION=""
    success_msg "虚拟环境已删除"
    press_any_key
}

venv_info() {
    echo
    if [ "$HAS_VENV" -eq 0 ]; then
        warn_msg "虚拟环境未创建"
        press_any_key
        return
    fi

    local python_bin="$SCRIPT_DIR/venv/bin/python3"
    local pip_bin="$SCRIPT_DIR/venv/bin/pip"

    printf "${BOLD}虚拟环境信息：${NC}\n"
    echo "─────────────────────────────────────"
    printf "  路径:         %s/venv\n" "$SCRIPT_DIR"
    printf "  Python 版本:  %s\n" "$($python_bin --version 2>/dev/null || echo '未知')"
    printf "  Pip 版本:     %s\n" "$($pip_bin --version 2>/dev/null | awk '{print $2}' || echo '未知')"

    # 关键包版本
    local torch_ver
    torch_ver=$($pip_bin show torch 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "未安装")
    printf "  PyTorch:      %s\n" "$torch_ver"

    local qwen_ver
    qwen_ver=$($pip_bin show qwen-asr 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "未安装")
    printf "  qwen-asr:     %s\n" "$qwen_ver"

    # 包数量
    local pkg_count
    pkg_count=$($pip_bin list 2>/dev/null | tail -n +3 | wc -l || echo "0")
    printf "  已安装包:     %s 个\n" "$pkg_count"
    echo "─────────────────────────────────────"

    press_any_key
}

menu_venv() {
    while true; do
        clear
        if [ "$HAS_VENV" -eq 1 ]; then
            # 有 venv：重新安装、卸载、检测版本
            show_menu "虚拟环境管理" \
                "1. 重新安装虚拟环境" \
                "2. 卸载删除" \
                "3. 检测版本信息" \
                "0. 返回主菜单"

            case $MENU_RESULT in
                0) venv_install_or_reinstall ;;
                1) venv_remove ;;
                2) venv_info ;;
                3) return ;;
            esac
        else
            # 无 venv：仅安装
            show_menu "虚拟环境管理" \
                "1. 安装虚拟环境" \
                "0. 返回主菜单"

            case $MENU_RESULT in
                0) venv_install_or_reinstall ;;
                1) return ;;
            esac
        fi
    done
}

# ============================================================
# 启动服务
# ============================================================

# 默认配置值
default_config() {
    LAUNCH_MODEL_SIZE="auto"
    LAUNCH_DEVICE="auto"
    LAUNCH_MODEL_SOURCE="modelscope"
    LAUNCH_ENABLE_ALIGN="yes"
    LAUNCH_USE_PUNC="no"
    LAUNCH_WEB="yes"
    LAUNCH_MAX_SEGMENT="5"
    LAUNCH_HOST="127.0.0.1"
    LAUNCH_PORT="8765"
    LAUNCH_API_KEY=""
    LAUNCH_METHOD=""
}

load_launch_config() {
    if [ -f "$CONFIG_FILE" ]; then
        # shellcheck source=/dev/null
        source "$CONFIG_FILE"
        return 0
    fi
    return 1
}

save_launch_config() {
    cat > "$CONFIG_FILE" <<EOF
# Qwen3-ASR CLI 启动配置
# 由 cli.sh 自动生成，可手动编辑
LAUNCH_MODEL_SIZE="$LAUNCH_MODEL_SIZE"
LAUNCH_DEVICE="$LAUNCH_DEVICE"
LAUNCH_MODEL_SOURCE="$LAUNCH_MODEL_SOURCE"
LAUNCH_ENABLE_ALIGN="$LAUNCH_ENABLE_ALIGN"
LAUNCH_USE_PUNC="$LAUNCH_USE_PUNC"
LAUNCH_WEB="$LAUNCH_WEB"
LAUNCH_MAX_SEGMENT="$LAUNCH_MAX_SEGMENT"
LAUNCH_HOST="$LAUNCH_HOST"
LAUNCH_PORT="$LAUNCH_PORT"
LAUNCH_API_KEY="$LAUNCH_API_KEY"
LAUNCH_METHOD="$LAUNCH_METHOD"
EOF
    success_msg "配置已保存到 .cli_launch_config"
}

print_config_summary() {
    printf "${BOLD}当前启动配置：${NC}\n"
    echo "─────────────────────────────────────"
    printf "  模型大小:     %s\n" "$LAUNCH_MODEL_SIZE"
    printf "  运行设备:     %s\n" "$LAUNCH_DEVICE"
    printf "  模型下载源:   %s\n" "$LAUNCH_MODEL_SOURCE"
    printf "  对齐模型:     %s\n" "$([ "$LAUNCH_ENABLE_ALIGN" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  标点恢复:     %s\n" "$([ "$LAUNCH_USE_PUNC" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  Web UI:       %s\n" "$([ "$LAUNCH_WEB" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  最大切片时长: %s 秒\n" "$LAUNCH_MAX_SEGMENT"
    printf "  监听地址:     %s\n" "$LAUNCH_HOST"
    printf "  监听端口:     %s\n" "$LAUNCH_PORT"
    printf "  API 密钥:     %s\n" "$([ -n "$LAUNCH_API_KEY" ] && echo "已设置" || echo "未设置（无需认证）")"
    if [ -n "$LAUNCH_METHOD" ]; then
        printf "  启动方式:     %s\n" "$LAUNCH_METHOD"
    fi
    echo "─────────────────────────────────────"
}

configure_launch() {
    echo
    printf "${BOLD}${CYAN}配置启动参数${NC}\n"
    echo

    # 模型大小
    show_menu "选择模型大小" \
        "auto (根据显存自动选择)" \
        "0.6b (轻量，显存需求低)" \
        "1.7b (完整，效果更好)"
    case $MENU_RESULT in
        0) LAUNCH_MODEL_SIZE="auto" ;;
        1) LAUNCH_MODEL_SIZE="0.6b" ;;
        2) LAUNCH_MODEL_SIZE="1.7b" ;;
    esac
    echo

    # 运行设备
    show_menu "选择运行设备" \
        "auto (自动检测)" \
        "cuda (GPU)" \
        "cpu"
    case $MENU_RESULT in
        0) LAUNCH_DEVICE="auto" ;;
        1) LAUNCH_DEVICE="cuda" ;;
        2) LAUNCH_DEVICE="cpu" ;;
    esac
    echo

    # 模型下载源
    show_menu "选择模型下载源" \
        "modelscope (国内推荐)" \
        "huggingface (国外)"
    case $MENU_RESULT in
        0) LAUNCH_MODEL_SOURCE="modelscope" ;;
        1) LAUNCH_MODEL_SOURCE="huggingface" ;;
    esac
    echo

    # 对齐模型
    show_menu "对齐模型" \
        "启用 (默认，支持字级时间戳)" \
        "禁用"
    case $MENU_RESULT in
        0) LAUNCH_ENABLE_ALIGN="yes" ;;
        1) LAUNCH_ENABLE_ALIGN="no" ;;
    esac
    echo

    # 标点恢复
    show_menu "标点恢复" \
        "禁用 (默认)" \
        "启用"
    case $MENU_RESULT in
        0) LAUNCH_USE_PUNC="no" ;;
        1) LAUNCH_USE_PUNC="yes" ;;
    esac
    echo

    # Web UI
    show_menu "Web UI" \
        "启用 (访问 /web-ui)" \
        "禁用"
    case $MENU_RESULT in
        0) LAUNCH_WEB="yes" ;;
        1) LAUNCH_WEB="no" ;;
    esac
    echo

    # 最大切片时长
    read_input "VAD 切片合并最大时长（秒）" "$LAUNCH_MAX_SEGMENT"
    LAUNCH_MAX_SEGMENT="$INPUT_RESULT"
    echo

    # 监听地址
    read_input "监听地址" "$LAUNCH_HOST"
    LAUNCH_HOST="$INPUT_RESULT"

    # 监听端口
    read_input "监听端口" "$LAUNCH_PORT"
    LAUNCH_PORT="$INPUT_RESULT"
    echo

    # API 密钥
    read_input "API 密钥（留空则不启用认证）" "$LAUNCH_API_KEY"
    LAUNCH_API_KEY="$INPUT_RESULT"
    echo
}

build_launch_args() {
    local args=""

    if [ "$LAUNCH_MODEL_SIZE" != "auto" ]; then
        args+=" --model-size $LAUNCH_MODEL_SIZE"
    fi
    args+=" --device $LAUNCH_DEVICE"
    args+=" --model-source $LAUNCH_MODEL_SOURCE"

    if [ "$LAUNCH_ENABLE_ALIGN" = "yes" ]; then
        args+=" --enable-align"
    else
        args+=" --no-align"
    fi

    if [ "$LAUNCH_USE_PUNC" = "yes" ]; then
        args+=" --use-punc"
    fi

    if [ "$LAUNCH_WEB" = "yes" ]; then
        args+=" --web"
    fi

    args+=" --max-segment $LAUNCH_MAX_SEGMENT"
    args+=" --host $LAUNCH_HOST"
    args+=" --port $LAUNCH_PORT"

    if [ -n "$LAUNCH_API_KEY" ]; then
        args+=" --api-key $LAUNCH_API_KEY"
    fi

    echo "$args"
}

launch_via_venv() {
    local args
    args=$(build_launch_args)

    if [ "$HAS_VENV" -eq 0 ]; then
        error_msg "虚拟环境未创建，请先安装"
        press_any_key
        return
    fi

    echo
    printf "${BOLD}启动命令：${NC}\n"
    printf "  bash start.sh%s\n" "$args"
    echo

    (cd "$SCRIPT_DIR" && bash start.sh $args)
}

launch_via_docker() {
    local args
    args=$(build_launch_args)

    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi

    # 检查同名容器是否已存在
    if docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"; then
        warn_msg "容器 $CONTAINER_NAME 已存在"
        show_menu "如何处理？" \
            "停止并删除旧容器，重新启动" \
            "取消启动"
        case $MENU_RESULT in
            0)
                info_msg "停止并删除旧容器..."
                docker stop "$CONTAINER_NAME" &>/dev/null || true
                docker rm "$CONTAINER_NAME" &>/dev/null || true
                ;;
            1)
                info_msg "已取消"
                return
                ;;
        esac
    fi

    # 构建 docker run 命令
    local docker_host="$LAUNCH_HOST"
    # Docker 容器内需要监听 0.0.0.0 才能从外部访问
    local docker_args
    docker_args=$(build_launch_args | sed "s/--host $LAUNCH_HOST/--host 0.0.0.0/")

    local gpu_flag=""
    if [ "$HAS_GPU" -eq 1 ]; then
        gpu_flag="--gpus all"
    fi

    local cmd="docker run -d ${gpu_flag} \\
    -p ${LAUNCH_PORT}:${LAUNCH_PORT} \\
    -v \"${SCRIPT_DIR}/models:/app/models\" \\
    -v \"${SCRIPT_DIR}/logs:/app/logs\" \\
    --name ${CONTAINER_NAME} \\
    ${IMAGE_NAME}:${IMAGE_TAG} \\
    ${docker_args}"

    echo
    printf "${BOLD}启动命令：${NC}\n"
    echo "$cmd"
    echo

    # 实际执行（不用 eval，直接组装）
    local run_args=("run" "-d")
    if [ "$HAS_GPU" -eq 1 ]; then
        run_args+=("--gpus" "all")
    fi
    run_args+=("-p" "${LAUNCH_PORT}:${LAUNCH_PORT}")
    run_args+=("-v" "${SCRIPT_DIR}/models:/app/models")
    run_args+=("-v" "${SCRIPT_DIR}/logs:/app/logs")
    if [ -n "$LAUNCH_API_KEY" ]; then
        run_args+=("-e" "ASR_API_KEY=${LAUNCH_API_KEY}")
    fi
    run_args+=("--name" "${CONTAINER_NAME}")
    run_args+=("${IMAGE_NAME}:${IMAGE_TAG}")
    # shellcheck disable=SC2086
    run_args+=($docker_args)

    if docker "${run_args[@]}"; then
        echo
        success_msg "容器已启动"
        info_msg "使用 docker logs -f $CONTAINER_NAME 查看日志"
    else
        echo
        error_msg "启动失败"
    fi
    press_any_key
}

choose_launch_method() {
    local has_docker_available=0
    local has_venv_available=0

    [ "$HAS_DOCKER" -eq 1 ] && has_docker_available=1
    [ "$HAS_VENV" -eq 1 ] && has_venv_available=1

    # 都没有
    if [ "$has_docker_available" -eq 0 ] && [ "$has_venv_available" -eq 0 ]; then
        error_msg "未检测到可用的运行环境（Docker / venv），请先安装"
        press_any_key
        return 1
    fi

    # 只有一种
    if [ "$has_docker_available" -eq 1 ] && [ "$has_venv_available" -eq 0 ]; then
        info_msg "仅检测到 Docker 环境，将使用 Docker 启动"
        LAUNCH_METHOD="docker"
        return 0
    fi

    if [ "$has_docker_available" -eq 0 ] && [ "$has_venv_available" -eq 1 ]; then
        info_msg "仅检测到 venv 环境，将使用本地虚拟环境启动"
        LAUNCH_METHOD="venv"
        return 0
    fi

    # 两种都有，让用户选
    show_menu "选择启动方式" \
        "Docker 容器 (推荐)" \
        "本地虚拟环境"
    case $MENU_RESULT in
        0) LAUNCH_METHOD="docker" ;;
        1) LAUNCH_METHOD="venv" ;;
    esac
    return 0
}

menu_start_service() {
    echo
    default_config

    # 尝试加载已保存的配置
    if load_launch_config; then
        printf "${GREEN}检测到已保存的启动配置：${NC}\n"
        echo
        print_config_summary
        echo

        show_menu "选择操作" \
            "使用已保存配置启动" \
            "重新配置" \
            "返回主菜单"

        case $MENU_RESULT in
            0)
                # 使用已保存配置，但重新选择启动方式
                if ! choose_launch_method; then
                    return
                fi
                ;;
            1)
                configure_launch
                if ! choose_launch_method; then
                    return
                fi
                save_launch_config
                ;;
            2)
                return
                ;;
        esac
    else
        # 无配置，进入配置流程
        configure_launch
        if ! choose_launch_method; then
            return
        fi
        save_launch_config
    fi

    # 确认并启动
    echo
    print_config_summary
    echo

    case "$LAUNCH_METHOD" in
        docker) launch_via_docker ;;
        venv)   launch_via_venv ;;
    esac
}

# ============================================================
# .gitignore 维护
# ============================================================
ensure_gitignore() {
    local gitignore="$SCRIPT_DIR/.gitignore"
    if [ -f "$gitignore" ]; then
        if ! grep -qxF '.cli_launch_config' "$gitignore" 2>/dev/null; then
            echo '.cli_launch_config' >> "$gitignore"
        fi
    fi
}

# ============================================================
# Banner
# ============================================================
show_banner() {
    printf "${BOLD}${CYAN}"
    cat << 'BANNER'
   ___                    _____          _    ____  ____
  / _ \__      _____ _ _ |___ /         / \  / ___||  _ \
 | | | \ \ /\ / / _ \ '_ \ |_ \  _____|  / \_\___ \| |_) |
 | |_| |\ V  V /  __/ | | |__) ||___| / /\ \___) |  _ <
  \__\_\ \_/\_/ \___|_| |_|____/      /_/  \_\____/|_| \_\
BANNER
    printf "${NC}"
    printf "${DIM}  Qwen3-ASR Service 管理工具${NC}\n"
    echo
}

# ============================================================
# 主菜单
# ============================================================
redraw_header() {
    clear
    show_banner
    print_status_summary
}

menu_main() {
    while true; do
        redraw_header
        show_menu "主菜单" \
            "1. Docker 管理" \
            "2. 虚拟环境管理" \
            "3. 启动服务" \
            "0. 退出"

        case $MENU_RESULT in
            0) menu_docker ;;
            1) menu_venv ;;
            2) menu_start_service ;;
            3)
                clear
                info_msg "再见！"
                exit 0
                ;;
        esac
    done
}

# ============================================================
# 入口
# ============================================================
main() {
    cd "$SCRIPT_DIR"
    clear
    show_banner
    check_prerequisites
    print_status_summary
    ensure_gitignore
    menu_main
}

main "$@"
