"""启动参数单一 schema：同一份定义驱动 argparse、配置文件校验与 config.example.yaml。

设计要点（docs/plan/features/20260604_config_file/config-file-design.md §3.2）：
- argparse 的 default 一律 SUPPRESS——否则无法区分"显式传了默认值"与"没传"，
  配置文件的覆盖语义（默认值 < 环境变量 < 配置文件 < CLI 显式参数）会错；
- 实义默认值收敛到本表（schema_defaults），消除 CLI 定义 / 文件校验 / 示例文件三处漂移；
- key = 配置文件键名（CLI 长参数横线转下划线）；dest = argparse Namespace 属性名，
  仅 --use-punc 二者不同（历史 dest=enable_punc，保持兼容）。

国际化（--help 文案）：每条 help 就近带一份英文 help_en（缺省回退中文），由
build_parser(lang) 按语言取用；语言经 resolve_help_lang() 跟随 shell 的 $LANG
自动判定，可由 --lang 显式覆盖。译文与定义同处，沿用"单一 schema 不漂移"的原则。
"""
import argparse
import locale
import os
import sys
from dataclasses import dataclass

import app.config as cfg


@dataclass(frozen=True)
class ArgSpec:
    key: str                      # 配置文件键名（= CLI 长参数横线转下划线）
    flags: tuple                  # CLI flag（bool 型为"开启" flag）
    default: object = None        # 实义默认值（argparse 一律 SUPPRESS）
    type: type = str              # str / int / float / bool（bool 走 store_true/store_false）
    choices: tuple = None
    help: str = ""                # 中文 help
    help_en: str = ""             # 英文 help（lang=en 时启用，空则回退 help）
    dest: str = None              # argparse dest，缺省 = key
    negative_flags: tuple = ()    # bool 开关对的"关闭" flag（如 --no-align）
    negative_help: str = ""       # 中文否定 help
    negative_help_en: str = ""    # 英文否定 help（空则回退 negative_help）
    group: str = "其他"           # 生效配置打印的分组，随定义声明（勿留在"其他"）

    @property
    def attr(self) -> str:
        return self.dest or self.key


ARG_SPECS = (
    ArgSpec(
        key="serve_mode", flags=("--serve-mode",), default="standard",
        choices=("standard", "vllm"), group="服务",
        help="服务运行模式：standard=transformers/OpenVINO 离线+实时(路线B)；"
             "vllm=vLLM 原生流式(Phase 3) (default: standard)",
        help_en="Service run mode: standard=transformers/OpenVINO offline+realtime "
                "(route B); vllm=vLLM native streaming (Phase 3) (default: standard)",
    ),
    ArgSpec(
        key="device", flags=("--device",), default="auto",
        choices=("auto", "cuda", "cpu"), group="模型",
        help="运行设备 (default: auto)",
        help_en="Compute device (default: auto)",
    ),
    ArgSpec(
        key="model_size", flags=("--model-size",), default=None,
        choices=("0.6b", "1.7b"), group="模型",
        help="ASR 模型大小 (default: 根据显存自动选择)",
        help_en="ASR model size (default: auto-selected by VRAM)",
    ),
    ArgSpec(
        key="enable_align", flags=("--enable-align",), default=True, type=bool,
        group="模型",
        help="加载对齐模型 (default)",
        help_en="Load the alignment model (default)",
        negative_flags=("--no-align",), negative_help="不加载对齐模型",
        negative_help_en="Do not load the alignment model",
    ),
    ArgSpec(
        key="use_punc", flags=("--use-punc",), default=False, type=bool,
        dest="enable_punc", group="模型",
        help="启用标点恢复",
        help_en="Enable punctuation restoration",
        negative_flags=("--no-punc",), negative_help="禁用标点恢复（覆盖配置文件）",
        negative_help_en="Disable punctuation restoration (overrides config file)",
    ),
    ArgSpec(
        key="model_source", flags=("--model-source",), default="modelscope",
        choices=("modelscope", "huggingface"), group="模型",
        help="模型下载源 (default: modelscope)",
        help_en="Model download source (default: modelscope)",
    ),
    ArgSpec(
        key="host", flags=("--host",), default=None, group="服务",
        help="监听地址 (default: 127.0.0.1)",
        help_en="Listen address (default: 127.0.0.1)",
    ),
    ArgSpec(
        key="port", flags=("--port",), default=None, type=int, group="服务",
        help="监听端口 (default: 8765)",
        help_en="Listen port (default: 8765)",
    ),
    ArgSpec(
        key="web", flags=("--web",), default=False, type=bool, group="服务",
        help="启用 Web UI (访问 /web-ui)",
        help_en="Enable Web UI (served at /web-ui)",
        negative_flags=("--no-web",), negative_help="禁用 Web UI（覆盖配置文件）",
        negative_help_en="Disable Web UI (overrides config file)",
    ),
    ArgSpec(
        key="max_segment", flags=("--max-segment",), default=5, type=int,
        group="离线任务",
        help="VAD 切片合并最大时长，单位秒 (default: 5)",
        help_en="Max merged VAD segment length in seconds (default: 5)",
    ),
    ArgSpec(
        key="api_key", flags=("--api-key",), default=None, group="服务",
        help="API 密钥，设置后启用 Bearer token 认证（覆盖 ASR_API_KEY 环境变量）",
        help_en="API key; enables Bearer token auth when set "
                "(overrides ASR_API_KEY env var)",
    ),
    ArgSpec(
        key="max_queue_size", flags=("--max-queue-size",), default=None, type=int,
        group="离线任务",
        help=f"任务队列最大长度 (default: {cfg.MAX_QUEUE_SIZE})",
        help_en=f"Max task queue length (default: {cfg.MAX_QUEUE_SIZE})",
    ),
    ArgSpec(
        key="enable_stream", flags=("--enable-stream",), default=False, type=bool,
        group="实时转写",
        help="挂载实时转写端点 WS /v2/asr/stream（路线B，standard 模式）",
        help_en="Mount realtime transcription endpoint WS /v2/asr/stream "
                "(route B, standard mode)",
        negative_flags=("--no-stream",), negative_help="不挂载实时转写端点（覆盖配置文件）",
        negative_help_en="Do not mount the realtime endpoint (overrides config file)",
    ),
    ArgSpec(
        key="max_stream_sessions", flags=("--max-stream-sessions",), default=None, type=int,
        group="实时转写",
        help=f"实时最大并发会话数 (default: {cfg.MAX_STREAM_SESSIONS})",
        help_en=f"Max concurrent realtime sessions (default: {cfg.MAX_STREAM_SESSIONS})",
    ),
    ArgSpec(
        key="stream_asr_concurrency", flags=("--stream-asr-concurrency",), default=None, type=int,
        group="实时转写",
        help=f"实时 ASR 解码并发上限 (default: {cfg.STREAM_ASR_CONCURRENCY})",
        help_en=f"Max concurrent realtime ASR decodes (default: {cfg.STREAM_ASR_CONCURRENCY})",
    ),
    ArgSpec(
        key="stream_save_audio", flags=("--stream-save-audio",), default=False, type=bool,
        group="实时转写",
        help="保存实时录音原件为 WAV（默认关，录音下载/删除接口要求 api_key）",
        help_en="Save realtime input audio as WAV (off by default; download/delete "
                "requires api_key)",
        negative_flags=("--no-stream-save-audio",),
        negative_help="不保存实时录音原件（覆盖配置文件）",
        negative_help_en="Do not save realtime input audio (overrides config file)",
    ),
    ArgSpec(
        key="stream_recording_retention_hours",
        flags=("--stream-recording-retention-hours",),
        default=72,
        type=int,
        group="实时转写",
        help="实时录音保留时长（小时），启动时清理过期文件；0=永不清理 (default: 72)",
        help_en="Realtime recording retention in hours, cleaned at startup; "
                "0=never (default: 72)",
    ),
    ArgSpec(
        key="vad_speech_noise_thres", flags=("--vad-speech-noise-thres",),
        default=0.6, type=float, group="远场过滤",
        help="FSMN-VAD 语音/噪声判决阈值（离线+实时统一）：调高更激进过滤远场/弱帧 (default: 0.6)",
        help_en="FSMN-VAD speech/noise decision threshold (offline+realtime); "
                "higher filters far-field/weak frames more aggressively (default: 0.6)",
    ),
    ArgSpec(
        key="stream_noise_filter", flags=("--stream-noise-filter",),
        default=False, type=bool, group="远场过滤",
        help="实时段级能量/SNR 门控：减少远场/环境音误触发（默认关）",
        help_en="Realtime segment-level energy/SNR gating: reduces far-field/ambient "
                "false triggers (off by default)",
        negative_flags=("--no-stream-noise-filter",),
        negative_help="关闭实时段级远场过滤（覆盖配置文件）",
        negative_help_en="Disable realtime segment-level far-field filtering "
                         "(overrides config file)",
    ),
    ArgSpec(
        key="stream_energy_floor_dbfs", flags=("--stream-energy-floor-dbfs",),
        default=-50.0, type=float, group="远场过滤",
        help="绝对能量门（dBFS，满量程参考）：段响度低于此丢弃 (default: -50.0)",
        help_en="Absolute energy gate (dBFS, full-scale ref): drop segments quieter "
                "than this (default: -50.0)",
    ),
    ArgSpec(
        key="stream_snr_min_db", flags=("--stream-snr-min-db",),
        default=6.0, type=float, group="远场过滤",
        help="自适应信噪比门（dB）：段相对会话噪声底不足此值丢弃；<=0 关闭该门 (default: 6.0)",
        help_en="Adaptive SNR gate (dB): drop segments below this over the session "
                "noise floor; <=0 disables (default: 6.0)",
    ),
    ArgSpec(
        key="gpu_memory_utilization", flags=("--gpu-memory-utilization",),
        default=None, type=float, group="vLLM",
        help=f"vLLM 显存占用率 (default: {cfg.VLLM_GPU_MEMORY_UTILIZATION})",
        help_en=f"vLLM GPU memory utilization (default: {cfg.VLLM_GPU_MEMORY_UTILIZATION})",
    ),
    ArgSpec(
        key="vllm_max_model_len", flags=("--vllm-max-model-len",),
        default=None, type=int, group="vLLM",
        help=f"vLLM 最大上下文长度，下调省 KV cache 显存 (default: {cfg.VLLM_MAX_MODEL_LEN})",
        help_en=f"vLLM max context length; lower to save KV cache memory (default: {cfg.VLLM_MAX_MODEL_LEN})",
    ),
    ArgSpec(
        key="vllm_chunk_size_sec", flags=("--vllm-chunk-size-sec",),
        default=None, type=float, group="vLLM",
        help=f"流式解码块大小（秒），越小 partial 越细腻 (default: {cfg.VLLM_CHUNK_SIZE_SEC})",
        help_en=f"Streaming decode chunk size (sec); smaller = finer partials "
                f"(default: {cfg.VLLM_CHUNK_SIZE_SEC})",
    ),
    ArgSpec(
        key="vllm_max_utterance_sec", flags=("--vllm-max-utterance-sec",),
        default=None, type=int, group="vLLM",
        help=f"单句兜底切分（秒），约束上下文/显存增长 (default: {cfg.VLLM_MAX_UTTERANCE_SEC})",
        help_en=f"Per-utterance hard cut (sec); bounds context/memory growth "
                f"(default: {cfg.VLLM_MAX_UTTERANCE_SEC})",
    ),
    ArgSpec(
        key="vllm_concurrency", flags=("--vllm-concurrency",),
        default=None, type=int, group="vLLM",
        help=f"同时解码会话数（generate 串行，>1 无吞吐收益）(default: {cfg.VLLM_CONCURRENCY})",
        help_en=f"Concurrent decoding sessions (generate is serial; >1 yields no throughput) "
                f"(default: {cfg.VLLM_CONCURRENCY})",
    ),
    ArgSpec(
        key="vllm_end_silence_ms", flags=("--vllm-end-silence-ms",),
        default=None, type=int, group="vLLM",
        help=f"能量端点尾静音判停（ms）(default: {cfg.VLLM_END_SILENCE_MS})",
        help_en=f"Energy endpointer end-silence threshold (ms) (default: {cfg.VLLM_END_SILENCE_MS})",
    ),
    ArgSpec(
        key="vllm_enable_align", flags=("--vllm-enable-align",),
        default=None, type=bool, group="vLLM",
        help=f"vLLM 离线加载对齐模型出词级时间戳 (default: {cfg.VLLM_ENABLE_ALIGN})",
        help_en=f"vLLM offline: load aligner for word-level timestamps (default: {cfg.VLLM_ENABLE_ALIGN})",
        negative_flags=("--no-vllm-align",),
        negative_help="vLLM 离线不加载对齐模型（省显存，无词级时间戳）",
        negative_help_en="vLLM offline: do not load the aligner (save VRAM, no word timestamps)",
    ),
    ArgSpec(
        key="vllm_align_device", flags=("--vllm-align-device",),
        default=None, choices=("cuda", "cpu"), group="vLLM",
        help=f"对齐器加载设备；cuda 快但显存在 gpu_memory_utilization 预算外，OOM 时改 cpu "
             f"(default: {cfg.VLLM_ALIGN_DEVICE})",
        help_en=f"Aligner device; cuda is fast but its VRAM is outside gpu_memory_utilization, "
                f"switch to cpu on OOM (default: {cfg.VLLM_ALIGN_DEVICE})",
    ),
    ArgSpec(
        key="vllm_infer_batch_size", flags=("--vllm-infer-batch-size",),
        default=None, type=int, group="vLLM",
        help=f"一次对齐/ASR 的音频块数（块≤180s）；-1=全部一次（长音频对齐易 OOM），"
             f"小值省显存 (default: {cfg.VLLM_INFER_BATCH_SIZE})",
        help_en=f"Audio chunks per alignment/ASR batch (chunks ≤180s); -1=all at once "
                f"(long audio aligner OOM), smaller saves VRAM (default: {cfg.VLLM_INFER_BATCH_SIZE})",
    ),
    ArgSpec(
        key="vllm_segment_gap_ms", flags=("--vllm-segment-gap-ms",),
        default=None, type=int, group="vLLM",
        help=f"vLLM 离线分段词间隙阈值（ms），相邻词间隙超此断句 (default: {cfg.VLLM_SEGMENT_GAP_MS})",
        help_en=f"vLLM offline segmentation word-gap threshold (ms) (default: {cfg.VLLM_SEGMENT_GAP_MS})",
    ),
    ArgSpec(
        key="enable_task_store", flags=("--enable-task-store",), default=False, type=bool,
        group="离线任务",
        help="离线任务持久化（data/tasks.db）：结果跨重启可查",
        help_en="Offline task persistence (data/tasks.db): results queryable across restarts",
        negative_flags=("--no-task-store",), negative_help="关闭任务持久化（覆盖配置文件）",
        negative_help_en="Disable task persistence (overrides config file)",
    ),
    ArgSpec(
        key="task_db_path", flags=("--task-db-path",), default="data/tasks.db",
        group="离线任务",
        help="任务库路径，相对服务根目录 (default: data/tasks.db)",
        help_en="Task DB path, relative to the service root (default: data/tasks.db)",
    ),
    ArgSpec(
        key="task_retention_days", flags=("--task-retention-days",), default=7, type=int,
        group="离线任务",
        help="过期任务清理窗口（天），启动时执行；0=永不清理 (default: 7)",
        help_en="Expired-task cleanup window in days, run at startup; 0=never (default: 7)",
    ),
    ArgSpec(
        key="enable_speaker", flags=("--enable-speaker",), default=False, type=bool,
        group="说话人分离",
        help="说话人分离：离线 segment.speaker / 实时 final.speaker（匿名 A/B/C…）",
        help_en="Speaker diarization: offline segment.speaker / realtime final.speaker "
                "(anonymous A/B/C…)",
        negative_flags=("--no-speaker",), negative_help="关闭说话人分离（覆盖配置文件）",
        negative_help_en="Disable speaker diarization (overrides config file)",
    ),
    ArgSpec(
        key="speaker_threshold", flags=("--speaker-threshold",), default=0.5, type=float,
        group="说话人分离",
        help="实时在线归簇余弦阈值，实测可用区间 [0.35, 0.65] (default: 0.5)",
        help_en="Realtime online clustering cosine threshold; usable range "
                "[0.35, 0.65] (default: 0.5)",
    ),
    ArgSpec(
        key="speaker_max", flags=("--speaker-max",), default=8, type=int,
        group="说话人分离",
        help="说话人数上限：实时硬上限，离线谱聚类簇数搜索上界 (default: 8)",
        help_en="Max speakers: hard cap for realtime, upper search bound for offline "
                "spectral clustering (default: 8)",
    ),
    ArgSpec(
        key="speaker_min_seg_ms", flags=("--speaker-min-seg-ms",), default=1500, type=int,
        group="说话人分离",
        help="实时短段门槛（毫秒）：短于此不建新簇/不更新质心 (default: 1500)",
        help_en="Realtime short-segment threshold (ms): below this, no new cluster / "
                "no centroid update (default: 1500)",
    ),
    ArgSpec(
        key="speaker_max_windows", flags=("--speaker-max-windows",), default=4000, type=int,
        group="说话人分离",
        help="离线滑窗数上限，超出均匀抽稀（超长音频内存防护） (default: 4000)",
        help_en="Max offline sliding windows; uniformly downsampled beyond this "
                "(long-audio memory guard) (default: 4000)",
    ),
    ArgSpec(
        key="enable_speaker_db", flags=("--enable-speaker-db",), default=False, type=bool,
        group="声纹库",
        help="声纹库（登记+真名识别）：依赖 --enable-speaker 且必须配置 api_key",
        help_en="Voiceprint DB (enrollment + real-name identification): requires "
                "--enable-speaker and a configured api_key",
        negative_flags=("--no-speaker-db",), negative_help="关闭声纹库（覆盖配置文件）",
        negative_help_en="Disable voiceprint DB (overrides config file)",
    ),
    ArgSpec(
        key="speaker_db_path", flags=("--speaker-db-path",), default="data/speakers.db",
        group="声纹库",
        help="声纹库路径，相对服务根目录；数据永不自动清理 (default: data/speakers.db)",
        help_en="Voiceprint DB path, relative to the service root; data is never "
                "auto-cleaned (default: data/speakers.db)",
    ),
    ArgSpec(
        key="speaker_id_threshold", flags=("--speaker-id-threshold",), default=0.45, type=float,
        group="声纹库",
        help="1:N 开集识别阈 τ_id，低于此为 unknown (default: 0.45)",
        help_en="1:N open-set identification threshold τ_id; below this is unknown "
                "(default: 0.45)",
    ),
    ArgSpec(
        key="speaker_id_margin", flags=("--speaker-id-margin",), default=0.10, type=float,
        group="声纹库",
        help="top1-top2 margin，差距小于此判 unknown（宁缺勿错） (default: 0.10)",
        help_en="top1-top2 margin; smaller gap is judged unknown (prefer abstaining "
                "over error) (default: 0.10)",
    ),
    ArgSpec(
        key="speaker_enroll_min_sec", flags=("--speaker-enroll-min-sec",), default=3.0, type=float,
        group="声纹库",
        help="手动登记单样本最短有效语音秒数（VAD 后） (default: 3.0)",
        help_en="Min effective speech seconds per manual-enrollment sample "
                "(post-VAD) (default: 3.0)",
    ),
    ArgSpec(
        key="speaker_auto_enroll", flags=("--speaker-auto-enroll",), default=True, type=bool,
        group="声纹库",
        help="离线识别未命中的簇自动以「说话人_NN」登记（开启=部署方声明已获数据主体同意）",
        help_en="Auto-enroll unmatched offline clusters as 'Speaker_NN' "
                "(enabling = deployer asserts data-subject consent obtained)",
        negative_flags=("--no-speaker-auto-enroll",), negative_help="关闭自动登记（覆盖配置文件）",
        negative_help_en="Disable auto-enrollment (overrides config file)",
    ),
    ArgSpec(
        key="speaker_auto_enroll_min_sec", flags=("--speaker-auto-enroll-min-sec",),
        default=10.0, type=float, group="声纹库",
        help="自动登记的簇最短语音总时长秒数（严于手动登记） (default: 10.0)",
        help_en="Min total speech seconds for an auto-enrolled cluster "
                "(stricter than manual) (default: 10.0)",
    ),
    ArgSpec(
        key="speaker_store_audio", flags=("--speaker-store-audio",), default=False, type=bool,
        group="声纹库",
        help="留存登记样本音频到 data/speaker_audio/（扩大合规面，默认关）",
        help_en="Keep enrollment sample audio under data/speaker_audio/ "
                "(widens compliance scope; off by default)",
        negative_flags=("--no-speaker-store-audio",), negative_help="不留存登记样本音频",
        negative_help_en="Do not keep enrollment sample audio",
    ),
    ArgSpec(
        key="enable_openai_api", flags=("--enable-openai-api",), default=False, type=bool,
        group="兼容接口",
        help="启用 OpenAI 兼容接口 /compat/openai/v1/*（drop-in 对接 OpenAI SDK）",
        help_en="Enable OpenAI-compatible API /compat/openai/v1/* (drop-in for the OpenAI SDK)",
        negative_flags=("--no-openai-api",), negative_help="关闭 OpenAI 兼容接口（覆盖配置文件）",
        negative_help_en="Disable the OpenAI-compatible API (overrides config file)",
    ),
    ArgSpec(
        key="openai_sync_timeout", flags=("--openai-sync-timeout",), default=300, type=int,
        group="兼容接口",
        help="OpenAI 同步转写等待上限，单位秒；超时返回 504 (default: 300)",
        help_en="OpenAI sync transcription wait limit in seconds; returns 504 on "
                "timeout (default: 300)",
    ),
    ArgSpec(
        key="enable_dashscope_api", flags=("--enable-dashscope-api",), default=False, type=bool,
        group="兼容接口",
        help="启用 DashScope 兼容接口 /compat/dashscope/api/v1/*（drop-in 对接 DashScope SDK）",
        help_en="Enable DashScope-compatible API /compat/dashscope/api/v1/* "
                "(drop-in for the DashScope SDK)",
        negative_flags=("--no-dashscope-api",), negative_help="关闭 DashScope 兼容接口（覆盖配置文件）",
        negative_help_en="Disable the DashScope-compatible API (overrides config file)",
    ),
    ArgSpec(
        key="compat_fetch_max_mb", flags=("--compat-fetch-max-mb",), default=None, type=int,
        group="兼容接口",
        help="DashScope file_urls 下载大小上限 MB (default: 同 MAX_AUDIO_FILE_SIZE)",
        help_en="DashScope file_urls download size cap in MB "
                "(default: same as MAX_AUDIO_FILE_SIZE)",
    ),
    ArgSpec(
        key="compat_fetch_timeout", flags=("--compat-fetch-timeout",), default=120, type=int,
        group="兼容接口",
        help="DashScope file_urls 下载整体超时秒 (default: 120)",
        help_en="DashScope file_urls overall download timeout in seconds (default: 120)",
    ),
    ArgSpec(
        key="compat_fetch_allow_private", flags=("--compat-fetch-allow-private",),
        default=False, type=bool, group="兼容接口",
        help="允许 file_urls 下载私网/回环地址（SSRF 默认禁止）",
        help_en="Allow file_urls downloads to private/loopback addresses "
                "(SSRF blocked by default)",
        negative_flags=("--no-compat-fetch-allow-private",),
        negative_help="禁止下载私网地址（覆盖配置文件）",
        negative_help_en="Block downloads to private addresses (overrides config file)",
    ),
    ArgSpec(
        key="compat_external_base_url", flags=("--compat-external-base-url",),
        default=None, group="兼容接口",
        help="兼容层回链外部基址（如 https://asr.example.com，反代/容器场景；默认按请求推导）",
        help_en="External base URL for compat-layer callbacks "
                "(e.g. https://asr.example.com for reverse-proxy/container; "
                "default derived from request)",
    ),
)


# --help / 用法文案的中英文常量（非逐参数项）
_DESCRIPTION = {
    "zh": "Qwen3-ASR 语音识别服务",
    "en": "Qwen3-ASR speech recognition service",
}
_LANG_HELP = {
    "zh": "--help/用法文案语言；auto 或不设时跟随 shell 的 $LANG (default: auto)",
    "en": "--help/usage text language; auto or unset follows the shell $LANG (default: auto)",
}
_CONFIG_GROUP = {"zh": "配置文件", "en": "Config file"}
_CONFIG_HELP = {
    "zh": "YAML 配置文件路径（缺省时自动发现 config.yaml/config.yml，"
          "未发现则由 config.example.yaml 引导生成）",
    "en": "YAML config path (auto-discovers config.yaml/config.yml; "
          "bootstraps from config.example.yaml when none found)",
}
_NO_CONFIG_HELP = {
    "zh": "跳过配置文件加载与引导生成（纯默认值+环境变量+CLI 启动）",
    "en": "Skip config-file loading and bootstrap (defaults + env + CLI only)",
}
_UPDATE_CONFIG_HELP = {
    "zh": "仅更新本地 config.yaml：把 config.example.yaml 缺失的项追加进去"
          "（只补不覆盖、沿用 example 默认值；本地无配置则引导生成），"
          "完成后直接退出，不启动服务",
    "en": "Update local config.yaml only: append items missing from it (only add, never "
          "overwrite; example defaults; bootstraps when none exists), then exit without "
          "starting the service",
}
_SYNC_ALL_HELP = {
    "zh": "（配合 --update-config）连同高级/可选项一并同步（按注释态补入，禁用+默认值引用），"
          "默认仅同步推荐项",
    "en": "(with --update-config) also sync advanced/optional items (added commented out, "
          "as disabled default references); by default only recommended items are synced",
}


def _prescan_lang(argv) -> str | None:
    """从 argv 预取 --lang 值（支持 --lang zh / --lang=zh），未指定返回 None。

    argparse 遇到 --help 会当场打印并退出，故必须在建 parser 前定语言；此处只读取
    argv，不依赖 argparse 解析结果。
    """
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--lang="):
            return a.split("=", 1)[1]
    return None


def resolve_help_lang(argv=None) -> str:
    """决定 --help/用法文案语言：--lang 显式参数 > shell 区域($LC_ALL/$LC_MESSAGES/$LANG)。

    区域环境变量都未设时回退 locale.getlocale()。归一化后以 zh 开头判为中文，否则英文。
    """
    cand = _prescan_lang(sys.argv[1:] if argv is None else argv)
    if not cand or cand == "auto":
        cand = (os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES")
                or os.environ.get("LANG") or "")
        if not cand:
            try:
                cand = locale.getlocale()[0] or ""
            except Exception:
                cand = ""
    return "zh" if cand.lower().startswith("zh") else "en"


def build_parser(lang: str = "zh") -> argparse.ArgumentParser:
    """由 schema 生成 argparse：全参数 default=SUPPRESS（未传则不出现在 Namespace）。

    lang 决定 --help 文案语言（"zh"/"en"，缺省中文，向后兼容）；英文缺译时回退中文。
    --config / --no-config / --update-config 为配置加载的元参数，不属于 schema
    （不能写进配置文件），单独注册并保留普通 default。
    --lang 为文案语言元参数：default=SUPPRESS（不入 Namespace，避免污染配置合并；其值
    实际由 resolve_help_lang() 预扫描 argv 取得），注册仅为在 --help 露出且传入时不报错。
    """
    en = lang == "en"

    def pick(zh_text, en_text):
        return en_text if (en and en_text) else zh_text

    parser = argparse.ArgumentParser(description=_DESCRIPTION["en" if en else "zh"])
    for spec in ARG_SPECS:
        if spec.type is bool:
            parser.add_argument(
                *spec.flags, dest=spec.attr, action="store_true",
                default=argparse.SUPPRESS, help=pick(spec.help, spec.help_en),
            )
            if spec.negative_flags:
                parser.add_argument(
                    *spec.negative_flags, dest=spec.attr, action="store_false",
                    default=argparse.SUPPRESS,
                    help=pick(spec.negative_help, spec.negative_help_en),
                )
        else:
            kwargs = dict(dest=spec.attr, default=argparse.SUPPRESS,
                          help=pick(spec.help, spec.help_en))
            if spec.type is not str:
                kwargs["type"] = spec.type
            if spec.choices:
                kwargs["choices"] = spec.choices
            parser.add_argument(*spec.flags, **kwargs)

    parser.add_argument(
        "--lang", dest="help_lang", choices=("zh", "en", "auto"),
        default=argparse.SUPPRESS, help=_LANG_HELP["en" if en else "zh"],
    )

    group = parser.add_argument_group(_CONFIG_GROUP["en" if en else "zh"])
    group.add_argument(
        "--config", dest="config", default=None, metavar="PATH",
        help=_CONFIG_HELP["en" if en else "zh"],
    )
    group.add_argument(
        "--no-config", dest="no_config", action="store_true", default=False,
        help=_NO_CONFIG_HELP["en" if en else "zh"],
    )
    group.add_argument(
        "--update-config", dest="update_config", action="store_true", default=False,
        help=_UPDATE_CONFIG_HELP["en" if en else "zh"],
    )
    group.add_argument(
        "--all", dest="sync_all", action="store_true", default=False,
        help=_SYNC_ALL_HELP["en" if en else "zh"],
    )
    return parser


def schema_defaults() -> dict:
    """实义默认值表（dest 键），优先级链的第①层。"""
    return {spec.attr: spec.default for spec in ARG_SPECS}
