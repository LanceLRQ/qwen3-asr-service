#!/usr/bin/env bash
set -e

IMAGE_NAME="lancelrq/qwen3-asr-service"

# 选择构建版本：GPU / CPU / ARM64
echo "请选择构建版本："
echo "  1) GPU（默认）"
echo "  2) CPU"
echo "  3) ARM64（CPU，适用于 Apple Silicon 等 arm64 平台）"
read -rp "请输入 [1/2/3]（回车默认 1）: " variant_choice
case "${variant_choice}" in
    2)   VARIANT="cpu" ;;
    3)   VARIANT="arm64" ;;
    *)   VARIANT="gpu" ;;
esac

# 输入版本号
case "$VARIANT" in
    cpu)   SUFFIX="-cpu" ;;
    arm64) SUFFIX="-arm64" ;;
    *)     SUFFIX="" ;;
esac
read -rp "请输入版本号（回车默认 latest）: " input_ver
VER="${input_ver:-latest}"
TAG="${VER}${SUFFIX}"

# 构建
echo ""
case "$VARIANT" in
    cpu)
        echo "Building ${IMAGE_NAME}:${TAG} (CPU, amd64) ..."
        docker build -f Dockerfile.cpu -t "${IMAGE_NAME}:${TAG}" .
        ;;
    arm64)
        echo "Building ${IMAGE_NAME}:${TAG} (CPU, arm64) ..."
        docker buildx build --platform linux/arm64 \
            -f Dockerfile.cpu -t "${IMAGE_NAME}:${TAG}" --load .
        ;;
    *)
        echo "Building ${IMAGE_NAME}:${TAG} (GPU) ..."
        docker build -t "${IMAGE_NAME}:${TAG}" .
        ;;
esac

# 输出结果
echo ""
echo "Build complete: ${IMAGE_NAME}:${TAG}"
echo ""
echo "Run example:"
if [ "$VARIANT" = "gpu" ]; then
    echo "  docker run --gpus all -p 8765:8765 -v /path/to/models:/app/models ${IMAGE_NAME}:${TAG}"
else
    echo "  docker run -p 8765:8765 -v /path/to/models:/app/models ${IMAGE_NAME}:${TAG}"
fi
