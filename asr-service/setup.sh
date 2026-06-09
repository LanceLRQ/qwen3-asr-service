#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  Qwen3-ASR Service Environment Setup"
echo "=========================================="

# 1. Check Python3 version (by platform)
PYTHON_BIN=""

detect_python() {
    local os_name="$(uname -s)"

    if [ "$os_name" = "Darwin" ]; then
        # macOS: prefer python3, use directly if 3.10
        if command -v python3 &> /dev/null; then
            local ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if [ "$ver" = "3.10" ]; then
                PYTHON_BIN="python3"
                echo "[INFO] macOS: Detected Python $ver, meets requirements"
                return 0
            fi
        fi

        # Default python3 is not 3.10, try homebrew python@3.12
        if [ -x "/opt/homebrew/opt/python@3.12/bin/python3.12" ]; then
            PYTHON_BIN="/opt/homebrew/opt/python@3.12/bin/python3.12"
            echo "[INFO] macOS: Using Homebrew Python 3.12"
            return 0
        fi

        echo "[ERROR] No suitable Python version found (requires 3.10 or 3.12)"
        echo "[ERROR] Please run: brew install python@3.10"
        exit 1

    elif [ "$os_name" = "Linux" ]; then
        # Linux: requires python3 to be 3.12
        if command -v python3 &> /dev/null; then
            local ver=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if [ "$ver" = "3.12" ]; then
                PYTHON_BIN="python3"
                echo "[INFO] Linux: Detected Python $ver, meets requirements"
                return 0
            fi
            echo "[ERROR] Current Python version is $ver, requires 3.12"
        else
            echo "[ERROR] python3 not found"
        fi
        echo "[ERROR] Please install Python 3.12 and try again"
        exit 1

    else
        echo "[ERROR] Unsupported OS: $os_name"
        exit 1
    fi
}

# 0. Linux users are advised to use Docker image
if [ "$(uname -s)" = "Linux" ]; then
    echo ""
    echo "[Recommended] Linux detected, Docker image deployment is recommended (ready to use, no manual setup needed):"
    echo ""
    echo "  Option 1: docker run"
    echo ""
    echo "    docker pull lancelrq/qwen3-asr-service:latest"
    echo "    docker run -d --gpus all -p 8765:8765 \\"
    echo "      -v ./models:/app/models \\"
    echo "      -v ./logs:/app/logs \\"
    echo "      lancelrq/qwen3-asr-service:latest \\"
    echo "      --model-size=1.7b --device=auto --model-source=modelscope \\"
    echo "      --enable-align --web --max-segment=20"
    echo ""
    echo "  Option 2: docker-compose"
    echo ""
    echo "    A docker/docker-compose.yml is provided in the project, just run:"
    echo "    docker compose -f docker/docker-compose.yml up -d"
    echo ""
    read -p "Continue with local installation? [y/N]: " CONTINUE_LOCAL
    CONTINUE_LOCAL=${CONTINUE_LOCAL:-N}
    case "$CONTINUE_LOCAL" in
        [Yy]|[Yy][Ee][Ss])
            echo "[INFO] Continuing with local installation..."
            ;;
        *)
            echo "[INFO] Local installation cancelled. Please use Docker for deployment."
            exit 0
            ;;
    esac
    echo ""
fi

detect_python
PYTHON_VERSION=$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "[INFO] Python version: $PYTHON_VERSION (path: $(command -v $PYTHON_BIN))"

# 2. Create venv
if [ -d "venv" ]; then
    echo "[INFO] Existing virtual environment detected"
    read -p "Delete and reinstall? [y/N]: " REINSTALL_VENV
    REINSTALL_VENV=${REINSTALL_VENV:-N}
    case "$REINSTALL_VENV" in
        [Yy]|[Yy][Ee][Ss])
            echo "[INFO] Removing old virtual environment..."
            rm -rf venv
            echo "[INFO] Creating virtual environment..."
            $PYTHON_BIN -m venv venv
            ;;
        *)
            echo "[INFO] Keeping existing virtual environment, skipping creation"
            ;;
    esac
else
    echo "[INFO] Creating virtual environment..."
    $PYTHON_BIN -m venv venv
fi

source venv/bin/activate

# 3. Upgrade pip
echo "[INFO] Upgrading pip..."
pip install --upgrade pip

# 4. Install PyTorch (based on GPU availability)
if command -v nvidia-smi &> /dev/null; then
    echo "[INFO] NVIDIA GPU detected, installing CUDA PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
else
    echo "[INFO] No GPU detected, installing CPU PyTorch..."
    pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
fi

# 5. Install other dependencies
echo "[INFO] Installing project dependencies..."
pip install -r requirements.txt

# 6. Create necessary directories
mkdir -p models/asr/0.6b models/asr/1.7b models/align/0.6b models/vad/fsmn models/vad/fsmn-onnx models/punc/ct-transformer models/punc/ct-transformer-onnx logs

# 7. Select model download method
echo ""
echo "=========================================="
echo "  Model Configuration"
echo "=========================================="
echo ""
echo "Select model source:"
echo "  1) ModelScope (recommended for China, faster download)"
echo "  2) HuggingFace"
echo "  3) Manual (skip download, prepare model files yourself)"
echo ""
read -p "Enter choice [1/2/3] (default 1): " MODEL_CHOICE
MODEL_CHOICE=${MODEL_CHOICE:-1}

case $MODEL_CHOICE in
    1)
        MODEL_SOURCE="modelscope"
        echo "[INFO] Selected ModelScope as download source"
        ;;
    2)
        MODEL_SOURCE="huggingface"
        echo "[INFO] Selected HuggingFace as download source"
        echo "[INFO] Note: VAD and punctuation models are only available on ModelScope, they will be downloaded from ModelScope automatically"
        ;;
    3)
        MODEL_SOURCE="manual"
        echo "[INFO] Selected manual mode"
        echo ""
        echo "=========================================="
        echo "  Manual Model Placement Guide"
        echo "=========================================="
        echo ""
        echo "Please place model files in the following directories:"
        echo ""
        echo "  Qwen3-ASR-0.6B (ASR lightweight, GPU VRAM 4-6GB):"
        echo "    -> $(pwd)/models/asr/0.6b/"
        echo ""
        echo "  Qwen3-ASR-1.7B (ASR full, GPU VRAM >= 6GB):"
        echo "    -> $(pwd)/models/asr/1.7b/"
        echo ""
        echo "  Qwen3-ForcedAligner-0.6B (word-level timestamp alignment):"
        echo "    -> $(pwd)/models/align/0.6b/"
        echo ""
        echo "  VAD model (voice activity detection):"
        echo "    -> $(pwd)/models/vad/fsmn/"
        echo ""
        echo "  Punctuation model (automatic punctuation):"
        echo "    -> $(pwd)/models/punc/ct-transformer/"
        echo ""
        echo "Model sources:"
        echo "  ModelScope:"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ASR-0.6B"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B"
        echo "    https://modelscope.cn/models/Qwen/Qwen3-ForcedAligner-0.6B"
        echo "    https://modelscope.cn/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
        echo "    https://modelscope.cn/models/iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch"
        echo ""
        echo "  HuggingFace (ASR and align models only; VAD/punctuation must be obtained from ModelScope):"
        echo "    https://huggingface.co/Qwen/Qwen3-ASR-0.6B"
        echo "    https://huggingface.co/Qwen/Qwen3-ASR-1.7B"
        echo "    https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B"
        echo ""
        echo "Once done, run: bash start.sh"
        echo "=========================================="
        ;;
    *)
        MODEL_SOURCE="modelscope"
        echo "[INFO] Invalid option, defaulting to ModelScope"
        ;;
esac

echo ""
echo "=========================================="
echo "  Environment Setup Complete"
echo "=========================================="
echo ""
echo "Use --model-source to specify the download source when starting the service:"
echo "  bash start.sh --model-source $MODEL_SOURCE"
echo ""
