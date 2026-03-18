from pydantic import BaseModel


class ASRResponse(BaseModel):
    task_id: str


class TaskStatusResponse(BaseModel):
    task_id: str
    status: str             # "pending" | "processing" | "completed" | "failed" | "not_found"
    progress: float
    result: dict | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    status: str             # "ready" | "loading" | "error"
    device: str             # "cuda" | "cpu"
    model_size: str         # "0.6b" | "1.7b"
    align_enabled: bool
    punc_enabled: bool
    asr_backend: str        # "qwen_asr" | "openvino"
    vad_backend: str        # "pytorch" | "onnx"
    punc_backend: str       # "pytorch" | "onnx"
