#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 检查 venv
if [ ! -d "venv" ]; then
    echo "[WARN] 虚拟环境未创建，正在初始化..."
    bash setup.sh
fi

source venv/bin/activate

# 将所有参数传递给 Python 服务
# 示例:
#   bash start.sh --model-size 1.7b --enable-align
#   bash start.sh --device cpu --model-size 0.6b
#   bash start.sh --model-source huggingface
python -m app.main "$@"
