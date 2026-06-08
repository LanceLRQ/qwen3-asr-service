import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 服务配置
HOST = "127.0.0.1"
PORT = 8765

# ─── 启动参数默认值（由 main.py argparse 覆盖） ───

DEVICE = "auto"                 # "auto" | "cuda" | "cpu"
ASR_MODEL_SIZE = "0.6b"         # "0.6b" | "1.7b"
ENABLE_ALIGN = True             # 是否加载对齐模型
ENABLE_PUNC = True              # 是否启用标点恢复
MODEL_SOURCE = os.environ.get("MODEL_SOURCE", "modelscope")
API_KEY = os.environ.get("ASR_API_KEY", "")   # Bearer token，为空则不启用认证

# ─── 模型路径 ───

MODELS_DIR = os.path.join(BASE_DIR, "models")
ASR_MODEL_DIR = os.path.join(MODELS_DIR, "asr")
ALIGN_MODEL_DIR = os.path.join(MODELS_DIR, "align")
VAD_MODEL_DIR = os.path.join(MODELS_DIR, "vad")
PUNC_MODEL_DIR = os.path.join(MODELS_DIR, "punc")
SPEAKER_MODEL_DIR = os.path.join(MODELS_DIR, "speaker")

# OpenVINO 模型仓库（HuggingFace）
OV_MODEL_REPO_MAP = {
    "0.6b": "dseditor/Qwen3-ASR-0.6B-INT8_ASYM-OpenVINO",
    "1.7b": "dseditor/Qwen3-ASR-1.7B-INT8_OpenVINO",
}

# 模型仓库 ID
MODEL_REPO_MAP = {
    "huggingface": {
        "asr_0.6b": "Qwen/Qwen3-ASR-0.6B",
        "asr_1.7b": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
    },
    "modelscope": {
        "asr_0.6b": "Qwen/Qwen3-ASR-0.6B",
        "asr_1.7b": "Qwen/Qwen3-ASR-1.7B",
        "aligner": "Qwen/Qwen3-ForcedAligner-0.6B",
    },
}

# 仅 ModelScope 提供的模型（不受 MODEL_SOURCE 影响）
MODELSCOPE_ONLY_REPO_MAP = {
    "vad": "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
    "vad_onnx": "iic/speech_fsmn_vad_zh-cn-16k-common-onnx",
    "punc": "iic/punc_ct-transformer_zh-cn-common-vocab272727-pytorch",
    "punc_onnx": "iic/punc_ct-transformer_zh-cn-common-vocab272727-onnx",
    "campplus": "iic/speech_campplus_sv_zh-cn_16k-common",
}

# 模型本地子目录
MODEL_LOCAL_MAP = {
    "asr_0.6b": os.path.join(ASR_MODEL_DIR, "0.6b"),
    "asr_1.7b": os.path.join(ASR_MODEL_DIR, "1.7b"),
    "aligner": os.path.join(ALIGN_MODEL_DIR, "0.6b"),
    "vad": os.path.join(VAD_MODEL_DIR, "fsmn"),
    "vad_onnx": os.path.join(VAD_MODEL_DIR, "fsmn-onnx"),
    "punc": os.path.join(PUNC_MODEL_DIR, "ct-transformer"),
    "punc_onnx": os.path.join(PUNC_MODEL_DIR, "ct-transformer-onnx"),
    "asr_ov_0.6b": os.path.join(ASR_MODEL_DIR, "openvino", "0.6b"),
    "asr_ov_1.7b": os.path.join(ASR_MODEL_DIR, "openvino", "1.7b"),
    "campplus": os.path.join(SPEAKER_MODEL_DIR, "campplus"),
}

# ─── VAD 参数 ───

VAD_MAX_SILENCE = 800           # 尾部静音时长 ms
VAD_SPEECH_NOISE_THRES = 0.6    # FSMN-VAD 语音/噪声判决阈值（离线+实时统一）：
                                # 调高更激进过滤模糊/远场/弱帧，0.6=模型原生默认（不改即不变行为），建议 0.6–0.8

# ─── ASR 推理 ───

ASR_BATCH_SIZE = 32             # 批量推理每批 chunk 数（与 Qwen3 max_inference_batch_size 对齐）

# ─── 音频处理 ───

MAX_SEGMENT_DURATION = 5        # 超长片段二次切分阈值（秒）
MAX_AUDIO_DURATION = 14400      # 最大音频时长 4 小时（秒）
MAX_AUDIO_FILE_SIZE = 1024      # 最大文件大小（MB）
MIN_AUDIO_DURATION = 1.0        # 最短音频时长（秒）

# ─── 缓存路径 ───

import tempfile
CACHE_DIR = os.path.join(tempfile.gettempdir(), "qwen3-asr-service")
UPLOADS_DIR = os.path.join(CACHE_DIR, "uploads")
AUDIO_CHUNKS_DIR = os.path.join(CACHE_DIR, "audio_chunks")
RESULTS_DIR = os.path.join(CACHE_DIR, "results")

# ─── 日志 ───

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "asr.log")

# ─── 任务队列 ───

MAX_QUEUE_SIZE = 100
TASK_TIMEOUT = 1800             # 单任务超时 30 分钟（秒）
TASK_RESULT_TTL = 3600          # 已完成任务保留时长（秒），默认 1 小时
TASK_CLEANUP_INTERVAL = 300     # 清理扫描间隔（秒），默认 5 分钟

# ─── serve-mode ───

SERVE_MODE = "standard"         # "standard" | "vllm"（由 main.py argparse 覆盖）

# ─── 配置文件 ───

CONFIG_FILE = None              # 本次生效的配置文件名（basename，/health 回显；None = 未加载）

# ─── 实时流式转写（路线 B / WS /v2/asr/stream）───

ENABLE_STREAM = False           # 是否挂载实时端点（standard 模式下 --enable-stream 开启）
MAX_STREAM_SESSIONS = 16        # 最大并发会话数（超额 WS 关闭 1013）
STREAM_VAD_CHUNK_MS = 200       # 在线 VAD 分块时长（毫秒）
STREAM_ASR_CONCURRENCY = 1      # ASR 解码并发上限（模型层有推理锁串行化，>1 无收益）
STREAM_MAX_SEGMENT_SEC = 12     # 长无停顿句兜底切分阈值（秒）
STREAM_MAX_SESSION_SECONDS = 3600   # 单会话最长时长（秒），超时回 session_timeout 并关闭
STREAM_MAX_FRAME_BYTES = 2 * 1024 * 1024    # 单条二进制帧上限（字节），超限拒帧不断连
STREAM_MAX_BACKLOG_BYTES = 8 * 1024 * 1024  # 会话处理积压上限（字节），超限回 backlog_overflow 断开
                                            # （16kHz PCM16 约合 4 分钟积压；离线/流式争抢推理时的保护阀）
STREAM_SAMPLE_RATE = 16000      # 内部统一采样率

# ─── 远场过滤（段级能量/SNR 门控，仅实时，默认关）───

STREAM_NOISE_FILTER = False         # 段级能量/SNR 门控总开关（opt-in，减少远场/环境音误触发）
STREAM_ENERGY_FLOOR_DBFS = -50.0    # 绝对能量门（dBFS，满量程参考）：段响度低于此直接丢弃
STREAM_SNR_MIN_DB = 6.0             # 自适应信噪比门（dB）：段相对会话噪声底不足此值丢弃；<=0 关闭该门

# ─── 说话人分离 ───

ENABLE_SPEAKER = False          # 总开关（--enable-speaker，由 main.py argparse 覆盖）
SPEAKER_THRESHOLD = 0.5         # 实时在线归簇余弦阈值（S0 spike 实测可用区间 [0.35, 0.65]）
SPEAKER_MAX = 8                 # 说话人数上限（实时硬上限；离线为谱聚类簇数搜索上界）
SPEAKER_MIN_SEG_MS = 1500       # 短于此的段不建新簇/不更新质心（S0 spike EXP7 标定）
SPEAKER_MAX_WINDOWS = 4000      # 离线滑窗数上限，超出均匀抽稀（谱聚类 N² 内存防护）

# ─── 声纹库 ───

ENABLE_SPEAKER_DB = False           # 总开关（依赖 enable_speaker 引擎加载成功 + api_key 非空）
SPEAKER_DB_PATH = "data/speakers.db"
SPEAKER_ID_THRESHOLD = 0.45         # 1:N 开集识别阈 τ_id（严于官方 1:1 验证阈 0.31）
SPEAKER_ID_MARGIN = 0.10            # top1-top2 margin δ（近邻打架时宁缺勿错）
SPEAKER_ENROLL_MIN_SEC = 3.0        # 手动登记单样本最短有效语音（VAD 后，秒）
SPEAKER_AUTO_ENROLL = True          # 离线 identify 未命中簇自动以「说话人_NN」占位名登记
SPEAKER_AUTO_ENROLL_MIN_SEC = 10.0  # 自动登记的簇最短语音总时长（严于手动登记）
SPEAKER_STORE_AUDIO = False         # 留存登记样本音频（扩大合规面，默认关）

# ─── vLLM（Phase 3）───

VLLM_GPU_MEMORY_UTILIZATION = 0.8
