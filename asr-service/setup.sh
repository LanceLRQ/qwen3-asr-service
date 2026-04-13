#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Qwen3-ASR Service 环境初始化"
echo "=========================================="

# 1. 检查 Python3 版本（按平台检测）
PYTHON_BIN=""

detect_python() {
    local os_name="$(uname -s)"

    if [ "$os_name" = "Darwin" ]; then
        # macOS: 优先使用 python3，如果为 3.10 则直接用
        if command -v python3 &> /dev/null; then
            local ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if [ "$ver" = "3.10" ]; then
                PYTHON_BIN="python3"
                echo "[INFO] macOS: 检测到 Python $ver，符合要求"
                return 0
            fi
        fi

        # 默认 python3 不是 3.10，尝试 homebrew python@3.12
        if [ -x "/opt/homebrew/opt/python@3.12/bin/python3.12" ]; then
            PYTHON_BIN="/opt/homebrew/opt/python@3.12/bin/python3.12"
            echo "[INFO] macOS: 使用 Homebrew Python 3.12"
            return 0
        fi

        echo "[ERROR] 未找到合适的 Python 版本（需要 3.10 或 3.12）"
        echo "[ERROR] 请执行: brew install python@3.10"
        exit 1

    elif [ "$os_name" = "Linux" ]; then
        # Linux: 要求 python3 为 3.12
        if command -v python3 &> /dev/null; then
            local ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if [ "$ver" = "3.12" ]; then
                PYTHON_BIN="python3"
                echo "[INFO] Linux: 检测到 Python $ver，符合要求"
                return 0
            fi
            echo "[ERROR] 当前 Python 版本为 $ver，需要 3.12"
        else
            echo "[ERROR] 未找到 python3"
        fi
        echo "[ERROR] 请安装 Python 3.12 后重试"
        exit 1

    else
        echo "[ERROR] 不支持的操作系统: $os_name"
        exit 1
    fi
}

# 0. Linux 环境下建议使用 Docker 镜像
if [ "$(uname -s)" = "Linux" ]; then
    echo ""
    echo "[推荐] 检测到 Linux 环境，建议使用 Docker 镜像部署，开箱即用无需手动配置环境："
    echo ""
    echo "  方式一：docker run"
    echo ""
    echo "    docker pull lancelrq/qwen3-asr-service:latest"
    echo "    docker run -d --gpus all -p 8765:8765 \\"
    echo "      -v ./models:/app/models \\"
    echo "      -v ./logs:/app/logs \\"
    echo "      lancelrq/qwen3-asr-service:latest \\"
    echo "      --model-size=1.7b --device=auto --model-source=modelscope \\"
    echo "      --enable-align --web --max-segment=20"
    echo ""
    echo "  方式二：docker-compose"
    echo ""
    echo "    项目已提供 docker-compose.yml，可直接使用："
    echo "    docker compose up -d"
    echo ""
    read -p "是否继续本地安装？[y/N]: " CONTINUE_LOCAL
    CONTINUE_LOCAL=${CONTINUE_LOCAL:-N}
    case "$CONTINUE_LOCAL" in
        [Yy]|[Yy][Ee][Ss]|是|继续)
            echo "[INFO] 继续本地安装..."
            ;;
        *)
            echo "[INFO] 已取消本地安装。请使用 Docker 镜像部署。"
            exit 0
            ;;
    esac
    echo ""
fi

detect_python
PYTHON_VERSION=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[INFO] Python 版本：$PYTHON_VERSION (路径: $(command -v $PYTHON_BIN))"

# 2. 创建 venv
if [ -d "venv" ]; then
    echo "[INFO] 检测到已有虚拟环境"
    read -p "是否删除并重新安装？[y/N]: " REINSTALL_VENV
    REINSTALL_VENV=${REINSTALL_VENV:-N}
    case "$REINSTALL_VENV" in
        [Yy]|[Yy][Ee][Ss]|是|重新安装)
            echo "[INFO] 删除旧虚拟环境..."
            rm -rf venv
            echo "[INFO] 创建虚拟环境..."
            $PYTHON_BIN -m venv venv
            ;;
        *)
            echo "[INFO] 保留已有虚拟环境，跳过创建"
            ;;
    esac
else
    echo "[INFO] 创建虚拟环境..."
    $PYTHON_BIN -m venv venv
fi

source venv/bin/activate

# 3. 升级 pip
echo "[INFO] 升级 pip..."
pip install --upgrade pip

# 4. 安装 PyTorch（根据 GPU 情况）
if command -v nvidia-smi &> /dev/null; then
    echo "[INFO] 检测到 NVIDIA GPU，安装 CUDA 版 PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
else
    echo "[INFO] 未检测到 GPU，安装 CPU 版 PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

# 5. 安装其他依赖
echo "[INFO] 安装项目依赖..."
pip install -r requirements.txt

# 6. 创建必要目录
mkdir -p models/asr/0.6b models/asr/1.7b models/align/0.6b models/vad/fsmn models/vad/fsmn-onnx models/punc/ct-transformer models/punc/ct-transformer-onnx logs

# 7. 选择模型下载方式
echo ""
echo "=========================================="
echo "  模型配置"
echo "=========================================="
echo ""
echo "请选择模型获取方式："
echo "  1) ModelScope（国内推荐，速度快）"
echo "  2) HuggingFace（国外源）"
echo "  3) 手动放置（跳过下载，自行准备模型文件）"
echo ""
read -p "请输入选项 [1/2/3]（默认 1）: " MODEL_CHOICE
MODEL_CHOICE=${MODEL_CHOICE:-1}

case $MODEL_CHOICE in
    1)
        MODEL_SOURCE="modelscope"
        echo "[INFO] 已选择 ModelScope 作为下载源"
        ;;
    2)
        MODEL_SOURCE="huggingface"
        echo "[INFO] 已选择 HuggingFace 作为下载源"
        echo "[INFO] 注意：VAD 和标点模型仅 ModelScope 提供，将自动从 ModelScope 下载"
        ;;
    3)
        MODEL_SOURCE="manual"
        echo "[INFO] 已选择手动模式"
        echo ""
        echo "=========================================="
        echo "  手动放置模型指引"
        echo "=========================================="
        echo ""
        echo "请将模型文件放入以下目录："
        echo ""
        echo "  Qwen3-ASR-0.6B（ASR 轻量，GPU VRAM 4-6GB）："
        echo "    → $(pwd)/models/asr/0.6b/"
        echo ""
        echo "  Qwen3-ASR-1.7B（ASR 完整，GPU VRAM >= 6GB）："
        echo "    → $(pwd)/models/asr/1.7b/"
        echo ""
        echo "  Qwen3-ForcedAligner-0.6B（字级别时间戳对齐）："
        echo "    → $(pwd)/models/align/0.6b/"
        echo ""
        echo "  VAD 模型（语音活动检测）："
        echo "    → $(pwd)/models/vad/fsmn/"
        echo ""
        echo "  标点模型（自动加标点）："
        echo "    → $(pwd)/models/punc/ct-transformer/"
        echo ""
        echo "模型来源："
        echo "  ModelScope:"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ASR-0.6B"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B"
        echo "    https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
        echo "    https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
        echo ""
        echo "  HuggingFace（仅 ASR 和对齐模型，VAD/标点需从 ModelScope 获取）:"
        echo "    https://huggingface.co/Qwen/Qwen3-ASR-0.6B"
        echo "    https://huggingface.co/Qwen/Qwen3-ASR-1.7B"
        echo "    https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B"
        echo ""
        echo "放置完成后执行 bash start.sh 启动服务。"
        echo "=========================================="
        ;;
    *)
        MODEL_SOURCE="modelscope"
        echo "[INFO] 无效选项，默认使用 ModelScope"
        ;;
esac

echo ""
echo "=========================================="
echo "  环境初始化完成"
echo "=========================================="
echo ""
echo "启动服务时请通过 --model-source 指定下载源："
echo "  bash start.sh --model-source $MODEL_SOURCE"
echo ""
