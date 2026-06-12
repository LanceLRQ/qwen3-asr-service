#!/usr/bin/env bash
set -e

# 切到仓库根：构建上下文必须是根目录（Dockerfile 内的 COPY 路径相对此处），
# 脚本已移入 docker/，故从自身位置回退一级定位根，支持任意目录调用。
cd "$(dirname "$0")/.."

IMAGE_NAME="lancelrq/qwen3-asr-service"

# 本地构建默认走华科镜像加速 APT 源；可用环境变量覆盖（置空则用 Ubuntu 官方源）
APT_MIRROR="${APT_MIRROR-mirrors.hust.edu.cn}"

# vLLM 镜像基底 tag（仅 vLLM 变体用，由 V0 预研锁定；可用环境变量覆盖）
VLLM_TAG="${VLLM_TAG:-v0.14.0}"

# 选择构建版本：GPU / CPU / ARM64 / vLLM
echo "请选择构建版本："
echo "  1) GPU（默认）"
echo "  2) CPU"
echo "  3) ARM64（CPU，适用于 Apple Silicon 等 arm64 平台）"
echo "  4) vLLM（GPU/amd64，原生流式，独立镜像）"
read -rp "请输入 [1/2/3/4]（回车默认 1）: " variant_choice
case "${variant_choice}" in
    2)   VARIANT="cpu" ;;
    3)   VARIANT="arm64" ;;
    4)   VARIANT="vllm" ;;
    *)   VARIANT="gpu" ;;
esac

# 输入版本号
case "$VARIANT" in
    cpu)   SUFFIX="-cpu" ;;
    arm64) SUFFIX="-arm64" ;;
    vllm)  SUFFIX="-vllm" ;;
    *)     SUFFIX="" ;;
esac
read -rp "请输入版本号（回车默认 latest）: " input_ver
VER="${input_ver:-latest}"
TAG="${VER}${SUFFIX}"

# 注入镜像内版本号（FastAPI version / OpenAPI）；latest 不是有效语义版本，回退 dev
if [ "$VER" = "latest" ]; then
    APP_VER="dev"
else
    APP_VER="$VER"
fi

# 构建
echo ""
case "$VARIANT" in
    cpu)
        echo "Building ${IMAGE_NAME}:${TAG} (CPU, amd64) ..."
        docker build -f docker/Dockerfile.cpu --build-arg APP_VERSION="${APP_VER}" --build-arg APT_MIRROR="${APT_MIRROR}" -t "${IMAGE_NAME}:${TAG}" .
        ;;
    arm64)
        echo "Building ${IMAGE_NAME}:${TAG} (CPU, arm64) ..."
        docker buildx build --platform linux/arm64 \
            -f docker/Dockerfile.cpu --build-arg APP_VERSION="${APP_VER}" --build-arg APT_MIRROR="${APT_MIRROR}" -t "${IMAGE_NAME}:${TAG}" --load .
        ;;
    vllm)
        echo "Building ${IMAGE_NAME}:${TAG} (vLLM, GPU/amd64, base vllm/vllm-openai:${VLLM_TAG}) ..."
        docker build -f docker/Dockerfile.vllm --build-arg APP_VERSION="${APP_VER}" --build-arg VLLM_TAG="${VLLM_TAG}" -t "${IMAGE_NAME}:${TAG}" .
        ;;
    *)
        echo "Building ${IMAGE_NAME}:${TAG} (GPU) ..."
        docker build -f docker/Dockerfile --build-arg APP_VERSION="${APP_VER}" --build-arg APT_MIRROR="${APT_MIRROR}" -t "${IMAGE_NAME}:${TAG}" .
        ;;
esac

# 输出结果
echo ""
echo "Build complete: ${IMAGE_NAME}:${TAG}"
echo ""
echo "Run example:"
if [ "$VARIANT" = "gpu" ] || [ "$VARIANT" = "vllm" ]; then
    echo "  docker run --gpus all -p 8765:8765 -v /path/to/models:/app/models ${IMAGE_NAME}:${TAG}"
else
    echo "  docker run -p 8765:8765 -v /path/to/models:/app/models ${IMAGE_NAME}:${TAG}"
fi
