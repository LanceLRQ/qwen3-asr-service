#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 选择虚拟环境：standard 用 venv，vLLM 用 venv-vllm（由 QWEN_VENV 指定，默认 venv）
VENV_DIR="${QWEN_VENV:-venv}"

# Check venv（缺失则按目标环境自动初始化）
if [ ! -d "$VENV_DIR" ]; then
    echo "[WARN] Virtual environment '$VENV_DIR' not found, initializing..."
    if [ "$VENV_DIR" = "venv-vllm" ]; then
        bash setup.sh --vllm
    else
        bash setup.sh
    fi
fi

# Pass all arguments to the Python service
# Examples:
#   bash start.sh --model-size 1.7b --enable-align
#   bash start.sh --device cpu --model-size 0.6b
#   bash start.sh --model-source huggingface
#   QWEN_VENV=venv-vllm bash start.sh --serve-mode vllm   # vLLM 原生流式（GPU 专用）
"$SCRIPT_DIR/$VENV_DIR/bin/python3" -m app.main "$@"
