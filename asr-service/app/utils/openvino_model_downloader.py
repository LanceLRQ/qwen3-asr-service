"""
openvino_model_downloader.py
─────────────────────────────────────────────────────────────────────
OpenVINO 模型自动下载（HuggingFace），使用 huggingface_hub.snapshot_download()。
"""
import os
import logging

from app.config import OV_MODEL_REPO_MAP, MODEL_LOCAL_MAP

logger = logging.getLogger(__name__)


def ensure_openvino_model(model_size: str) -> str:
    """
    确保 OpenVINO 模型已下载到本地，返回本地模型目录路径。

    Args:
        model_size: "0.6b" 或 "1.7b"

    Returns:
        本地模型目录的绝对路径
    """
    local_key = f"asr_ov_{model_size}"
    local_dir = MODEL_LOCAL_MAP[local_key]

    # 检查是否已有完整模型文件
    if _is_model_complete(local_dir, model_size):
        logger.info(f"OpenVINO 模型已存在: {local_dir}")
        return local_dir

    # 获取仓库 ID
    repo_id = OV_MODEL_REPO_MAP.get(model_size)
    if not repo_id:
        raise ValueError(
            f"不支持的模型大小: {model_size}，"
            f"可选: {list(OV_MODEL_REPO_MAP.keys())}"
        )

    logger.info(f"开始下载 OpenVINO 模型 [huggingface]: {repo_id} -> {local_dir}")
    os.makedirs(local_dir, exist_ok=True)

    from huggingface_hub import snapshot_download
    snapshot_download(
        repo_id=repo_id,
        local_dir=local_dir,
    )

    logger.info(f"OpenVINO 模型下载完成: {local_dir}")
    return local_dir


def _is_model_complete(model_dir: str, model_size: str = "0.6b") -> bool:
    """检查模型目录是否包含必要的 OpenVINO 模型文件。"""
    if not os.path.exists(model_dir):
        return False

    common_files = [
        "audio_encoder_model.xml",
        "audio_encoder_model.bin",
        "thinker_embeddings_model.xml",
        "thinker_embeddings_model.bin",
        "vocab.json",
    ]

    # 1.7b 使用 KV cache 分离架构，decoder 文件名不同
    if model_size == "1.7b":
        decoder_files = [
            "decoder_prefill_kv_model.xml",
            "decoder_prefill_kv_model.bin",
            "decoder_kv_model.xml",
            "decoder_kv_model.bin",
        ]
    else:
        decoder_files = [
            "decoder_model.xml",
            "decoder_model.bin",
        ]

    for fname in common_files + decoder_files:
        if not os.path.exists(os.path.join(model_dir, fname)):
            return False

    return True
