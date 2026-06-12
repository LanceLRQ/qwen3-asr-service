import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 服务配置
HOST = "127.0.0.1"
PORT = 8765
ENABLE_WEB = False              # Web UI 是否挂载（--web 实际挂载时置位；根路径据此跳转）

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

# ─── 兼容接口（/compat/*）───

ENABLE_OPENAI_API = False        # 挂载 OpenAI 兼容接口 /compat/openai/v1/*
OPENAI_SYNC_TIMEOUT = 300        # OpenAI 同步转写等待上限（秒），超时返回 504
ENABLE_DASHSCOPE_API = False     # 挂载 DashScope 兼容接口 /compat/dashscope/api/v1/*
COMPAT_FETCH_MAX_MB = None       # DashScope file_urls 下载大小上限 MB（None=同 MAX_AUDIO_FILE_SIZE）
COMPAT_FETCH_TIMEOUT = 120       # file_urls 下载整体超时（秒）
COMPAT_FETCH_ALLOW_PRIVATE = False  # 允许下载私网地址（SSRF 默认禁止）
COMPAT_EXTERNAL_BASE_URL = None  # 兼容层回链外部基址（反代/容器；None=按请求推导）

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

# ─── vLLM（路线 A：原生流式）───

VLLM_GPU_MEMORY_UTILIZATION = 0.6   # 显存占用率（×总显存为预算；实占略低）：单流 ASR 无需 0.8 的大 KV 池
VLLM_MAX_MODEL_LEN = 32768          # 单序列上下文上限：ASR 单句远小于此；过大(默认65536)会抬高 KV 下限致低占用率起不来
VLLM_CHUNK_SIZE_SEC = 1.0           # 流式解码块大小（秒）：越小 partial 越细腻（V0 实测定档）
VLLM_UNFIXED_CHUNK_NUM = 2          # 前 N 块不拿历史当前缀（冷启动稳定）
VLLM_UNFIXED_TOKEN_NUM = 5          # N 块后回滚末 K token 当前缀（降抖动）
VLLM_CONCURRENCY = 1                # 同时解码会话数（generate 串行，>1 无吞吐收益）
VLLM_MAX_UTTERANCE_SEC = 20         # 单句兜底切分（秒）：约束上下文/显存增长（非性能必需）
VLLM_ENERGY_FLOOR_DBFS = -45.0      # 能量端点门限（dBFS）：高于此判为语音/句开始
VLLM_END_SILENCE_MS = 800           # 能量端点尾静音判停（ms）
VLLM_ENABLE_ALIGN = True            # 离线词级时间戳：加载 ForcedAligner（HF 拉取 ~0.6B；--no-vllm-align 可关省显存）
VLLM_ALIGN_DEVICE = "cuda"          # 对齐器加载设备：cuda 快但显存在 gpu_memory_utilization 预算外，长音频易 OOM 时改 cpu（float32，无 GPU 争用）
VLLM_INFER_BATCH_SIZE = 4           # qwen_asr max_inference_batch_size：一次对齐/ASR 的音频块数（块≤180s）。-1=全部一次（长音频对齐前向激活叠加易 OOM）；小值省显存
VLLM_OFFLINE_CHUNK_SEC = 180        # 离线逐块转写的切块时长（秒）：长音频按静音边界切块、块间报进度+查取消+压显存；≤此值的短音频单块直转（与 qwen 对齐切块上限一致，质量不变）
VLLM_SEGMENT_GAP_MS = 500           # 离线分段：相邻词间隙 > 此值断句（无 FSMN-VAD，以词间隙替代）
