#!/usr/bin/env bash
# 兼容接口端到端一键测试：在独立 venv 里起 mock 服务 + 跑客户端冒烟。
#
# 默认对 mock 服务测试（无需真模型，验证端点/SDK 字段/WS 握手/SSE/错误码/下载链路）。
# 传 --base-url 可改测一个真实运行的服务（此时不起 mock）：
#   ./run.sh --base-url http://127.0.0.1:8765 --api-key sk-xxx
#
# 首次运行自动建 venv 并装依赖（scripts/e2e/.venv，已 gitignore）。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASR_ROOT="$(cd "$HERE/../.." && pwd)"
VENV="$HERE/.venv"
PYBIN="$VENV/bin/python"
PORT="${E2E_PORT:-8799}"

# 是否用户自带 --base-url（自带则不起 mock）
USE_MOCK=1
for arg in "$@"; do
  case "$arg" in
    --base-url|--base-url=*) USE_MOCK=0 ;;
  esac
done

# 1. 建 venv + 装依赖（幂等：venv 存在即跳过）
if [ ! -x "$PYBIN" ]; then
  echo "[e2e] 创建独立 venv: $VENV"
  python3 -m venv "$VENV"
  "$PYBIN" -m pip install -q --upgrade pip
  echo "[e2e] 安装依赖 (scripts/e2e/requirements.txt) ..."
  "$PYBIN" -m pip install -q -r "$HERE/requirements.txt"
fi

SRV_PID=""
cleanup() { [ -n "$SRV_PID" ] && kill "$SRV_PID" 2>/dev/null || true; }
trap cleanup EXIT

ARGS=("$@")
if [ "$USE_MOCK" -eq 1 ]; then
  echo "[e2e] 启动 mock 服务 :$PORT（无真模型）"
  PYTHONPATH="$ASR_ROOT" "$PYBIN" "$HERE/mock_server.py" --port "$PORT" &
  SRV_PID=$!
  # 等端口就绪（最多 ~15s）
  for _ in $(seq 1 150); do
    if "$PYBIN" -c "import socket;socket.create_connection(('127.0.0.1',$PORT),0.3).close()" 2>/dev/null; then
      break
    fi
    sleep 0.1
  done
  ARGS+=(--base-url "http://127.0.0.1:$PORT")
fi

echo "[e2e] 运行客户端冒烟 ..."
"$PYBIN" "$HERE/compat_e2e.py" "${ARGS[@]}"
