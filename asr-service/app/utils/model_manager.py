import os
import logging
from app.config import MODEL_SOURCE

logger = logging.getLogger(__name__)


def ensure_model(repo_id: str, local_dir: str):
    """
    确保模型已下载到本地。
    根据 MODEL_SOURCE 配置选择下载源，目录非空则跳过。
    """
    if os.path.exists(local_dir) and os.listdir(local_dir):
        logger.info(f"模型已存在: {local_dir}")
        return

    if MODEL_SOURCE == "manual":
        raise FileNotFoundError(
            f"模型未找到: {local_dir}\n"
            f"当前配置为手动模式，请将模型文件放入该目录后重启服务。"
        )

    logger.info(f"开始下载模型 [{MODEL_SOURCE}]: {repo_id} -> {local_dir}")
    os.makedirs(local_dir, exist_ok=True)

    if MODEL_SOURCE == "modelscope":
        from modelscope import snapshot_download
        snapshot_download(
            model_id=repo_id,
            local_dir=local_dir,
        )
    else:
        from huggingface_hub import snapshot_download
        snapshot_download(
            repo_id=repo_id,
            local_dir=local_dir,
        )

    logger.info(f"模型下载完成: {local_dir}")


def ensure_model_hf(repo_id: str, local_dir: str):
    """强制从 HuggingFace 下载（用于仅 HF 提供的模型，如 PANNs 32k / YAMNet）。

    不受 MODEL_SOURCE 影响（这些仓库 ModelScope 无可信镜像，见音频标注设计 §5）；
    手动模式下要求用户自行放置。
    """
    if os.path.exists(local_dir) and os.listdir(local_dir):
        logger.info(f"模型已存在: {local_dir}")
        return

    if MODEL_SOURCE == "manual":
        raise FileNotFoundError(
            f"模型未找到: {local_dir}\n"
            f"当前为手动模式，请将模型文件放入该目录后重启服务。\n"
            f"下载地址: https://huggingface.co/{repo_id}"
        )

    logger.info(f"开始下载模型 [huggingface]: {repo_id} -> {local_dir}")
    os.makedirs(local_dir, exist_ok=True)

    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo_id, local_dir=local_dir)

    logger.info(f"模型下载完成: {local_dir}")


def ensure_file(url: str, local_path: str, min_bytes: int = 0, timeout: float = 600.0):
    """从直链 URL 下载单个文件（用于非 repo 的权重，如 Zenodo 的 PANNs 16k）。

    已存在且大小达标则跳过；manual 模式要求用户自行放置。流式下载到 .part 临时文件，
    校验大小后原子改名，避免中断留下半截文件。
    """
    if os.path.exists(local_path) and os.path.getsize(local_path) >= max(min_bytes, 1):
        logger.info(f"文件已存在: {local_path}")
        return

    if MODEL_SOURCE == "manual":
        raise FileNotFoundError(
            f"文件未找到: {local_path}\n"
            f"当前为手动模式，请从以下地址下载并放入该路径后重启服务:\n{url}"
        )

    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    logger.info(f"开始下载文件: {url} -> {local_path}")

    import httpx
    tmp = local_path + ".part"
    with httpx.stream("GET", url, follow_redirects=True, timeout=timeout) as resp:
        resp.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                f.write(chunk)

    size = os.path.getsize(tmp)
    if size < min_bytes:
        os.remove(tmp)
        raise RuntimeError(f"下载文件过小（{size} B < {min_bytes} B），疑似失败或被拦截: {url}")
    os.replace(tmp, local_path)
    logger.info(f"文件下载完成: {local_path}（{size} B）")


def ensure_model_modelscope(repo_id: str, local_dir: str):
    """
    强制从 ModelScope 下载模型（用于仅 ModelScope 提供的模型，如 VAD、标点）。
    手动模式下同样要求用户自行放置。
    """
    if os.path.exists(local_dir) and os.listdir(local_dir):
        logger.info(f"模型已存在: {local_dir}")
        return

    if MODEL_SOURCE == "manual":
        raise FileNotFoundError(
            f"模型未找到: {local_dir}\n"
            f"当前配置为手动模式，请将模型文件放入该目录后重启服务。\n"
            f"下载地址: https://modelscope.cn/models/{repo_id}"
        )

    logger.info(f"开始下载模型 [modelscope]: {repo_id} -> {local_dir}")
    os.makedirs(local_dir, exist_ok=True)

    from modelscope import snapshot_download
    snapshot_download(
        model_id=repo_id,
        local_dir=local_dir,
    )

    logger.info(f"模型下载完成: {local_dir}")
