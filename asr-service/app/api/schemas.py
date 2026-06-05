from pydantic import BaseModel


class ASRResponse(BaseModel):
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str             # "pending" | "processing" | "completed" | "failed" | "cancelled" | "not_found"
    progress: float
    result: dict | None = None
    error: str | None = None
    wav_name: str | None = None      # 原始文件名（展示用）
    created_at: str | None = None
    finished_at: str | None = None


class TaskListItem(BaseModel):
    task_id: str
    status: str
    progress: float
    language: str | None = None
    wav_name: str | None = None      # 原始文件名（展示用）
    created_at: str
    finished_at: str | None = None
    error: str | None = None


class TaskListResponse(BaseModel):
    total: int
    tasks: list[TaskListItem]


class CancelResponse(BaseModel):
    task_id: str
    status: str     # "cancelled" | "already_completed" | "already_failed" | "already_cancelled"
                    # | "deleted"（持久化历史记录已删除） | "not_found"
    message: str


class StreamCapabilities(BaseModel):
    enabled: bool = False
    backend: str | None = None        # "vad-offline" | "vllm-native"
    path: str | None = None           # "/v2/asr/stream"（统一端点）
    partial_results: bool = False
    word_timestamps: bool = False
    speaker_labels: bool = False      # 实时 final.speaker（匿名 A/B/C…）


class CapabilitiesResponse(BaseModel):
    mode: str                          # "standard" | "vllm"
    offline_api: bool
    speaker_labels: bool = False       # 说话人分离总开关（离线+实时同一开关）
    stream: StreamCapabilities


class HealthResponse(BaseModel):
    """健康检查响应（mode-aware）。仅新增字段/放宽为可选，向后兼容：
    standard 模式响应与原有字段一致，vllm 模式不适用字段为 null。"""
    status: str                        # "ready" | "loading" | "error"
    mode: str = "standard"             # 当前运行模式："standard" | "vllm"
    device: str                        # "cuda" | "cpu"
    model_size: str | None = None      # "0.6b" | "1.7b"
    align_enabled: bool = False
    punc_enabled: bool = False
    speaker_enabled: bool = False
    asr_backend: str | None = None     # "qwen_asr" | "openvino"
    vad_backend: str | None = None     # "pytorch" | "onnx"
    punc_backend: str | None = None    # "pytorch" | "onnx"
    config_file: str | None = None     # 本次生效的配置文件名（None = 未加载配置文件）
    capabilities: CapabilitiesResponse | None = None
