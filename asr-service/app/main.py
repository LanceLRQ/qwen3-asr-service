import argparse
import logging
import sys
import uvicorn
from fastapi import FastAPI

from app.utils.logger import setup_logger
import app.config as cfg
from app.runtime.device import detect_device, resolve_device, auto_select_model_size, should_disable_align
from app.runtime.task_manager import TaskManager
from app.engines.qwen_asr_engine import QwenASREngine
from app.engines.vad_engine import VADEngine
from app.engines.punc_engine import PuncEngine
from app.pipeline.audio_preprocessor import check_ffmpeg
from app.pipeline.asr_pipeline import ASRPipeline
from app.api.routes import init_routes, build_offline_router
from app.api.common_routes import init_common, build_common_router

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description="Qwen3-ASR Service")
    parser.add_argument(
        "--serve-mode", dest="serve_mode", choices=["standard", "vllm"], default="standard",
        help="服务运行模式：standard=transformers/OpenVINO 离线+实时(路线B)；"
             "vllm=vLLM 原生流式(Phase 3) (default: standard)",
    )
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
        "--use-punc", dest="enable_punc", action="store_true", default=False,
        help="启用标点恢复",
    )
    parser.add_argument(
        "--model-source", choices=["modelscope", "huggingface"], default="modelscope",
        help="模型下载源 (default: modelscope)",
    )
    parser.add_argument(
        "--host", default=None,
        help="监听地址 (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port", type=int, default=None,
        help="监听端口 (default: 8765)",
    )
    parser.add_argument(
        "--web", action="store_true", default=False,
        help="启用 Web UI (访问 /web-ui)",
    )
    parser.add_argument(
        "--max-segment", type=int, default=5,
        help="VAD 切片合并最大时长，单位秒 (default: 5)",
    )
    parser.add_argument(
        "--api-key", default=None,
        help="API 密钥，设置后启用 Bearer token 认证（覆盖 ASR_API_KEY 环境变量）",
    )
    parser.add_argument(
        "--max-queue-size", type=int, default=None,
        help=f"任务队列最大长度 (default: {cfg.MAX_QUEUE_SIZE})",
    )
    parser.add_argument(
        "--enable-stream", dest="enable_stream", action="store_true", default=False,
        help="挂载实时转写端点 WS /v2/asr/stream（路线B，standard 模式）",
    )
    parser.add_argument(
        "--max-stream-sessions", type=int, default=None,
        help=f"实时最大并发会话数 (default: {cfg.MAX_STREAM_SESSIONS})",
    )
    parser.add_argument(
        "--stream-asr-concurrency", type=int, default=None,
        help=f"实时 ASR 解码并发上限 (default: {cfg.STREAM_ASR_CONCURRENCY})",
    )
    return parser.parse_args()


def _apply_cli_config(args):
    """将命令行参数写入全局配置（模式无关部分）"""
    cfg.MODEL_SOURCE = args.model_source
    cfg.MAX_SEGMENT_DURATION = args.max_segment
    if args.host is not None:
        cfg.HOST = args.host
    if args.port is not None:
        cfg.PORT = args.port
    if args.api_key is not None:
        cfg.API_KEY = args.api_key
    if args.max_queue_size is not None:
        cfg.MAX_QUEUE_SIZE = args.max_queue_size
    cfg.SERVE_MODE = getattr(args, "serve_mode", "standard")
    cfg.ENABLE_STREAM = getattr(args, "enable_stream", False)
    if getattr(args, "max_stream_sessions", None) is not None:
        cfg.MAX_STREAM_SESSIONS = args.max_stream_sessions
    if getattr(args, "stream_asr_concurrency", None) is not None:
        cfg.STREAM_ASR_CONCURRENCY = args.stream_asr_concurrency
    if cfg.API_KEY:
        logger.info("API 密钥已配置，Bearer token 认证已启用")


def create_app(args=None) -> FastAPI:
    """创建并配置 FastAPI 应用，按 --serve-mode 分派装配。"""
    if args is None:
        args = parse_args()

    # 1. 配置日志
    setup_logger()
    logger.info("Qwen3-ASR Service 启动中...")

    # 2. 写入全局配置（模式无关）
    _apply_cli_config(args)

    serve_mode = getattr(args, "serve_mode", "standard")
    app = FastAPI(title="Qwen3-ASR Service", version="2.0.0")

    if serve_mode == "vllm":
        _assemble_vllm(app, args)
    else:
        _assemble_standard(app, args)

    logger.info(f"Qwen3-ASR Service 就绪（serve-mode={serve_mode}），监听 {cfg.HOST}:{cfg.PORT}")
    return app


def _assemble_standard(app: FastAPI, args) -> None:
    """standard 模式：transformers/OpenVINO 离线引擎 + 离线接口(v1/v2) + 共性接口。
    实时 Route B 的挂载在 T09 接通（见下方 TODO）。"""
    # ffmpeg（离线格式转换依赖）
    check_ffmpeg()
    logger.info("ffmpeg 检测通过")

    # 检测设备并确定运行参数
    device_info = detect_device()
    device = resolve_device(args.device, device_info=device_info)
    is_cpu = device == "cpu"
    vram_gb = device_info.get("vram_gb")
    logger.info(f"当前运行模式：{"CPU" if is_cpu else "CUDA"}")

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
        f"align={enable_align}, punc={enable_punc}"
    )

    # 加载引擎
    device_map = "cuda:0" if device == "cuda" else "cpu"

    # VAD 引擎（必须）
    vad_engine = VADEngine()
    try:
        vad_engine.load()
    except Exception as e:
        logger.critical(f"VAD 模型加载失败，服务无法启动: {e}")
        sys.exit(1)

    # ASR 引擎（必须）—— CPU 使用 OpenVINO，GPU 使用 Qwen ASR
    if is_cpu:
        from app.engines.openvino_asr_engine import OpenVINOASREngine
        asr_engine = OpenVINOASREngine(model_size=model_size)
        asr_backend = "openvino"
    else:
        asr_engine = QwenASREngine(
            model_size=model_size,
            device=device_map,
            enable_align=enable_align,
        )
        asr_backend = "qwen_asr"
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
        punc_engine = PuncEngine()
        try:
            punc_engine.load()
        except Exception as e:
            logger.warning(f"标点模型加载失败，降级为无标点模式: {e}")
            punc_engine = None
            enable_punc = False

    # 创建 Pipeline
    pipeline = ASRPipeline(
        asr_engine=asr_engine,
        vad_engine=vad_engine,
        punc_engine=punc_engine,
    )

    # 创建任务管理器
    task_manager = TaskManager(max_queue_size=cfg.MAX_QUEUE_SIZE)

    def process_task(task: dict):
        def on_progress(p):
            task_manager.update_progress(task["task_id"], p)

        return pipeline.run(
            audio_path=task["file_path"],
            task_id=task["task_id"],
            language=task.get("language"),
            progress_callback=on_progress,
            cancelled=lambda: task_manager.is_stopping or task_manager.is_cancelled(task["task_id"]),
        )

    task_manager.set_processor(process_task)
    task_manager.start()

    # 构建服务信息（mode-aware，供 /health、/capabilities 使用）
    stream_enabled = getattr(args, "enable_stream", False)
    capabilities = {
        "mode": "standard",
        "offline_api": True,
        "stream": {
            "enabled": stream_enabled,
            "backend": "vad-offline" if stream_enabled else None,
            "path": "/v2/asr/stream" if stream_enabled else None,
            "partial_results": False,
            "word_timestamps": enable_align if stream_enabled else False,
        },
    }
    service_info = {
        "status": "ready",
        "mode": "standard",
        "device": device,
        "model_size": model_size,
        "align_enabled": enable_align,
        "punc_enabled": enable_punc,
        "asr_backend": asr_backend,
        "vad_backend": VADEngine.BACKEND,
        "punc_backend": PuncEngine.BACKEND if enable_punc else "disabled",
        "capabilities": capabilities,
    }

    # 共性路由（两模式都挂）
    init_common(service_info)
    app.include_router(build_common_router("/v1"))
    app.include_router(build_common_router("/v2"))

    # 离线路由：v1（含 deprecated 别名）+ v2（同名复用）
    init_routes(task_manager)
    app.include_router(build_offline_router("/v1", include_deprecated=True))
    app.include_router(build_offline_router("/v2"))

    # 实时 Route B：按 --enable-stream 挂载统一端点 WS /v2/asr/stream
    stream_backend = None
    if stream_enabled:
        from app.api.ws_routes import ws_router_stream, init_ws_stream
        from app.runtime.stream_session import VadOfflineBackend
        stream_backend = VadOfflineBackend(
            asr_engine, vad_engine, punc_engine,
            max_sessions=cfg.MAX_STREAM_SESSIONS,
            asr_concurrency=cfg.STREAM_ASR_CONCURRENCY,
            max_segment_sec=cfg.STREAM_MAX_SEGMENT_SEC,
            vad_chunk_ms=cfg.STREAM_VAD_CHUNK_MS,
        )
        init_ws_stream(stream_backend)
        app.include_router(ws_router_stream)
        logger.info("实时转写已启用：WS /v2/asr/stream（路线B / vad-offline）")

    # 条件挂载 Web UI
    if getattr(args, "web", False):
        from app.web.views import web_router
        app.include_router(web_router)
        logger.info(f"Web UI 已启用，访问 http://{cfg.HOST}:{cfg.PORT}/web-ui")

    @app.on_event("shutdown")
    def on_shutdown():
        logger.info("收到终止信号，正在安全关闭服务...")
        task_manager.shutdown()
        if stream_backend is not None:
            stream_backend.shutdown()
        logger.info("Qwen3-ASR Service 已安全退出")

    logger.info(f"运行模式: {service_info}")


def _assemble_vllm(app: FastAPI, args) -> None:
    """vllm 模式占位（Phase 3 启用）：仅挂共性接口，不加载 transformers/OpenVINO 引擎。

    vLLM 原生流式（路线 A）的引擎与实时端点将在 Phase 3（T12/T13）接入；
    当前仅通过 /health、/capabilities 暴露模式与"未启用"能力。
    """
    logger.warning(
        "serve-mode=vllm：vLLM 原生流式为 Phase 3 功能，当前未启用。"
        "本模式仅提供 /health 与 /capabilities；实时端点将在 Phase 3 接入。"
        "如需离线/实时(路线B)功能，请使用 --serve-mode standard。"
    )

    device_info = detect_device()
    device = resolve_device(args.device, device_info=device_info)

    capabilities = {
        "mode": "vllm",
        "offline_api": False,
        "stream": {
            "enabled": False,          # Phase 3 接入后置位
            "backend": "vllm-native",
            "path": None,
            "partial_results": False,
            "word_timestamps": False,
        },
    }
    service_info = {
        "status": "ready",
        "mode": "vllm",
        "device": device,
        "capabilities": capabilities,
    }

    init_common(service_info)
    app.include_router(build_common_router("/v1"))
    app.include_router(build_common_router("/v2"))


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app


if __name__ == "__main__":
    args = parse_args()
    if args.host is not None:
        cfg.HOST = args.host
    if args.port is not None:
        cfg.PORT = args.port
    uvicorn.run("app.main:get_app", host=cfg.HOST, port=cfg.PORT, reload=False, factory=True)
