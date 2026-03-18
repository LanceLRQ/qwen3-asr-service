import argparse
import logging
import sys
import uvicorn
from fastapi import FastAPI

from app.utils.logger import setup_logger
from app.config import HOST, PORT
from app.runtime.device import detect_device, resolve_device, auto_select_model_size, should_disable_align
from app.runtime.task_manager import TaskManager
from app.engines.qwen_asr_engine import QwenASREngine
from app.engines.vad_engine import VADEngine
from app.engines.punc_engine import PuncEngine
from app.pipeline.audio_preprocessor import check_ffmpeg
from app.pipeline.asr_pipeline import ASRPipeline
from app.api.routes import router, init_routes

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="ASR Service")
    parser.add_argument(
        "--device", choices=["auto", "cuda", "cpu"], default="auto",
        help="运行设备 (default: auto)",
    )
    parser.add_argument(
        "--model-size", choices=["0.6b", "1.7b"], default=None,
        help="ASR 模型大小 (default: 根据显存自动选择)",
    )
    parser.add_argument(
        "--enable-align", dest="enable_align", action="store_true", default=True,
        help="加载对齐模型 (default)",
    )
    parser.add_argument(
        "--no-align", dest="enable_align", action="store_false",
        help="不加载对齐模型",
    )
    parser.add_argument(
        "--enable-punc", dest="enable_punc", action="store_true", default=True,
        help="启用标点恢复 (default)",
    )
    parser.add_argument(
        "--no-punc", dest="enable_punc", action="store_false",
        help="不启用标点恢复",
    )
    parser.add_argument(
        "--model-source", choices=["modelscope", "huggingface"], default="modelscope",
        help="模型下载源 (default: modelscope)",
    )
    return parser.parse_args()


def create_app(args=None) -> FastAPI:
    """创建并配置 FastAPI 应用"""
    if args is None:
        args = parse_args()

    # 1. 配置日志
    setup_logger()
    logger.info("ASR Service 启动中...")

    # 2. 检测 ffmpeg
    check_ffmpeg()
    logger.info("ffmpeg 检测通过")

    # 3. 写入全局配置
    import app.config as cfg
    cfg.MODEL_SOURCE = args.model_source

    # 4. 检测设备并确定运行参数
    device_info = detect_device()
    device = resolve_device(args.device)
    is_cpu = device == "cpu"
    use_onnx = is_cpu
    vram_gb = device_info.get("vram_gb")

    # 自动选择模型大小
    model_size = args.model_size or auto_select_model_size(vram_gb)

    # 确定对齐开关
    enable_align = args.enable_align
    if should_disable_align(device, vram_gb):
        if enable_align:
            logger.warning("当前设备条件不满足，强制关闭对齐模型")
        enable_align = False

    # 确定标点开关
    enable_punc = args.enable_punc

    logger.info(
        f"运行配置: device={device}, model_size={model_size}, "
        f"align={enable_align}, punc={enable_punc}, onnx={use_onnx}"
    )

    # 5. 加载引擎
    device_map = "cuda:0" if device == "cuda" else "cpu"

    # VAD 引擎（必须）
    vad_engine = VADEngine(use_onnx=use_onnx)
    try:
        vad_engine.load()
    except Exception as e:
        logger.critical(f"VAD 模型加载失败，服务无法启动: {e}")
        sys.exit(1)

    # ASR 引擎（必须）
    asr_engine = QwenASREngine(
        model_size=model_size,
        device=device_map,
        enable_align=enable_align,
    )
    try:
        asr_engine.load()
    except Exception as e:
        logger.critical(f"ASR 模型加载失败，服务无法启动: {e}")
        sys.exit(1)

    # 更新对齐状态（可能在加载时降级）
    enable_align = asr_engine.align_enabled

    # 标点引擎（可选）
    punc_engine = None
    if enable_punc:
        punc_engine = PuncEngine(use_onnx=use_onnx)
        try:
            punc_engine.load()
        except Exception as e:
            logger.warning(f"标点模型加载失败，降级为无标点模式: {e}")
            punc_engine = None
            enable_punc = False

    # 6. 创建 Pipeline
    pipeline = ASRPipeline(
        asr_engine=asr_engine,
        vad_engine=vad_engine,
        punc_engine=punc_engine,
    )

    # 7. 创建任务管理器
    task_manager = TaskManager()

    def process_task(task: dict):
        def on_progress(p):
            task_manager.update_progress(task["task_id"], p)

        return pipeline.run(
            audio_path=task["file_path"],
            task_id=task["task_id"],
            language=task.get("language"),
            progress_callback=on_progress,
        )

    task_manager.set_processor(process_task)
    task_manager.start()

    # 8. 构建服务信息（供 /health 接口使用）
    service_info = {
        "status": "ready",
        "device": device,
        "model_size": model_size,
        "align_enabled": enable_align,
        "punc_enabled": enable_punc,
        "vad_backend": "onnx" if use_onnx else "pytorch",
        "punc_backend": "onnx" if (use_onnx and enable_punc) else ("pytorch" if enable_punc else "disabled"),
    }

    # 9. 创建 FastAPI 应用
    app = FastAPI(title="ASR Service", version="2.0.0")
    init_routes(task_manager, service_info)
    app.include_router(router)

    logger.info(f"ASR Service 就绪，监听 {HOST}:{PORT}")
    logger.info(f"运行模式: {service_info}")
    return app


app = create_app()

if __name__ == "__main__":
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
