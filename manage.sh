#!/usr/bin/env bash
# Qwen3-ASR Service 交互式管理脚本
# 支持 Docker / venv 两种运行方式的统一管理入口

set -euo pipefail

# ============================================================
# 常量定义
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"                          # 脚本已置于仓库根
SERVICE_DIR="$SCRIPT_DIR/asr-service"               # 应用目录（venv / 启动脚本 / 配置 / 模型 / 日志）
COMPOSE_FILE="$PROJECT_ROOT/docker/docker-compose.yml"
COMPOSE_FILE_CPU="$PROJECT_ROOT/docker/docker-compose.cpu.yml"
COMPOSE_FILE_VLLM="$PROJECT_ROOT/docker/docker-compose.vllm.yml"
SERVICE_CONFIG="$SERVICE_DIR/config.yaml"
SERVICE_CONFIG_EXAMPLE="$SERVICE_DIR/config.example.yaml"
CONFIG_FILE="$SERVICE_DIR/.cli_launch_config"
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
HAS_VENV_VLLM=0
HAS_GPU=0
VENV_PYTHON_VERSION=""
VENV_VLLM_PYTHON_VERSION=""
MENU_RESULT=0
INPUT_RESULT=""
ACTIVE_COMPOSE_VARIANT="gpu"   # Compose 编排：gpu / cpu / vllm（菜单内可切换）

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
            $'\x1b')  # 转义序列开头（方向键为 ESC [ A/B）
                # 逐字节读取，超时用整数秒以兼容 macOS 自带 Bash 3.2
                # （Bash 3.2 的 read -t 不支持小数，-t 0.1 会报错导致方向键失效）
                local b2 b3
                read -rsn1 -t 1 b2 || true
                if [ "$b2" = '[' ]; then
                    read -rsn1 -t 1 b3 || true
                    case "$b3" in
                        'A')  # 上箭头
                            selected=$(( (selected - 1 + count) % count ))
                            ;;
                        'B')  # 下箭头
                            selected=$(( (selected + 1) % count ))
                            ;;
                    esac
                fi
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

    # venv（standard）与 venv-vllm（vLLM 独立环境）——重跑时先重置，反映删除/新建
    HAS_VENV=0; VENV_PYTHON_VERSION=""
    if [ -d "$SERVICE_DIR/venv" ] && [ -f "$SERVICE_DIR/venv/bin/python3" ]; then
        HAS_VENV=1
        VENV_PYTHON_VERSION=$("$SERVICE_DIR/venv/bin/python3" --version 2>/dev/null | awk '{print $2}' || echo "未知")
    fi
    HAS_VENV_VLLM=0; VENV_VLLM_PYTHON_VERSION=""
    if [ -d "$SERVICE_DIR/venv-vllm" ] && [ -f "$SERVICE_DIR/venv-vllm/bin/python3" ]; then
        HAS_VENV_VLLM=1
        VENV_VLLM_PYTHON_VERSION=$("$SERVICE_DIR/venv-vllm/bin/python3" --version 2>/dev/null | awk '{print $2}' || echo "未知")
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

    if [ "$HAS_VENV_VLLM" -eq 1 ]; then
        printf "  vLLM 环境 venv-vllm: ${GREEN}✔ Python %s${NC}\n" "$VENV_VLLM_PYTHON_VERSION"
    else
        printf "  vLLM 环境 venv-vllm: ${DIM}✘ 未创建（仅 vLLM 模式需要）${NC}\n"
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
    show_menu "拉取哪个镜像变体？" \
        "GPU standard（${IMAGE_TAG}）" \
        "CPU（${IMAGE_TAG}-cpu）" \
        "vLLM 原生流式（${IMAGE_TAG}-vllm）"
    local tag
    case $MENU_RESULT in
        0) tag="$IMAGE_TAG" ;;
        1) tag="${IMAGE_TAG}-cpu" ;;
        2) tag="${IMAGE_TAG}-vllm" ;;
    esac
    info_msg "拉取镜像 ${IMAGE_NAME}:${tag} ..."
    echo
    if docker pull "${IMAGE_NAME}:${tag}"; then
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
    if [ ! -f "$PROJECT_ROOT/docker/build.sh" ]; then
        error_msg "未找到 docker/build.sh"
        press_any_key
        return
    fi
    info_msg "构建镜像（从 Dockerfile）..."
    echo
    if (cd "$PROJECT_ROOT" && bash docker/build.sh); then
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

    # 参数向导（首次自动进入配置）→ docker run 启动
    launch_wizard docker
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

        show_menu "Docker 容器方式（docker run，参数向导）" \
            "1. 启动容器（参数向导）" \
            "2. 停止容器" \
            "3. 查看容器状态" \
            "4. 查看日志" \
            "5. 拉取镜像" \
            "6. 构建镜像" \
            "7. 查看镜像状态" \
            "0. 返回主菜单"

        case $MENU_RESULT in
            0) docker_up ;;
            1) docker_down ;;
            2) docker_status ;;
            3) docker_logs ;;
            4) docker_pull ;;
            5) docker_build ;;
            6) docker_images ;;
            7) return ;;
        esac
    done
}

# ============================================================
# Docker Compose 管理（config.yaml 驱动，推荐）
# ============================================================
# 当前选中的编排文件（GPU / CPU，可在菜单切换）
active_compose_file() {
    case "$ACTIVE_COMPOSE_VARIANT" in
        cpu)  echo "$COMPOSE_FILE_CPU" ;;
        vllm) echo "$COMPOSE_FILE_VLLM" ;;
        *)    echo "$COMPOSE_FILE" ;;
    esac
}

# 首次使用引导：无 config.yaml 时从 config.example.yaml 拷贝生成。
# compose 已把 config.yaml 挂载进容器，必须先存在，否则 bind 挂载会创建空目录。
ensure_service_config() {
    if [ -f "$SERVICE_CONFIG" ]; then
        return 0
    fi
    if [ ! -f "$SERVICE_CONFIG_EXAMPLE" ]; then
        error_msg "未找到 config.example.yaml，无法生成 config.yaml"
        return 1
    fi
    info_msg "首次使用：从 config.example.yaml 生成 config.yaml ..."
    if cp "$SERVICE_CONFIG_EXAMPLE" "$SERVICE_CONFIG"; then
        chmod 600 "$SERVICE_CONFIG" 2>/dev/null || true
        success_msg "已生成 asr-service/config.yaml（chmod 600；可能写入 api_key，勿提交）"
        return 0
    fi
    error_msg "生成 config.yaml 失败"
    return 1
}

# 前置检查：Docker 与 Compose 均就绪
compose_prereq() {
    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        return 1
    fi
    if [ "$HAS_COMPOSE" -eq 0 ]; then
        error_msg "Docker Compose 未安装"
        return 1
    fi
    return 0
}

compose_up() {
    compose_prereq || { press_any_key; return; }
    ensure_service_config || { press_any_key; return; }
    local file; file="$(active_compose_file)"
    info_msg "启动容器（$ACTIVE_COMPOSE_VARIANT · $(basename "$file") · 配置以 config.yaml 为准）..."
    echo
    if $COMPOSE_CMD -f "$file" up -d; then
        echo
        success_msg "容器已启动"
        info_msg "Web UI 默认 http://<host>:<port>/web-ui（host/port 以 config.yaml 为准）"
    else
        echo
        error_msg "启动失败"
    fi
    press_any_key
}

compose_down() {
    compose_prereq || { press_any_key; return; }
    local file; file="$(active_compose_file)"
    info_msg "停止并移除容器（$(basename "$file")）..."
    echo
    if $COMPOSE_CMD -f "$file" down; then
        echo
        success_msg "容器已停止并移除"
    else
        echo
        error_msg "停止失败"
    fi
    press_any_key
}

compose_restart() {
    compose_prereq || { press_any_key; return; }
    ensure_service_config || { press_any_key; return; }
    local file; file="$(active_compose_file)"
    info_msg "重启容器（$(basename "$file")）..."
    echo
    $COMPOSE_CMD -f "$file" down || true
    if $COMPOSE_CMD -f "$file" up -d; then
        echo
        success_msg "容器已重启"
    else
        echo
        error_msg "重启失败"
    fi
    press_any_key
}

compose_logs() {
    compose_prereq || { press_any_key; return; }
    local file; file="$(active_compose_file)"
    info_msg "实时日志（Ctrl+C 返回菜单）..."
    echo
    $COMPOSE_CMD -f "$file" logs --tail=50 -f || true
    press_any_key
}

# 打开 config.yaml 编辑（保存后下次 compose 启动生效）
edit_service_config() {
    ensure_service_config || { press_any_key; return; }
    local editor="${EDITOR:-}"
    if [ -z "$editor" ]; then
        if command -v nano &>/dev/null; then editor="nano"
        elif command -v vim &>/dev/null; then editor="vim"
        else editor="vi"; fi
    fi
    info_msg "使用 $editor 打开 asr-service/config.yaml ..."
    "$editor" "$SERVICE_CONFIG" || true
    press_any_key
}

toggle_compose_variant() {
    show_menu "选择 Compose 编排" \
        "GPU（docker-compose.yml）" \
        "CPU（docker-compose.cpu.yml）" \
        "vLLM（docker-compose.vllm.yml · GPU 原生流式 · 8766）"
    case $MENU_RESULT in
        0) ACTIVE_COMPOSE_VARIANT="gpu" ;;
        1) ACTIVE_COMPOSE_VARIANT="cpu" ;;
        2) ACTIVE_COMPOSE_VARIANT="vllm" ;;
    esac
    success_msg "已切换为 $ACTIVE_COMPOSE_VARIANT 编排"
    if [ "$ACTIVE_COMPOSE_VARIANT" = "vllm" ]; then
        info_msg "vLLM 镜像 lancelrq/qwen3-asr-service:latest-vllm；首次需先拉取或构建（docker/build.sh 选 4）"
    fi
    press_any_key
}

menu_compose() {
    while true; do
        clear
        if [ "$HAS_COMPOSE" -eq 0 ]; then
            warn_msg "Docker Compose 未安装，部分功能不可用"
            echo
        fi
        show_menu "Docker Compose 管理（当前：$ACTIVE_COMPOSE_VARIANT · config.yaml 驱动）" \
            "1. 启动容器（up -d）" \
            "2. 停止容器（down）" \
            "3. 重启容器" \
            "4. 查看日志" \
            "5. 编辑 config.yaml 配置" \
            "6. 切换 GPU / CPU / vLLM 编排" \
            "0. 返回主菜单"
        case $MENU_RESULT in
            0) compose_up ;;
            1) compose_down ;;
            2) compose_restart ;;
            3) compose_logs ;;
            4) edit_service_config ;;
            5) toggle_compose_variant ;;
            6) return ;;
        esac
    done
}

# ============================================================
# 虚拟环境管理
# ============================================================
# 安装/重装虚拟环境：target = standard（venv）| vllm（venv-vllm）
# 已存在时由 setup.sh 自身交互询问是否重装，故此处不重复确认
venv_install_or_reinstall() {
    local target="${1:-standard}"
    local setup_flag="" label="standard"
    if [ "$target" = "vllm" ]; then
        setup_flag="--vllm"; label="vLLM（venv-vllm）"
    fi
    if [ ! -f "$SERVICE_DIR/setup.sh" ]; then
        error_msg "未找到 setup.sh"
        press_any_key
        return
    fi
    if [ "$target" = "vllm" ] && [ "$HAS_GPU" -eq 0 ]; then
        warn_msg "未检测到 GPU —— vLLM 环境装好后仍需 CUDA 才能启动"
    fi
    info_msg "运行安装脚本（$label；已存在则脚本会询问是否重装）..."
    echo
    (cd "$SERVICE_DIR" && bash setup.sh $setup_flag) || true
    check_prerequisites   # 刷新 venv / venv-vllm 检测
    press_any_key
}

venv_remove() {
    if [ "$HAS_VENV" -eq 0 ] && [ "$HAS_VENV_VLLM" -eq 0 ]; then
        warn_msg "无虚拟环境可删除"
        press_any_key
        return
    fi
    show_menu "删除哪个虚拟环境？" \
        "standard（venv）" \
        "vLLM（venv-vllm）" \
        "取消"
    local target_dir=""
    case $MENU_RESULT in
        0) target_dir="venv" ;;
        1) target_dir="venv-vllm" ;;
        2) return ;;
    esac
    if [ ! -d "$SERVICE_DIR/$target_dir" ]; then
        warn_msg "$target_dir 不存在"
        press_any_key
        return
    fi
    if ! confirm "确定要删除 $target_dir？"; then
        info_msg "已取消"
        return
    fi
    info_msg "删除 $target_dir ..."
    rm -rf "$SERVICE_DIR/$target_dir"
    check_prerequisites
    success_msg "$target_dir 已删除"
    press_any_key
}

# 单个环境的版本信息（dir=venv|venv-vllm；present=1 才打印）
_venv_info_one() {
    local dir="$1" label="$2" present="$3"
    [ "$present" -eq 1 ] || return 0
    local python_bin="$SERVICE_DIR/$dir/bin/python3"
    # 统一用 python -m pip：venv 跨路径迁移后，venv/bin/pip 等控制台脚本的 shebang
    # 仍指向旧的创建路径会报 bad interpreter；-m pip 由解释器定位，不受影响。
    local pip_run=("$python_bin" -m pip)

    printf "${BOLD}%s 环境（%s）：${NC}\n" "$label" "$dir"
    echo "─────────────────────────────────────"
    printf "  路径:         %s/%s\n" "$SERVICE_DIR" "$dir"
    printf "  Python 版本:  %s\n" "$("$python_bin" --version 2>/dev/null || echo '未知')"
    printf "  Pip 版本:     %s\n" "$("${pip_run[@]}" --version 2>/dev/null | awk '{print $2}' || echo '未知')"

    local torch_ver
    torch_ver=$("${pip_run[@]}" show torch 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "未安装")
    printf "  PyTorch:      %s\n" "$torch_ver"

    local qwen_ver
    qwen_ver=$("${pip_run[@]}" show qwen-asr 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "未安装")
    printf "  qwen-asr:     %s\n" "$qwen_ver"

    if [ "$dir" = "venv-vllm" ]; then
        local vllm_ver
        vllm_ver=$("${pip_run[@]}" show vllm 2>/dev/null | grep "^Version:" | awk '{print $2}' || echo "未安装")
        printf "  vLLM:         %s\n" "$vllm_ver"
    fi

    # 包数量：pip 段加 || true，避免 pipefail 下 wc 仍输出而被 "|| echo" 叠加成 "0\n0"
    local pkg_count
    pkg_count=$( { "${pip_run[@]}" list 2>/dev/null || true; } | tail -n +3 | wc -l | tr -d ' ')
    printf "  已安装包:     %s 个\n" "$pkg_count"
    echo "─────────────────────────────────────"
}

venv_info() {
    echo
    if [ "$HAS_VENV" -eq 0 ] && [ "$HAS_VENV_VLLM" -eq 0 ]; then
        warn_msg "尚无虚拟环境（venv / venv-vllm 均未创建）"
        press_any_key
        return
    fi
    _venv_info_one "venv" "standard" "$HAS_VENV"
    _venv_info_one "venv-vllm" "vLLM" "$HAS_VENV_VLLM"
    press_any_key
}

menu_venv() {
    while true; do
        clear
        show_menu "Venv 虚拟环境方式（standard=venv · vLLM=venv-vllm · 本地 start.sh）" \
            "1. 启动服务（参数向导，启动模式在面板内选）" \
            "2. 安装/重装 standard 环境（venv）" \
            "3. 安装/重装 vLLM 环境（venv-vllm，GPU 专用）" \
            "4. 卸载删除环境" \
            "5. 检测版本信息" \
            "0. 返回主菜单"

        case $MENU_RESULT in
            0) venv_start ;;
            1) venv_install_or_reinstall standard ;;
            2) venv_install_or_reinstall vllm ;;
            3) venv_remove ;;
            4) venv_info ;;
            5) return ;;
        esac
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
    # v2 功能（默认沿用 config.example：实时转写/任务持久化开，说话人/声纹库关）
    LAUNCH_ENABLE_STREAM="yes"
    LAUNCH_ENABLE_TASK_STORE="yes"
    LAUNCH_ENABLE_SPEAKER="no"
    LAUNCH_ENABLE_SPEAKER_DB="no"
    # serve-mode：standard（默认）/ vllm（GPU 原生流式，独立 venv-vllm/镜像）
    LAUNCH_SERVE_MODE="standard"
    # vLLM 专属（仅 serve-mode=vllm 生效）：对齐器设备 + 一次对齐/ASR 的音频块数
    LAUNCH_VLLM_ALIGN_DEVICE="cuda"
    LAUNCH_VLLM_INFER_BATCH="4"
    # 兼容接口（两模式通用；vLLM 模式实时随其挂载、无需 --enable-stream）
    LAUNCH_ENABLE_OPENAI="no"
    LAUNCH_ENABLE_DASHSCOPE="no"
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
# 由 manage.sh 自动生成，可手动编辑
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
LAUNCH_ENABLE_STREAM="$LAUNCH_ENABLE_STREAM"
LAUNCH_ENABLE_TASK_STORE="$LAUNCH_ENABLE_TASK_STORE"
LAUNCH_ENABLE_SPEAKER="$LAUNCH_ENABLE_SPEAKER"
LAUNCH_ENABLE_SPEAKER_DB="$LAUNCH_ENABLE_SPEAKER_DB"
LAUNCH_SERVE_MODE="$LAUNCH_SERVE_MODE"
LAUNCH_VLLM_ALIGN_DEVICE="$LAUNCH_VLLM_ALIGN_DEVICE"
LAUNCH_VLLM_INFER_BATCH="$LAUNCH_VLLM_INFER_BATCH"
LAUNCH_ENABLE_OPENAI="$LAUNCH_ENABLE_OPENAI"
LAUNCH_ENABLE_DASHSCOPE="$LAUNCH_ENABLE_DASHSCOPE"
LAUNCH_METHOD="$LAUNCH_METHOD"
EOF
}

print_config_summary() {
    printf "${BOLD}当前启动配置：${NC}\n"
    echo "─────────────────────────────────────"
    printf "  启动模式:     %s\n" "$LAUNCH_SERVE_MODE"
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
    printf "  实时转写:     %s\n" "$([ "$LAUNCH_ENABLE_STREAM" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  任务持久化:   %s\n" "$([ "$LAUNCH_ENABLE_TASK_STORE" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  说话人分离:   %s\n" "$([ "$LAUNCH_ENABLE_SPEAKER" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  声纹库:       %s\n" "$([ "$LAUNCH_ENABLE_SPEAKER_DB" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  OpenAI 兼容:  %s\n" "$([ "$LAUNCH_ENABLE_OPENAI" = "yes" ] && echo "启用" || echo "禁用")"
    printf "  DashScope兼容:%s\n" "$([ "$LAUNCH_ENABLE_DASHSCOPE" = "yes" ] && echo "启用" || echo "禁用")"
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        printf "  vLLM 对齐设备: %s\n" "$LAUNCH_VLLM_ALIGN_DEVICE"
        printf "  vLLM 批大小:   %s\n" "$LAUNCH_VLLM_INFER_BATCH"
    fi
    if [ -n "$LAUNCH_METHOD" ]; then
        printf "  启动方式:     %s\n" "$LAUNCH_METHOD"
    fi
    echo "─────────────────────────────────────"
}

# ── 单项编辑：每个参数独立修改，互不影响 ──

yn_label() { [ "$1" = "yes" ] && echo "启用" || echo "禁用"; }

# 翻转 yes/no 布尔变量（间接展开，兼容 bash 3.2）
toggle_bool() {
    local name="$1"
    if [ "${!name}" = "yes" ]; then
        eval "$name=no"
    else
        eval "$name=yes"
    fi
}

edit_model_size() {
    show_menu "选择模型大小" \
        "auto (根据显存自动选择)" \
        "0.6b (轻量，显存需求低)" \
        "1.7b (完整，效果更好)"
    case $MENU_RESULT in
        0) LAUNCH_MODEL_SIZE="auto" ;;
        1) LAUNCH_MODEL_SIZE="0.6b" ;;
        2) LAUNCH_MODEL_SIZE="1.7b" ;;
    esac
}

edit_device() {
    show_menu "选择运行设备" \
        "auto (自动检测)" \
        "cuda (GPU)" \
        "cpu"
    case $MENU_RESULT in
        0) LAUNCH_DEVICE="auto" ;;
        1) LAUNCH_DEVICE="cuda" ;;
        2) LAUNCH_DEVICE="cpu" ;;
    esac
}

edit_model_source() {
    show_menu "选择模型下载源" \
        "modelscope (国内推荐)" \
        "huggingface (国外)"
    case $MENU_RESULT in
        0) LAUNCH_MODEL_SOURCE="modelscope" ;;
        1) LAUNCH_MODEL_SOURCE="huggingface" ;;
    esac
}

edit_serve_mode() {
    show_menu "选择启动模式（serve-mode）" \
        "standard（默认 · funasr/OpenVINO · CPU/GPU）" \
        "vllm（vLLM 原生流式 · GPU 专用 · 独立 venv-vllm/镜像）"
    case $MENU_RESULT in
        0) LAUNCH_SERVE_MODE="standard" ;;
        1) LAUNCH_SERVE_MODE="vllm" ;;
    esac
}

edit_vllm_align_device() {
    show_menu "vLLM 对齐器加载设备" \
        "cuda（快 · 显存在 gpu_memory_utilization 之外 · 长音频易 OOM）" \
        "cpu（无 GPU 争用 · float32 · 慢但稳）"
    case $MENU_RESULT in
        0) LAUNCH_VLLM_ALIGN_DEVICE="cuda" ;;
        1) LAUNCH_VLLM_ALIGN_DEVICE="cpu" ;;
    esac
}

# 面板行左侧标签（值类项预留对齐空格；start/back 无值不补齐）
panel_label() {
    case "$1" in
        start)        printf "%s" "$2" ;;
        back)         printf "返回（不启动）" ;;
        model_size)   printf "模型大小      " ;;
        device)       printf "运行设备      " ;;
        model_source) printf "模型下载源    " ;;
        align)        printf "对齐模型      " ;;
        punc)         printf "标点恢复      " ;;
        web)          printf "Web UI        " ;;
        max_segment)  printf "最大切片时长  " ;;
        host)         printf "监听地址      " ;;
        port)         printf "监听端口      " ;;
        api_key)      printf "API 密钥      " ;;
        stream)       printf "实时转写      " ;;
        task_store)   printf "任务持久化    " ;;
        speaker)      printf "说话人分离    " ;;
        speaker_db)   printf "声纹库        " ;;
        serve_mode)   printf "启动模式      " ;;
        vllm_align_device) printf "vLLM 对齐设备 " ;;
        vllm_infer_batch)  printf "vLLM 批大小   " ;;
        openai_api)   printf "OpenAI 兼容   " ;;
        dashscope_api) printf "DashScope兼容 " ;;
    esac
}

# 面板行右侧当前值（start/back 返回空串）
panel_value() {
    case "$1" in
        model_size)   printf "%s" "$LAUNCH_MODEL_SIZE" ;;
        device)       printf "%s" "$LAUNCH_DEVICE" ;;
        model_source) printf "%s" "$LAUNCH_MODEL_SOURCE" ;;
        align)        yn_label "$LAUNCH_ENABLE_ALIGN" ;;
        punc)         yn_label "$LAUNCH_USE_PUNC" ;;
        web)          yn_label "$LAUNCH_WEB" ;;
        max_segment)  printf "%s 秒" "$LAUNCH_MAX_SEGMENT" ;;
        host)         printf "%s" "$LAUNCH_HOST" ;;
        port)         printf "%s" "$LAUNCH_PORT" ;;
        api_key)      [ -n "$LAUNCH_API_KEY" ] && printf "已设置" || printf "未设置" ;;
        stream)       yn_label "$LAUNCH_ENABLE_STREAM" ;;
        task_store)   yn_label "$LAUNCH_ENABLE_TASK_STORE" ;;
        speaker)      yn_label "$LAUNCH_ENABLE_SPEAKER" ;;
        speaker_db)   yn_label "$LAUNCH_ENABLE_SPEAKER_DB" ;;
        serve_mode)   printf "%s" "$LAUNCH_SERVE_MODE" ;;
        vllm_align_device) printf "%s" "$LAUNCH_VLLM_ALIGN_DEVICE" ;;
        vllm_infer_batch)  printf "%s" "$LAUNCH_VLLM_INFER_BATCH" ;;
        openai_api)   yn_label "$LAUNCH_ENABLE_OPENAI" ;;
        dashscope_api) yn_label "$LAUNCH_ENABLE_DASHSCOPE" ;;
    esac
}

# 值类项编辑（子菜单或输入），复用既有 edit_* 与 read_input
panel_edit() {
    case "$1" in
        model_size)   edit_model_size ;;
        device)       edit_device ;;
        model_source) edit_model_source ;;
        max_segment)  read_input "VAD 切片合并最大时长（秒）" "$LAUNCH_MAX_SEGMENT"; LAUNCH_MAX_SEGMENT="$INPUT_RESULT" ;;
        host)         read_input "监听地址" "$LAUNCH_HOST"; LAUNCH_HOST="$INPUT_RESULT" ;;
        port)         read_input "监听端口" "$LAUNCH_PORT"; LAUNCH_PORT="$INPUT_RESULT" ;;
        api_key)      read_input "API 密钥（留空则不启用认证）" "$LAUNCH_API_KEY"; LAUNCH_API_KEY="$INPUT_RESULT" ;;
        vllm_align_device) edit_vllm_align_device ;;
        vllm_infer_batch)  read_input "vLLM 一次对齐/ASR 的音频块数（-1=全部一次，长音频易 OOM；小值省显存）" "$LAUNCH_VLLM_INFER_BATCH"; LAUNCH_VLLM_INFER_BATCH="$INPUT_RESULT" ;;
    esac
}

# 面板项集合（依 serve-mode 动态）：standard 显示 punc/stream；
# vllm 显示 vllm_align_device/vllm_infer_batch，隐藏 punc（模型原生标点）与 stream（流式恒开）
panel_keys() {
    local k=(start serve_mode model_size device model_source align)
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        k+=(vllm_align_device vllm_infer_batch)
    else
        k+=(punc)
    fi
    k+=(web max_segment host port api_key)
    [ "$LAUNCH_SERVE_MODE" != "vllm" ] && k+=(stream)
    k+=(task_store speaker speaker_db openai_api dashscope_api back)
    printf '%s\n' "${k[@]}"
}

# 配置面板：自带按键循环，方向键选行、空格/回车就地切换或编辑，
# 选「启动」按当前配置运行；开关翻转后光标停在原行、即时刷新、不弹提示
config_panel() {
    local method="$1"
    local start_label
    case "$method" in
        docker) start_label="▶ 启动 Docker 容器（按当前配置）" ;;
        *)      start_label="▶ 启动服务（按当前配置）" ;;
    esac

    local keys=() _k
    while IFS= read -r _k; do keys+=("$_k"); done < <(panel_keys)
    local nrows=${#keys[@]}
    local selected=0 first_draw=1 hint=""

    printf '\033[?25l'   # 隐藏光标
    clear                # 清屏，面板独占一屏，避免与上层菜单残留叠加

    while true; do
        # 就地重绘：非首次绘制时上移（标题+空行+nrows+提示行）
        [ "$first_draw" -eq 0 ] && printf '\033[%dA' $((nrows + 3))
        first_draw=0

        printf '\033[2K'
        printf "${BOLD}${CYAN}配置启动参数${NC} ${DIM}· ↑↓ 选择 · 空格/回车 修改 · 选「启动」运行${NC}\n"
        printf '\033[2K\n'

        local i k label val line
        for i in "${!keys[@]}"; do
            k="${keys[$i]}"
            label=$(panel_label "$k" "$start_label")
            val=$(panel_value "$k")
            if [ -n "$val" ]; then line="${label}: ${val}"; else line="$label"; fi
            printf '\033[2K'
            if [ "$i" -eq "$selected" ]; then
                printf "  ${REVERSE} %s ${NC}\n" "$line"
            else
                printf "    %s\n" "$line"
            fi
        done

        printf '\033[2K'
        printf "  ${DIM}%s${NC}\n" "$hint"
        hint=""

        local key rest
        IFS= read -rsn1 key || { printf '\033[?25h'; exit 0; }
        case "$key" in
            $'\x1b')                 # 方向键
                read -rsn2 -t 0.1 rest || true
                case "$rest" in
                    '[A') selected=$(( (selected - 1 + nrows) % nrows )) ;;
                    '[B') selected=$(( (selected + 1) % nrows )) ;;
                esac
                ;;
            ' '|'')                  # 空格 或 回车
                case "${keys[$selected]}" in
                    start)
                        printf '\033[?25h'
                        save_launch_config
                        echo; print_config_summary; echo
                        case "$method" in
                            docker) launch_via_docker ;;
                            venv)   launch_via_venv ;;
                        esac
                        return
                        ;;
                    back)
                        printf '\033[?25h'
                        return
                        ;;
                    serve_mode)
                        printf '\033[?25h'; echo
                        edit_serve_mode
                        # serve-mode 改变 → 面板项集合变化，重建 keys 并夹紧光标
                        keys=(); while IFS= read -r _k; do keys+=("$_k"); done < <(panel_keys)
                        nrows=${#keys[@]}
                        [ "$selected" -ge "$nrows" ] && selected=$((nrows - 1))
                        save_launch_config
                        printf '\033[?25l'; clear; first_draw=1
                        ;;
                    openai_api)    toggle_bool LAUNCH_ENABLE_OPENAI;    save_launch_config ;;
                    dashscope_api) toggle_bool LAUNCH_ENABLE_DASHSCOPE; save_launch_config ;;
                    align)      toggle_bool LAUNCH_ENABLE_ALIGN;      save_launch_config ;;
                    punc)       toggle_bool LAUNCH_USE_PUNC;          save_launch_config ;;
                    web)        toggle_bool LAUNCH_WEB;               save_launch_config ;;
                    stream)     toggle_bool LAUNCH_ENABLE_STREAM;     save_launch_config ;;
                    task_store) toggle_bool LAUNCH_ENABLE_TASK_STORE; save_launch_config ;;
                    speaker)
                        toggle_bool LAUNCH_ENABLE_SPEAKER
                        # 关闭说话人分离时，依赖它的声纹库一并关闭
                        if [ "$LAUNCH_ENABLE_SPEAKER" != "yes" ] && [ "$LAUNCH_ENABLE_SPEAKER_DB" = "yes" ]; then
                            LAUNCH_ENABLE_SPEAKER_DB="no"
                            hint="声纹库依赖说话人分离，已一并关闭"
                        fi
                        save_launch_config
                        ;;
                    speaker_db)
                        toggle_bool LAUNCH_ENABLE_SPEAKER_DB
                        if [ "$LAUNCH_ENABLE_SPEAKER_DB" = "yes" ]; then
                            if [ "$LAUNCH_ENABLE_SPEAKER" != "yes" ]; then
                                LAUNCH_ENABLE_SPEAKER="yes"
                                hint="已自动开启说话人分离（声纹库依赖）"
                            fi
                            if [ -z "$LAUNCH_API_KEY" ]; then
                                [ -n "$hint" ] && hint="$hint；"
                                hint="${hint}声纹库需配置 API 密钥，否则启动会被拒绝"
                            fi
                        fi
                        save_launch_config
                        ;;
                    *)                # 值类项：进入子编辑（子菜单/输入）后清屏重绘
                        printf '\033[?25h'
                        echo
                        panel_edit "${keys[$selected]}"
                        save_launch_config
                        printf '\033[?25l'
                        clear         # 抹掉子编辑输出与旧帧，面板回到屏顶重绘
                        first_draw=1
                        ;;
                esac
                ;;
        esac
    done
}

build_launch_args() {
    local args=""
    local is_vllm="no"
    [ "$LAUNCH_SERVE_MODE" = "vllm" ] && is_vllm="yes"

    # serve-mode：vllm 时显式声明（standard 不输出该 flag，保持与既有行为一致）
    if [ "$is_vllm" = "yes" ]; then
        args+=" --serve-mode vllm"
    fi

    if [ "$LAUNCH_MODEL_SIZE" != "auto" ]; then
        args+=" --model-size $LAUNCH_MODEL_SIZE"
    fi
    args+=" --device $LAUNCH_DEVICE"
    args+=" --model-source $LAUNCH_MODEL_SOURCE"

    # 对齐模型：standard 与 vllm 用不同 flag 名
    if [ "$is_vllm" = "yes" ]; then
        if [ "$LAUNCH_ENABLE_ALIGN" = "yes" ]; then
            args+=" --vllm-enable-align"
        else
            args+=" --no-vllm-align"
        fi
        args+=" --vllm-align-device $LAUNCH_VLLM_ALIGN_DEVICE"
        args+=" --vllm-infer-batch-size $LAUNCH_VLLM_INFER_BATCH"
    else
        if [ "$LAUNCH_ENABLE_ALIGN" = "yes" ]; then
            args+=" --enable-align"
        else
            args+=" --no-align"
        fi
        # 标点恢复仅 standard（vllm 模型原生标点，无单独开关）
        if [ "$LAUNCH_USE_PUNC" = "yes" ]; then
            args+=" --use-punc"
        fi
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

    # 实时转写：standard 需显式开关；vllm 流式恒开（不输出 --enable-stream/--no-stream）
    if [ "$is_vllm" != "yes" ]; then
        if [ "$LAUNCH_ENABLE_STREAM" = "yes" ]; then
            args+=" --enable-stream"
        else
            args+=" --no-stream"
        fi
    fi

    # v2 功能：显式正/反 flag，覆盖容器内 config.yaml 的默认值（两模式通用）
    if [ "$LAUNCH_ENABLE_TASK_STORE" = "yes" ]; then
        args+=" --enable-task-store"
    else
        args+=" --no-task-store"
    fi

    if [ "$LAUNCH_ENABLE_SPEAKER" = "yes" ]; then
        args+=" --enable-speaker"
    else
        args+=" --no-speaker"
    fi

    if [ "$LAUNCH_ENABLE_SPEAKER_DB" = "yes" ]; then
        args+=" --enable-speaker-db"
    else
        args+=" --no-speaker-db"
    fi

    # 兼容接口（两模式通用；vllm 模式实时随其挂载、无需 --enable-stream）
    if [ "$LAUNCH_ENABLE_OPENAI" = "yes" ]; then
        args+=" --enable-openai-api"
    else
        args+=" --no-openai-api"
    fi

    if [ "$LAUNCH_ENABLE_DASHSCOPE" = "yes" ]; then
        args+=" --enable-dashscope-api"
    else
        args+=" --no-dashscope-api"
    fi

    echo "$args"
}

launch_via_venv() {
    local args
    args=$(build_launch_args)

    # serve-mode 决定用哪个虚拟环境：standard→venv，vllm→venv-vllm
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        if [ "$HAS_VENV_VLLM" -eq 0 ]; then
            error_msg "vLLM 环境（venv-vllm）未创建，请先在 Venv 菜单安装 vLLM 环境"
            press_any_key
            return
        fi
        if [ "$HAS_GPU" -eq 0 ]; then
            warn_msg "未检测到 GPU —— vLLM 模式必须 CUDA，启动可能失败"
            if ! confirm "仍要继续？"; then return; fi
        fi
    else
        if [ "$HAS_VENV" -eq 0 ]; then
            error_msg "虚拟环境未创建，请先安装"
            press_any_key
            return
        fi
    fi

    echo
    printf "${BOLD}启动命令：${NC}\n"
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        printf "  QWEN_VENV=venv-vllm bash start.sh%s\n" "$args"
    else
        printf "  bash start.sh%s\n" "$args"
    fi
    echo

    # || true：start.sh 非零退出（Ctrl-C 停服 / 端口占用 / 模型加载失败）不应触发
    # set -e 下的 EXIT trap 终止整个 CLI，应返回菜单（与 docker/install 各路径一致）
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        (cd "$SERVICE_DIR" && QWEN_VENV=venv-vllm bash start.sh $args) || true
    else
        (cd "$SERVICE_DIR" && bash start.sh $args) || true
    fi
}

launch_via_docker() {
    local args
    args=$(build_launch_args)

    if [ "$HAS_DOCKER" -eq 0 ]; then
        error_msg "Docker 未安装"
        press_any_key
        return
    fi

    # 镜像 tag 按 serve-mode 选择（vllm 用 -vllm 变体，且对无 GPU 提醒）
    local image_tag="$IMAGE_TAG"
    if [ "$LAUNCH_SERVE_MODE" = "vllm" ]; then
        image_tag="${IMAGE_TAG}-vllm"
        if [ "$HAS_GPU" -eq 0 ]; then
            warn_msg "未检测到 GPU —— vLLM 镜像必须 CUDA，启动可能失败"
            if ! confirm "仍要继续？"; then return; fi
        fi
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
    # Docker 容器内需监听 0.0.0.0 才能从外部访问；临时覆盖 LAUNCH_HOST 重建参数，
    # 避免对用户输入的 host 做 sed 正则替换（含 / 或正则元字符会破坏整串参数）
    local docker_args
    docker_args=$(LAUNCH_HOST="0.0.0.0" build_launch_args)

    local gpu_flag=""
    if [ "$HAS_GPU" -eq 1 ]; then
        gpu_flag="--gpus all"
    fi

    local cmd="docker run -d ${gpu_flag} \\
    -p ${LAUNCH_PORT}:${LAUNCH_PORT} \\
    -v \"${SERVICE_DIR}/models:/app/models\" \\
    -v \"${SERVICE_DIR}/logs:/app/logs\" \\
    -v \"${SERVICE_DIR}/data:/app/data\" \\
    --name ${CONTAINER_NAME} \\
    ${IMAGE_NAME}:${image_tag} \\
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
    run_args+=("-v" "${SERVICE_DIR}/models:/app/models")
    run_args+=("-v" "${SERVICE_DIR}/logs:/app/logs")
    run_args+=("-v" "${SERVICE_DIR}/data:/app/data")
    if [ -n "$LAUNCH_API_KEY" ]; then
        run_args+=("-e" "ASR_API_KEY=${LAUNCH_API_KEY}")
    fi
    run_args+=("--name" "${CONTAINER_NAME}")
    run_args+=("${IMAGE_NAME}:${image_tag}")
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

# 参数向导启动：method = docker | venv。启动方式由调用的子菜单决定，不再单独询问。
# 首次（无已保存配置）自动进入配置流程；已有配置则可沿用 / 重新配置。
launch_wizard() {
    local method="$1"
    echo
    default_config
    load_launch_config || true       # 有保存则覆盖默认值，无则用默认
    LAUNCH_METHOD="$method"          # 启动方式以当前入口为准
    config_panel "$method"
}

# venv 方式启动入口（供「Venv 虚拟环境方式」子菜单调用）
# 具体环境（venv / venv-vllm）由参数面板的 serve-mode 决定，launch_via_venv 内再校验对应环境
venv_start() {
    if [ "$HAS_VENV" -eq 0 ] && [ "$HAS_VENV_VLLM" -eq 0 ]; then
        error_msg "尚无虚拟环境，请先安装 standard 或 vLLM 环境"
        press_any_key
        return
    fi
    launch_wizard venv
}

# ============================================================
# .gitignore 维护
# ============================================================
ensure_gitignore() {
    local gitignore="$SERVICE_DIR/.gitignore"
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
        show_menu "请选择启动方式" \
            "1. Docker Compose 方式（推荐）" \
            "2. Docker 容器方式" \
            "3. Venv 虚拟环境方式" \
            "0. 退出"

        case $MENU_RESULT in
            0) menu_compose ;;
            1) menu_docker ;;
            2) menu_venv ;;
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
