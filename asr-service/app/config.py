import os

# 项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 服务配置
HOST = "0.0.0.0"
PORT = 8765

# ─── 启动参数默认值（由 main.py argparse 覆盖） ───

DEVICE = "auto"                 # "auto" | "cuda" | "cpu"
ASR_MODEL_SIZE = "0.6b"         # "0.6b" | "1.7b"
ENABLE_ALIGN = True             # 是否加载对齐模型
ENABLE_PUNC = True              # 是否启用标点恢复
MODEL_SOURCE = "modelscope"     # "modelscope" | "huggingface"

# ─── 模型路径 ───

MODELS_DIR = os.path.join(BASE_DIR, "models")
ASR_MODEL_DIR = os.path.join(MODELS_DIR, "asr")
ALIGN_MODEL_DIR = os.path.join(MODELS_DIR, "align")
VAD_MODEL_DIR = os.path.join(MODELS_DIR, "vad")
PUNC_MODEL_DIR = os.path.join(MODELS_DIR, "punc")

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
}

# ─── VAD 参数 ───

VAD_MAX_SILENCE = 800           # 尾部静音时长 ms
VAD_SPEECH_NOISE_THRES = 0.5    # 语音/噪声阈值

# ─── 音频处理 ───

MAX_SEGMENT_DURATION = 30       # 超长片段二次切分阈值（秒）
MAX_AUDIO_DURATION = 14400      # 最大音频时长 4 小时（秒）
MAX_AUDIO_FILE_SIZE = 1024      # 最大文件大小（MB）
MIN_AUDIO_DURATION = 1.0        # 最短音频时长（秒）

# ─── 缓存路径 ───

CACHE_DIR = os.path.join(BASE_DIR, "cache")
UPLOADS_DIR = os.path.join(CACHE_DIR, "uploads")
AUDIO_CHUNKS_DIR = os.path.join(CACHE_DIR, "audio_chunks")
RESULTS_DIR = os.path.join(CACHE_DIR, "results")

# ─── 日志 ───

LOG_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "asr.log")

# ─── 任务队列 ───

MAX_QUEUE_SIZE = 100
TASK_TIMEOUT = 1800             # 单任务超时 30 分钟（秒）
