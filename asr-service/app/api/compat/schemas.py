"""兼容层 Pydantic 模型。

Phase 2：DashScope 录音文件识别提交请求体。上游额外参数（disfluency_removal_enabled /
special_word_filter / timestamp_alignment_enabled 等）未声明 → Pydantic 默认忽略。
"""
from pydantic import BaseModel, Field


class DashScopeInput(BaseModel):
    file_urls: list[str] = Field(default_factory=list)


class DashScopeParameters(BaseModel):
    language_hints: list[str] | None = None
    diarization_enabled: bool | None = None
    speaker_count: int | None = None          # 忽略：说话人数上限为服务级配置
    channel_id: list[int] | None = None        # 忽略：本服务单声道


class DashScopeSubmitRequest(BaseModel):
    model: str | None = None
    input: DashScopeInput = Field(default_factory=DashScopeInput)
    parameters: DashScopeParameters | None = None
