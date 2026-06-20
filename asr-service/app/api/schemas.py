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


class StreamRecordingDeleteResponse(BaseModel):
    recording_id: str
    status: str     # "deleted" | "not_found"
    deleted: bool


class SpeakerTemplateInfo(BaseModel):
    id: int
    dur_sec: float
    created_at: str


class SpeakerInfo(BaseModel):
    id: str                            # uuid4 hex（32 字符）
    name: str                          # 显示名（自动登记为「说话人_NN」，PATCH 可改）
    note: str | None = None
    source: str = "manual"             # "manual" | "auto"（自动登记）
    template_count: int | None = None  # 列表项携带
    model_tag: str | None = None       # 详情携带
    templates: list[SpeakerTemplateInfo] | None = None   # 详情携带（不含向量本体）
    created_at: str
    updated_at: str | None = None


class SpeakerListResponse(BaseModel):
    total: int
    speakers: list[SpeakerInfo]


class EnrollResponse(BaseModel):
    speaker_id: str
    name: str
    templates: int
    quality_hint: str | None = None    # 模板数不足建议值时的提示（警告不阻断）


class IdentifyResponse(BaseModel):
    matched: bool
    speaker_id: str | None = None
    name: str | None = None
    score: float | None = None


class SpeakerUpdateRequest(BaseModel):
    name: str | None = None
    note: str | None = None


class SpeakerDeleteResponse(BaseModel):
    speaker_id: str
    deleted: bool = True


class TemplateDeleteResponse(BaseModel):
    speaker_id: str
    template_id: int
    remaining: int
    hint: str | None = None            # 剩 0 模板时提示补样本或删除说话人


class StreamCapabilities(BaseModel):
    enabled: bool = False
    backend: str | None = None        # "vad-offline" | "vllm-native"
    path: str | None = None           # "/v2/asr/stream"（统一端点）
    partial_results: bool = False
    word_timestamps: bool = False
    speaker_labels: bool = False      # 实时 final.speaker（匿名 A/B/C…）
    save_audio: bool = False          # 是否保存实时录音 WAV
    recording_retention_hours: int = 72
    recording_download_path: str | None = None


class CapabilitiesResponse(BaseModel):
    mode: str                          # "standard" | "vllm"
    offline_api: bool
    speaker_labels: bool = False       # 说话人分离总开关（离线+实时同一开关）
    speaker_identification: bool = False   # 声纹库真名识别（enroll/identify 可用）
    stream: StreamCapabilities
    defaults: dict = {}                # 可覆盖参数的当前生效默认值（Web UI 占位提示，反映实际配置）
    compat: dict = {}                  # 兼容接口已挂端点（vLLM Phase 3：openai/dashscope/realtime/realtime_partial）


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
    speaker_db_enabled: bool = False
    asr_backend: str | None = None     # "qwen_asr" | "openvino"
    vad_backend: str | None = None     # "pytorch" | "onnx"
    punc_backend: str | None = None    # "pytorch" | "onnx"
    config_file: str | None = None     # 本次生效的配置文件名（None = 未加载配置文件）
    capabilities: CapabilitiesResponse | None = None
