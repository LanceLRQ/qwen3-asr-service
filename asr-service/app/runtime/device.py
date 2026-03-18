import logging

logger = logging.getLogger(__name__)


def detect_device() -> dict:
    """
    检测运行设备，返回设备信息。

    返回:
        {"type": "cuda"|"cpu", "vram_gb": float|None, "name": str|None}
    """
    try:
        import torch
        if torch.cuda.is_available():
            vram = torch.cuda.get_device_properties(0).total_memory / 1024**3
            name = torch.cuda.get_device_name(0)
            logger.info(f"检测到 GPU: {name}, VRAM: {vram:.1f}GB")
            return {"type": "cuda", "vram_gb": round(vram, 1), "name": name}
    except ImportError:
        pass

    logger.info("使用 CPU 模式")
    return {"type": "cpu", "vram_gb": None, "name": None}


def resolve_device(requested: str) -> str:
    """
    根据用户请求和硬件情况，确定最终使用的设备。

    参数:
        requested: "auto" | "cuda" | "cpu"

    返回:
        "cuda" | "cpu"
    """
    info = detect_device()

    if requested == "cpu":
        return "cpu"

    if requested == "cuda":
        if info["type"] != "cuda":
            raise RuntimeError("请求使用 CUDA 但未检测到可用 GPU")
        return "cuda"

    # auto 模式
    return info["type"]


def auto_select_model_size(vram_gb: float | None) -> str:
    """根据显存自动选择模型大小"""
    if vram_gb is None:
        return "0.6b"
    if vram_gb >= 6:
        return "1.7b"
    return "0.6b"


def should_disable_align(device: str, vram_gb: float | None) -> bool:
    """判断是否需要强制关闭对齐"""
    if device == "cpu":
        return True
    if vram_gb is not None and vram_gb < 4:
        return True
    return False
