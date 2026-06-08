import logging
import os
import sys
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.utils.logger import setup_logger
from app.utils.arg_schema import build_parser, ARG_SPECS
from app.utils.config_file import merge_runtime_config
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


def parse_args(argv=None):
    """解析 CLI 并应用配置链：schema 默认值 < 环境变量 < 配置文件 < CLI 显式参数。

    参数定义见 app/utils/arg_schema.py（单一 schema，argparse 全 SUPPRESS）；
    配置文件的发现/引导生成/校验/合并见 app/utils/config_file.py。
    """
    cli_ns = build_parser().parse_args(argv)
    return merge_runtime_config(cli_ns)


# 值为 None 时回填的 cfg 真实默认值（"生效配置"不应打"(未指定)"误导成未生效）
_CFG_FALLBACK_ATTRS = {
    "host": "HOST", "port": "PORT", "max_queue_size": "MAX_QUEUE_SIZE",
    "max_stream_sessions": "MAX_STREAM_SESSIONS",
    "stream_asr_concurrency": "STREAM_ASR_CONCURRENCY",
}


def _log_effective_config(args):
    """启动时打印生效配置（四层合并结果），便于核对实际运行参数；api_key 脱敏。

    分组随 ArgSpec.group 声明走（新参数在定义处即归组）；None 值回填 cfg
    真实默认值并标注"(默认)"，model_size 标注"(自动选择)"（装配时按显存解析，
    结果见后续"运行配置"日志行）。
    输出只用 ASCII 字符（=/-/.）做边框与点线对齐——框线字符（─│┌）在部分
    控制台字体/缩放下半宽全宽不一致会走样，这里刻意避开。
    """
    def fmt(spec):
        val = getattr(args, spec.attr, None)
        if spec.key == "api_key" and val:
            val = (val[:4] + "****") if len(val) > 4 else "****"
        if val is None:
            if spec.key in _CFG_FALLBACK_ATTRS:
                val = f"{getattr(cfg, _CFG_FALLBACK_ATTRS[spec.key])} (默认)"
            elif spec.key == "model_size":
                val = "(自动选择)"
            else:
                val = "(未指定)"
        dots = "." * max(2, 25 - len(spec.key))
        return f"    {spec.key} {dots} {val}"

    groups = {}
    order = []
    for spec in ARG_SPECS:
        if spec.group not in groups:
            groups[spec.group] = []
            order.append(spec.group)
        groups[spec.group].append(spec)

    bar = "=" * 62
    lines = ["", bar,
             "  Qwen3-ASR 生效配置（默认值 < 环境变量 < 配置文件 < CLI）",
             f"  配置文件: {cfg.CONFIG_FILE or '未使用'}",
             "-" * 62]
    for title in order:
        lines.append(f"  [{title}]")
        lines.extend(fmt(s) for s in groups[title])
    lines.append(bar)
    logger.info("\n".join(lines))


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
    if getattr(args, "vad_speech_noise_thres", None) is not None:
        cfg.VAD_SPEECH_NOISE_THRES = args.vad_speech_noise_thres
    cfg.STREAM_NOISE_FILTER = getattr(args, "stream_noise_filter", False)
    if getattr(args, "stream_energy_floor_dbfs", None) is not None:
        cfg.STREAM_ENERGY_FLOOR_DBFS = args.stream_energy_floor_dbfs
    if getattr(args, "stream_snr_min_db", None) is not None:
        cfg.STREAM_SNR_MIN_DB = args.stream_snr_min_db
    cfg.ENABLE_SPEAKER = getattr(args, "enable_speaker", False)
    if getattr(args, "speaker_threshold", None) is not None:
        cfg.SPEAKER_THRESHOLD = args.speaker_threshold
    if getattr(args, "speaker_max", None) is not None:
        cfg.SPEAKER_MAX = args.speaker_max
    if getattr(args, "speaker_min_seg_ms", None) is not None:
        cfg.SPEAKER_MIN_SEG_MS = args.speaker_min_seg_ms
    if getattr(args, "speaker_max_windows", None) is not None:
        cfg.SPEAKER_MAX_WINDOWS = args.speaker_max_windows
    cfg.ENABLE_SPEAKER_DB = getattr(args, "enable_speaker_db", False)
    if getattr(args, "speaker_db_path", None) is not None:
        cfg.SPEAKER_DB_PATH = args.speaker_db_path
    if getattr(args, "speaker_id_threshold", None) is not None:
        cfg.SPEAKER_ID_THRESHOLD = args.speaker_id_threshold
    if getattr(args, "speaker_id_margin", None) is not None:
        cfg.SPEAKER_ID_MARGIN = args.speaker_id_margin
    if getattr(args, "speaker_enroll_min_sec", None) is not None:
        cfg.SPEAKER_ENROLL_MIN_SEC = args.speaker_enroll_min_sec
    cfg.SPEAKER_AUTO_ENROLL = getattr(args, "speaker_auto_enroll", True)
    if getattr(args, "speaker_auto_enroll_min_sec", None) is not None:
        cfg.SPEAKER_AUTO_ENROLL_MIN_SEC = args.speaker_auto_enroll_min_sec
    cfg.SPEAKER_STORE_AUDIO = getattr(args, "speaker_store_audio", False)
    if cfg.API_KEY:
        logger.info("API 密钥已配置，Bearer token 认证已启用")


def create_app(args=None) -> FastAPI:
    """创建并配置 FastAPI 应用，按 --serve-mode 分派装配。"""
    if args is None:
        args = parse_args()

    # 1. 配置日志
    setup_logger()
    logger.info("Qwen3-ASR Service 启动中...")
    _log_effective_config(args)

    # 2. 写入全局配置（模式无关）
    _apply_cli_config(args)

    serve_mode = getattr(args, "serve_mode", "standard")
    app = FastAPI(title="Qwen3-ASR Service", version="2.0.0")
    # 响应压缩（vendored 前端库 1.7MB → ~426KB；仅作用于 HTTP，WS 不受影响）
    app.add_middleware(GZipMiddleware, minimum_size=1024)

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

    # 说话人分离引擎（可选）：加载失败降级关闭，不影响转写主链路（容错对齐标点）
    speaker_engine = None
    if cfg.ENABLE_SPEAKER:
        from app.engines.speaker_embedding_engine import SpeakerEmbeddingEngine
        speaker_engine = SpeakerEmbeddingEngine()
        try:
            speaker_engine.load()
        except Exception as e:
            logger.warning(f"说话人引擎加载失败，已降级关闭: {e}")
            speaker_engine = None
    speaker_enabled = speaker_engine is not None

    # 声纹库（可选）：降级矩阵按序检查，任一失败 = ERROR 日志 + 模块关闭、服务继续启动
    speaker_service = None
    speaker_store = None
    speaker_tag_mismatch = False
    if getattr(args, "enable_speaker_db", False):
        if speaker_engine is None:                       # ① 依赖分离引擎
            logger.error("声纹库需要 --enable-speaker 且说话人引擎加载成功，已降级关闭")
        elif not cfg.API_KEY:                            # ② 合规硬规则
            logger.error("声纹库要求配置 api_key（声纹属生物识别信息，"
                         "不允许无鉴权访问），已降级关闭")
        else:
            from app.engines.speaker_embedding_engine import SpeakerEmbeddingEngine
            from app.runtime.speaker_store import SpeakerStore
            from app.runtime.speaker_service import SpeakerService
            spk_db_path = cfg.SPEAKER_DB_PATH
            if not os.path.isabs(spk_db_path):
                spk_db_path = os.path.join(cfg.BASE_DIR, spk_db_path)
            try:
                speaker_store = SpeakerStore(
                    spk_db_path, model_tag=SpeakerEmbeddingEngine.MODEL_TAG)
                # ③ model_tag 失配：仅禁登记/识别（503），GET/DELETE 保留（被遗忘权）
                speaker_tag_mismatch = not speaker_store.check_model_tag(
                    SpeakerEmbeddingEngine.MODEL_TAG)
                if speaker_tag_mismatch:
                    logger.error("声纹库 model_tag 与当前引擎不一致：登记/识别已禁用，"
                                 "管理端点保留（如需重建请删除库文件或迁移模板）")
                speaker_service = SpeakerService(speaker_store, speaker_engine, vad_engine)
            except Exception as e:                       # ④ 建库失败
                logger.error(f"声纹库初始化失败，已降级关闭: {e}")
                speaker_service = None
                speaker_store = None
    speaker_db_enabled = speaker_service is not None and not speaker_tag_mismatch
    # 转写联动仅在识别可用时注入（失配 = 库内模板与当前引擎不可比，联动同样禁用）
    linked_speaker_service = speaker_service if speaker_db_enabled else None

    # 创建 Pipeline
    pipeline = ASRPipeline(
        asr_engine=asr_engine,
        vad_engine=vad_engine,
        punc_engine=punc_engine,
        speaker_engine=speaker_engine,
        speaker_service=linked_speaker_service,
    )

    # 任务持久化（可选）：建库失败只告警不中断启动（附属能力不拖垮主链路）
    task_store = None
    if getattr(args, "enable_task_store", False):
        from app.runtime.task_store import TaskStore
        db_path = args.task_db_path
        if not os.path.isabs(db_path):
            db_path = os.path.join(cfg.BASE_DIR, db_path)
        try:
            task_store = TaskStore(db_path, retention_days=args.task_retention_days)
            dangling = task_store.close_dangling()
            if dangling:
                logger.warning(f"上次退出时有 {dangling} 个未完成任务，已标记为失败（service restarted）")
            expired = task_store.cleanup_expired()
            if expired:
                logger.info(f"已清理 {expired} 个过期历史任务（>{args.task_retention_days} 天）")
        except Exception as e:
            logger.error(f"任务持久化初始化失败，本次以纯内存模式运行: {e}")
            task_store = None

    # 创建任务管理器
    task_manager = TaskManager(max_queue_size=cfg.MAX_QUEUE_SIZE, store=task_store)

    def process_task(task: dict):
        def on_progress(p):
            task_manager.update_progress(task["task_id"], p)

        return pipeline.run(
            audio_path=task["file_path"],
            task_id=task["task_id"],
            language=task.get("language"),
            progress_callback=on_progress,
            cancelled=lambda: task_manager.is_stopping or task_manager.is_cancelled(task["task_id"]),
            identify_speakers=task.get("identify_speakers", False),
            options=task.get("options"),
        )

    task_manager.set_processor(process_task)
    task_manager.start()

    # 构建服务信息（mode-aware，供 /health、/capabilities 使用）
    stream_enabled = getattr(args, "enable_stream", False)
    capabilities = {
        "mode": "standard",
        "offline_api": True,
        "speaker_labels": speaker_enabled,
        "speaker_identification": speaker_db_enabled,
        "stream": {
            "enabled": stream_enabled,
            "backend": "vad-offline" if stream_enabled else None,
            "path": "/v2/asr/stream" if stream_enabled else None,
            "partial_results": False,
            "word_timestamps": enable_align if stream_enabled else False,
            "speaker_labels": speaker_enabled if stream_enabled else False,
        },
    }
    service_info = {
        "status": "ready",
        "mode": "standard",
        "device": device,
        "model_size": model_size,
        "align_enabled": enable_align,
        "punc_enabled": enable_punc,
        "speaker_enabled": speaker_enabled,
        "speaker_db_enabled": speaker_db_enabled,
        "asr_backend": asr_backend,
        "vad_backend": VADEngine.BACKEND,
        "punc_backend": PuncEngine.BACKEND if enable_punc else "disabled",
        "config_file": cfg.CONFIG_FILE,
        "capabilities": capabilities,
    }

    # 共性路由（两模式都挂）
    init_common(service_info)
    app.include_router(build_common_router("/v1"))
    app.include_router(build_common_router("/v2"))

    # 离线路由：v1（含 deprecated 别名）+ v2（同名复用）
    init_routes(task_manager, task_store)
    app.include_router(build_offline_router("/v1", include_deprecated=True))
    app.include_router(build_offline_router("/v2"))

    # 声纹库路由（仅 /v2）：无条件挂载——未启用/降级时端点统一 503
    from app.api.speaker_routes import init_speaker_routes, build_speakers_router
    init_speaker_routes(speaker_service, tag_mismatch=speaker_tag_mismatch)
    app.include_router(build_speakers_router())
    if speaker_db_enabled:
        logger.info(f"声纹库已启用：/v2/speakers*（{speaker_store.speaker_count} 人，"
                    f"自动登记={'开' if cfg.SPEAKER_AUTO_ENROLL else '关'}）")

    # 实时 Route B：按 --enable-stream 挂载统一端点 WS /v2/asr/stream
    stream_backend = None
    if stream_enabled:
        from app.api.ws_routes import ws_router_stream, init_ws_stream
        from app.runtime.stream_session import VadOfflineBackend
        stream_backend = VadOfflineBackend(
            asr_engine, vad_engine, punc_engine,
            speaker=speaker_engine,
            speaker_service=linked_speaker_service,
            max_sessions=cfg.MAX_STREAM_SESSIONS,
            asr_concurrency=cfg.STREAM_ASR_CONCURRENCY,
            max_segment_sec=cfg.STREAM_MAX_SEGMENT_SEC,
            vad_chunk_ms=cfg.STREAM_VAD_CHUNK_MS,
            noise_filter=cfg.STREAM_NOISE_FILTER,
            energy_floor_dbfs=cfg.STREAM_ENERGY_FLOOR_DBFS,
            snr_min_db=cfg.STREAM_SNR_MIN_DB,
        )
        init_ws_stream(stream_backend)
        app.include_router(ws_router_stream)
        logger.info("实时转写已启用：WS /v2/asr/stream（路线B / vad-offline）")

    # 条件挂载 Web UI
    if getattr(args, "web", False):
        from app.web.views import web_router, ASSETS_DIR
        app.include_router(web_router)
        app.mount("/web-ui/assets", StaticFiles(directory=ASSETS_DIR), name="web-assets")
        logger.info(f"Web UI 已启用，访问 http://{cfg.HOST}:{cfg.PORT}/web-ui")

    @app.on_event("shutdown")
    def on_shutdown():
        logger.info("收到终止信号，正在安全关闭服务...")
        worker_exited = task_manager.shutdown()
        if stream_backend is not None:
            stream_backend.shutdown()
        if speaker_store is not None:
            speaker_store.close()
        if task_store is not None:
            if worker_exited:
                task_store.close()
            else:
                # 工作线程仍在收尾（finalize 落库中），跳过 close 避免竞态；
                # WAL 模式下进程退出后可恢复，悬挂任务由下次启动 close_dangling 收口
                logger.warning("工作线程未在超时内退出，跳过任务库连接关闭")
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
        "config_file": cfg.CONFIG_FILE,
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
    uvicorn.run(
        "app.main:get_app", host=cfg.HOST, port=cfg.PORT, reload=False, factory=True,
        # 实时流式：放宽 keepalive 与接收队列，配合应用层收发解耦/积压上限，
        # 避免推理负载高时 pong 读取延迟被误判为超时（1011 keepalive ping timeout）
        ws_ping_timeout=60,
        ws_max_queue=256,
    )
