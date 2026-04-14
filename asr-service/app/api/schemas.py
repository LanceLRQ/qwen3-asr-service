from pydantic import BaseModel


class ASRResponse(BaseModel):
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str             # "pending" | "processing" | "completed" | "failed" | "cancelled" | "not_found"
    progress: float
    result: dict | None = None
    error: str | None = None


class TaskListItem(BaseModel):
    task_id: str
    status: str
    progress: float
    language: str | None = None
    created_at: str
    finished_at: float | None = None
    error: str | None = None


class TaskListResponse(BaseModel):
    total: int
    tasks: list[TaskListItem]


class CancelResponse(BaseModel):
    task_id: str
    status: str     # "cancelled" | "already_completed" | "already_failed" | "already_cancelled" | "not_found"
    message: str


class HealthResponse(BaseModel):
    status: str             # "ready" | "loading" | "error"
    device: str             # "cuda" | "cpu"
    model_size: str         # "0.6b" | "1.7b"
    align_enabled: bool
    punc_enabled: bool
    asr_backend: str        # "qwen_asr" | "openvino"
    vad_backend: str        # "pytorch" | "onnx"
    punc_backend: str       # "pytorch" | "onnx"
