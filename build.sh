#!/usr/bin/env bash
set -e

IMAGE_NAME="qwen3-asr-service"
VERSION=$(git describe --tags --always 2>/dev/null || echo "latest")

# 代理设置（可通过环境变量或参数传入）
PROXY=${1:-${HTTP_PROXY:-${http_proxy:-}}}
BUILD_ARGS=""
if [ -n "$PROXY" ]; then
    echo "Using proxy: ${PROXY}"
    BUILD_ARGS="--build-arg HTTP_PROXY=${PROXY} --build-arg HTTPS_PROXY=${PROXY}"
fi

echo "Building ${IMAGE_NAME}:${VERSION} ..."
docker build ${BUILD_ARGS} -t "${IMAGE_NAME}:${VERSION}" -t "${IMAGE_NAME}:latest" .

echo ""
echo "Build complete:"
echo "  ${IMAGE_NAME}:${VERSION}"
echo "  ${IMAGE_NAME}:latest"
echo ""
echo "Run example:"
echo "  docker run --gpus all -p 8765:8765 -v /path/to/models:/app/models ${IMAGE_NAME}:latest"
