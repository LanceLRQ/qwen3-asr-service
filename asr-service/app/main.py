import logging
import os
import sys
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse

from app.utils.logger import setup_logger
from app.utils.arg_schema import build_parser, ARG_SPECS, resolve_help_lang
from app.utils.config_file import merge_runtime_config, run_config_update
import app.config as cfg
from app.runtime.device import detect_device, resolve_device, auto_select_model_size, should_disable_align
from app.api.common_routes import init_common, build_common_router
# standard 专属重型依赖（funasr/transformers/OpenVINO 系引擎、离线管线/路由）容错导入：
# vLLM 专用环境（无 funasr）下置为 None，使 app.main 仍可被 uvicorn factory 加载启动
# vllm 模式；_assemble_standard 调用时若缺失则明确报错退出。保留为模块级名以便测试替身。
try:
    from app.runtime.task_manager import TaskManager
    from app.engines.qwen_asr_engine import QwenASREngine
    from app.engines.vad_engine import VADEngine
    from app.engines.punc_engine import PuncEngine
    from app.pipeline.audio_preprocessor import check_ffmpeg
    from app.pipeline.asr_pipeline import ASRPipeline
    from app.api.routes import init_routes, build_offline_router
    _STANDARD_DEPS_OK = True
except ImportError as _std_imp_err:      # vLLM 环境缺 funasr 等 standard 依赖
    TaskManager = QwenASREngine = VADEngine = PuncEngine = None
    check_ffmpeg = ASRPipeline = init_routes = build_offline_router = None
    _STANDARD_DEPS_OK = False
    _STANDARD_DEPS_ERR = _std_imp_err

logger = logging.getLogger(__name__)


def parse_args(argv=None):
    """解析 CLI 并应用配置链：schema 默认值 < 环境变量 < 配置文件 < CLI 显式参数。

    参数定义见 app/utils/arg_schema.py（单一 schema，argparse 全 SUPPRESS）；
    配置文件的发现/引导生成/校验/合并见 app/utils/config_file.py。
    --help 文案语言先于建 parser 判定（跟随 shell $LANG，--lang 可覆盖），见 resolve_help_lang。
    """
    return merge_runtime_config(_parse_cli(argv))


def _parse_cli(argv=None):
    """解析 CLI 为原始 Namespace（未合并配置链），供需要在合并前拦截元参数的入口使用。

    --help 文案语言先于建 parser 判定（跟随 shell $LANG，--lang 可覆盖），见 resolve_help_lang。
    """
    lang = resolve_help_lang(argv)
    return build_parser(lang).parse_args(argv)


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
    # vLLM（路线 A）
    if getattr(args, "gpu_memory_utilization", None) is not None:
        cfg.VLLM_GPU_MEMORY_UTILIZATION = args.gpu_memory_utilization
    if getattr(args, "vllm_max_model_len", None) is not None:
        cfg.VLLM_MAX_MODEL_LEN = args.vllm_max_model_len
    if getattr(args, "vllm_chunk_size_sec", None) is not None:
        cfg.VLLM_CHUNK_SIZE_SEC = args.vllm_chunk_size_sec
    if getattr(args, "vllm_max_utterance_sec", None) is not None:
        cfg.VLLM_MAX_UTTERANCE_SEC = args.vllm_max_utterance_sec
    if getattr(args, "vllm_concurrency", None) is not None:
        cfg.VLLM_CONCURRENCY = args.vllm_concurrency
    if getattr(args, "vllm_end_silence_ms", None) is not None:
        cfg.VLLM_END_SILENCE_MS = args.vllm_end_silence_ms
    if getattr(args, "vllm_enable_align", None) is not None:
        cfg.VLLM_ENABLE_ALIGN = args.vllm_enable_align
    if getattr(args, "vllm_align_device", None) is not None:
        cfg.VLLM_ALIGN_DEVICE = args.vllm_align_device
    if getattr(args, "vllm_infer_batch_size", None) is not None:
        cfg.VLLM_INFER_BATCH_SIZE = args.vllm_infer_batch_size
    if getattr(args, "vllm_segment_gap_ms", None) is not None:
        cfg.VLLM_SEGMENT_GAP_MS = args.vllm_segment_gap_ms
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
    cfg.ENABLE_OPENAI_API = getattr(args, "enable_openai_api", False)
    if getattr(args, "openai_sync_timeout", None) is not None:
        cfg.OPENAI_SYNC_TIMEOUT = args.openai_sync_timeout
    cfg.ENABLE_DASHSCOPE_API = getattr(args, "enable_dashscope_api", False)
    if getattr(args, "compat_fetch_max_mb", None) is not None:
        cfg.COMPAT_FETCH_MAX_MB = args.compat_fetch_max_mb
    if getattr(args, "compat_fetch_timeout", None) is not None:
        cfg.COMPAT_FETCH_TIMEOUT = args.compat_fetch_timeout
    cfg.COMPAT_FETCH_ALLOW_PRIVATE = getattr(args, "compat_fetch_allow_private", False)
    if getattr(args, "compat_external_base_url", None) is not None:
        cfg.COMPAT_EXTERNAL_BASE_URL = args.compat_external_base_url
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
    app = FastAPI(title="Qwen3-ASR Service", version=os.environ.get("APP_VERSION", "2.2.0"))
    # 响应压缩（vendored 前端库 1.7MB → ~426KB；仅作用于 HTTP，WS 不受影响）
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    if serve_mode == "vllm":
        _assemble_vllm(app, args)
    else:
        _assemble_standard(app, args)

    _mount_root(app)
    logger.info(f"Qwen3-ASR Service 就绪（serve-mode={serve_mode}），监听 {cfg.HOST}:{cfg.PORT}")
    return app


def _mount_root(app: FastAPI) -> None:
    """根路径：已启用 Web UI 则跳转，否则回服务索引（避免空白/404）。"""

    @app.get("/", include_in_schema=False)
    async def root():
        if cfg.ENABLE_WEB:
            return RedirectResponse(url="/web-ui")
        return {
            "service": "Qwen3-ASR Service",
            "version": app.version,
            "mode": cfg.SERVE_MODE,
            "health": "/v2/health",
            "capabilities": "/v2/capabilities",
            "web_ui": "未启用，启动加 --web 开启 / disabled, start with --web",
        }


def _assemble_standard(app: FastAPI, args) -> None:
    """standard 模式：transformers/OpenVINO 离线引擎 + 离线接口(v1/v2) + 共性接口。
    实时 Route B 的挂载在 T09 接通（见下方 TODO）。"""
    if not _STANDARD_DEPS_OK:
        logger.critical(
            f"standard 模式依赖缺失（{_STANDARD_DEPS_ERR}）。当前疑为 vLLM 专用环境，"
            "请用 --serve-mode vllm，或在完整环境（含 funasr 等）运行 standard 模式。")
        sys.exit(1)

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
        # 可覆盖参数的当前生效默认值（反映实际配置，供 Web UI 占位提示）
        "defaults": {
            "max_segment": cfg.MAX_SEGMENT_DURATION,
            "max_end_silence_ms": cfg.VAD_MAX_SILENCE,
            "max_segment_sec": cfg.STREAM_MAX_SEGMENT_SEC,
            "speaker_threshold": cfg.SPEAKER_THRESHOLD,
            "speaker_min_seg_ms": cfg.SPEAKER_MIN_SEG_MS,
            "speaker_max": cfg.SPEAKER_MAX,
            "speaker_id_threshold": cfg.SPEAKER_ID_THRESHOLD,
            "speaker_id_margin": cfg.SPEAKER_ID_MARGIN,
            "energy_floor_dbfs": cfg.STREAM_ENERGY_FLOOR_DBFS,
            "snr_min_db": cfg.STREAM_SNR_MIN_DB,
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

    # 兼容接口（/compat/*）：可选挂载，与 v1/v2 完全隔离（错误信封按异常类型分派）
    enable_openai = getattr(args, "enable_openai_api", False)
    enable_dashscope = getattr(args, "enable_dashscope_api", False)
    if enable_openai or enable_dashscope:
        from app.api.compat import init_compat
        from app.api.compat.errors import register_compat_exception_handlers
        init_compat(task_manager=task_manager, task_store=task_store,
                    backend=stream_backend, service_info=service_info)
        register_compat_exception_handlers(app)
    if enable_openai:
        from app.api.compat.openai_routes import build_openai_router
        app.include_router(build_openai_router())
        logger.info("OpenAI 兼容接口已启用：/compat/openai/v1/*")
        if stream_backend is not None:
            from app.api.compat.openai_ws_routes import build_openai_ws_router
            app.include_router(build_openai_ws_router())
            logger.info("OpenAI 实时兼容已启用：WS /compat/openai/v1/realtime（整句，无逐字增量）")
        elif stream_enabled is False:
            logger.info("OpenAI 实时兼容未挂载：需 --enable-stream")
    if enable_dashscope:
        from app.api.compat.dashscope_routes import build_dashscope_router
        app.include_router(build_dashscope_router())
        logger.info("DashScope 兼容接口已启用：/compat/dashscope/api/v1/*")
        if stream_backend is not None:
            from app.api.compat.dashscope_ws_routes import build_dashscope_ws_router
            app.include_router(build_dashscope_ws_router())
            logger.info("DashScope 实时兼容已启用：WS /compat/dashscope/api-ws/v1/inference（整句，无中间结果）")
        elif stream_enabled is False:
            logger.info("DashScope 实时兼容未挂载：需 --enable-stream")

    # 条件挂载 Web UI
    if getattr(args, "web", False):
        from app.web.views import web_router, ASSETS_DIR, DOCS_MEDIA_DIR
        app.include_router(web_router)
        app.mount("/web-ui/assets", StaticFiles(directory=ASSETS_DIR), name="web-assets")
        if os.path.isdir(DOCS_MEDIA_DIR):
            app.mount("/web-ui/docs-media", StaticFiles(directory=DOCS_MEDIA_DIR), name="docs-media")
        cfg.ENABLE_WEB = True       # 根路径据此跳转 /web-ui（仅实际挂载时置位）
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
    """vllm 模式（路线 A）：vLLM 原生流式引擎 + 统一实时端点 WS /v2/asr/stream + 共性接口。

    仅提供流式转写；不挂离线/transformers/OpenVINO/TaskManager/兼容桥（D5）。
    断句用能量端点器（无 funasr）。要求 CUDA 设备；进程内同步 vLLM，须 uvicorn workers=1。
    """
    device_info = detect_device()
    device = resolve_device(args.device, device_info=device_info)
    if device != "cuda":
        logger.critical(
            f"serve-mode=vllm 需要 CUDA 设备，当前解析为 {device}。"
            "vLLM 原生流式仅支持 GPU；如需 CPU/离线请用 --serve-mode standard。")
        sys.exit(1)
    vram_gb = device_info.get("vram_gb")
    model_size = args.model_size or auto_select_model_size(vram_gb)

    # 惰性导入：vLLM / qwen-asr[vllm] 仅 vllm 模式加载；standard / CPU 不依赖
    from app.engines.vllm_asr_engine import VLLMASREngine
    from app.runtime.vllm_stream_session import VllmStreamBackend
    from app.api.ws_routes import ws_router_stream, init_ws_stream

    engine = VLLMASREngine(
        model_size=model_size,
        gpu_memory_utilization=cfg.VLLM_GPU_MEMORY_UTILIZATION,
        max_model_len=cfg.VLLM_MAX_MODEL_LEN,
        chunk_size_sec=cfg.VLLM_CHUNK_SIZE_SEC,
        unfixed_chunk_num=cfg.VLLM_UNFIXED_CHUNK_NUM,
        unfixed_token_num=cfg.VLLM_UNFIXED_TOKEN_NUM,
        enable_align=cfg.VLLM_ENABLE_ALIGN,
        align_device=cfg.VLLM_ALIGN_DEVICE,
        infer_batch_size=cfg.VLLM_INFER_BATCH_SIZE,
    )
    try:
        engine.load()
    except Exception as e:
        logger.critical(f"vLLM 引擎加载失败: {e}", exc_info=True)
        sys.exit(1)

    # 说话人分离引擎（Phase 2，可选，非 funasr：CAM++ + scipy/sklearn 聚类）：加载失败
    # 降级关闭，不影响转写/实时主链路（容错对齐 standard）。
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

    # 离线能量 VAD（无 funasr）：说话人滑窗来源 + 声纹库登记/识别样本切分的 vad_engine 替身
    energy_vad = None
    if speaker_enabled:
        from app.runtime.energy_vad import EnergyVAD
        energy_vad = EnergyVAD(energy_floor_dbfs=cfg.VLLM_ENERGY_FLOOR_DBFS)

    # 声纹库（Phase 2，可选）：降级矩阵按序检查，任一失败 = ERROR 日志 + 模块关闭、服务继续
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
                speaker_service = SpeakerService(speaker_store, speaker_engine, energy_vad)
            except Exception as e:                       # ④ 建库失败
                logger.error(f"声纹库初始化失败，已降级关闭: {e}")
                speaker_service = None
                speaker_store = None
    speaker_db_enabled = speaker_service is not None and not speaker_tag_mismatch
    # 转写联动仅在识别可用时注入（失配 = 库内模板与当前引擎不可比，联动同样禁用）
    linked_speaker_service = speaker_service if speaker_db_enabled else None

    stream_backend = VllmStreamBackend(
        engine,
        max_sessions=cfg.MAX_STREAM_SESSIONS,
        concurrency=cfg.VLLM_CONCURRENCY,
        max_utterance_sec=cfg.VLLM_MAX_UTTERANCE_SEC,
        energy_floor_dbfs=cfg.VLLM_ENERGY_FLOOR_DBFS,
        end_silence_ms=cfg.VLLM_END_SILENCE_MS,
    )
    init_ws_stream(stream_backend)
    app.include_router(ws_router_stream)

    # 离线任务层（Phase 1）：复用 standard 的 TaskManager/TaskStore/离线路由（依赖中性，
    # 惰性导入——module 顶层这些名在 venv-vllm 因 funasr 缺失为 None，故此处重新导入）；
    # 处理函数换成 vLLM 批量 transcribe（runtime/vllm_offline）。
    from app.runtime.task_manager import TaskManager
    from app.runtime.vllm_offline import run_vllm_offline

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

    task_manager = TaskManager(max_queue_size=cfg.MAX_QUEUE_SIZE, store=task_store)

    def process_task(task: dict):
        def on_progress(p):
            task_manager.update_progress(task["task_id"], p)
        return run_vllm_offline(
            engine, task,
            progress_callback=on_progress,
            cancelled=lambda: task_manager.is_stopping or task_manager.is_cancelled(task["task_id"]),
            speaker_engine=speaker_engine,
            speaker_service=linked_speaker_service,
            energy_vad=energy_vad,
        )

    task_manager.set_processor(process_task)
    task_manager.start()

    capabilities = {
        "mode": "vllm",
        "offline_api": True,
        "speaker_labels": speaker_enabled,             # Phase 2：离线说话人分离
        "speaker_identification": speaker_db_enabled,  # Phase 2：声纹识别（需声纹库）
        "stream": {
            "enabled": True,
            "backend": "vllm-native",
            "path": "/v2/asr/stream",
            "partial_results": True,
            "word_timestamps": False,
            "speaker_labels": False,                   # 流式说话人仍无（仅离线，见能力对照）
        },
        # 离线可覆盖默认（Web UI 占位/对照用）；vLLM 无 FSMN，分段用标点优先
        "defaults": {
            "max_segment": cfg.MAX_SEGMENT_DURATION,
            "segment_gap_ms": cfg.VLLM_SEGMENT_GAP_MS,
            "speaker_max": cfg.SPEAKER_MAX,
            "speaker_id_threshold": cfg.SPEAKER_ID_THRESHOLD,
            "speaker_id_margin": cfg.SPEAKER_ID_MARGIN,
        },
        # 兼容接口（Phase 3）：据 --enable-openai-api/--enable-dashscope-api 实挂；vLLM 流式恒开，
        # 实时兼容随离线开关一并挂（无需 --enable-stream）。realtime_partial=R2 已下发增量
        # （DashScope 中间 result-generated 干净 / OpenAI delta best-effort，见兼容文档）
        "compat": {
            "openai": getattr(args, "enable_openai_api", False),
            "dashscope": getattr(args, "enable_dashscope_api", False),
            "realtime": getattr(args, "enable_openai_api", False)
            or getattr(args, "enable_dashscope_api", False),
            "realtime_partial": getattr(args, "enable_openai_api", False)
            or getattr(args, "enable_dashscope_api", False),
        },
    }
    service_info = {
        "status": "ready",
        "mode": "vllm",
        "device": device,
        "model_size": model_size,
        "align_enabled": engine.align_enabled,
        "punc_enabled": True,        # 模型原生标点（恒有，不可单独关；详见能力对照文档）
        "speaker_enabled": speaker_enabled,
        "speaker_db_enabled": speaker_db_enabled,
        "asr_backend": "vllm",
        "config_file": cfg.CONFIG_FILE,
        "capabilities": capabilities,
    }

    init_common(service_info)
    app.include_router(build_common_router("/v1"))
    app.include_router(build_common_router("/v2"))

    # 离线路由：v1（含 deprecated 别名）+ v2（同名复用）——与 standard 同一套契约
    from app.api.routes import init_routes, build_offline_router
    init_routes(task_manager, task_store)
    app.include_router(build_offline_router("/v1", include_deprecated=True))
    app.include_router(build_offline_router("/v2"))

    # 声纹库路由（仅 /v2）：无条件挂载——未启用/降级时端点统一 503（与 standard 同）
    from app.api.speaker_routes import init_speaker_routes, build_speakers_router
    init_speaker_routes(speaker_service, tag_mismatch=speaker_tag_mismatch)
    app.include_router(build_speakers_router())
    if speaker_db_enabled:
        logger.info(f"声纹库已启用：/v2/speakers*（{speaker_store.speaker_count} 人，"
                    f"自动登记={'开' if cfg.SPEAKER_AUTO_ENROLL else '关'}）")

    # 兼容接口（/compat/*，Phase 3）：离线复用 vLLM 任务层，实时复用 vLLM 流式后端。
    # 与 standard 唯一差异：vLLM 流式恒开，故实时兼容随离线开关挂载（无需 --enable-stream）；
    # 实时为 R1 finals-only（ws_bridge 跳过 partial）。compat 代码依赖中性，惰性导入。
    enable_openai = getattr(args, "enable_openai_api", False)
    enable_dashscope = getattr(args, "enable_dashscope_api", False)
    if enable_openai or enable_dashscope:
        from app.api.compat import init_compat
        from app.api.compat.errors import register_compat_exception_handlers
        init_compat(task_manager=task_manager, task_store=task_store,
                    backend=stream_backend, service_info=service_info)
        register_compat_exception_handlers(app)
    if enable_openai:
        from app.api.compat.openai_routes import build_openai_router
        from app.api.compat.openai_ws_routes import build_openai_ws_router
        app.include_router(build_openai_router())
        app.include_router(build_openai_ws_router())
        logger.info("OpenAI 兼容接口已启用：/compat/openai/v1/*（含实时 WS /realtime，整句下发）")
    if enable_dashscope:
        from app.api.compat.dashscope_routes import build_dashscope_router
        from app.api.compat.dashscope_ws_routes import build_dashscope_ws_router
        app.include_router(build_dashscope_router())
        app.include_router(build_dashscope_ws_router())
        logger.info("DashScope 兼容接口已启用：/compat/dashscope/api/v1/*"
                    "（含实时 WS /api-ws/v1/inference，整句下发）")

    # 条件挂载 Web UI（演示页已内置 partial→final 实时渲染）
    if getattr(args, "web", False):
        from app.web.views import web_router, ASSETS_DIR, DOCS_MEDIA_DIR
        app.include_router(web_router)
        app.mount("/web-ui/assets", StaticFiles(directory=ASSETS_DIR), name="web-assets")
        if os.path.isdir(DOCS_MEDIA_DIR):
            app.mount("/web-ui/docs-media", StaticFiles(directory=DOCS_MEDIA_DIR), name="docs-media")
        cfg.ENABLE_WEB = True
        logger.info(f"Web UI 已启用，访问 http://{cfg.HOST}:{cfg.PORT}/web-ui")

    @app.on_event("shutdown")
    def _on_vllm_shutdown():
        logger.info("收到终止信号，正在关闭 vLLM 实时后端与离线任务...")
        task_manager.shutdown()
        stream_backend.shutdown()
        if speaker_store is not None:
            speaker_store.close()
        logger.info("Qwen3-ASR Service (vllm) 已安全退出")

    logger.info(f"运行模式: {service_info}")
    logger.info("实时转写已启用：WS /v2/asr/stream（路线A / vllm-native，含 partial）")


app = None


def get_app():
    global app
    if app is None:
        app = create_app()
    return app


if __name__ == "__main__":
    cli_ns = _parse_cli()
    # --update-config：仅同步本地 config.yaml 后退出，不启动服务
    if getattr(cli_ns, "update_config", False):
        setup_logger()
        run_config_update(cli_ns.config, cli_ns.no_config,
                          getattr(cli_ns, "sync_all", False))
        sys.exit(0)
    args = merge_runtime_config(cli_ns)
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
