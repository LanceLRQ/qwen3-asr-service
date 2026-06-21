"""实时转写 WebSocket 类型化信封消息（统一协议，后端无关）。

服务端把各后端原始输出统一封装为带 `type` 的信封；客户端按 `type` 分发。
差异（是否产 partial、是否带 words）在 session.created.capabilities 中声明。
定义依 implementation-plan §3.5 / api-reference §四。
"""
from typing import Literal

from pydantic import BaseModel


# ── 客户端 → 服务端 ──

class StartMsg(BaseModel):
    type: Literal["start"] = "start"
    audio_fs: int = 16000
    language: str | None = None
    wav_name: str = "stream"
    identify_speakers: bool = False    # 声纹识别（需 speaker_identification 能力）
    # 远场过滤可选覆盖（缺省=服务端默认）；服务端范围钳制，仅影响本会话
    noise_filter: bool | None = None
    energy_floor_dbfs: float | None = None
    snr_min_db: float | None = None
    # 说话人分辨（需 speaker_labels；缺省=服务端默认）
    speaker_threshold: float | None = None
    speaker_min_seg_ms: int | None = None
    speaker_max: int | None = None
    speaker_id_threshold: float | None = None    # 声纹识别严格度（需 speaker_identification）
    speaker_id_margin: float | None = None
    # 响应快慢 / 分段
    max_end_silence_ms: int | None = None        # 断句尾静音
    max_segment_sec: int | None = None           # 长句兜底切分
    # 输出内容降级开关（只能关；开启需对应模型已加载，否则进 warnings 软提示）
    with_punc: bool | None = None
    with_words: bool | None = None
    diarize: bool | None = None


class StopMsg(BaseModel):
    type: Literal["stop"] = "stop"


# ── 服务端 → 客户端（全部带 type）──

class SessionCreated(BaseModel):
    type: Literal["session.created"] = "session.created"
    protocol: str = "qwen3-asr-stream"
    protocol_version: str = "1.0"
    mode: str                          # "standard" | "vllm"
    backend: str                       # "vad-offline" | "vllm-native"
    sample_rate: int = 16000
    capabilities: dict                 # {partial_results, word_timestamps, languages_auto}
    limits: dict = {}                  # {max_frame_bytes, max_backlog_bytes}，客户端据此控速


class PartialMsg(BaseModel):
    type: Literal["partial"] = "partial"
    seg_id: int
    text: str


class FinalMsg(BaseModel):
    type: Literal["final"] = "final"
    seg_id: int
    text: str
    start: int | None = None
    end: int | None = None
    words: list | None = None          # 仅 word_timestamps=true（路线 B 启用对齐）
    speaker: str | None = None         # 仅 speaker_labels=true（匿名标签 A/B/C…）
    speaker_name: str | None = None    # 仅 identify_speakers=true 且声纹库命中（以最新 final 为准）


class SceneMsg(BaseModel):
    type: Literal["scene"] = "scene"
    label: str                         # silence | speech | singing | music | other
    confidence: float
    since: int                         # 该场景状态起始时间戳(ms)
    scores: dict = {}                  # 各内容桶代表分（speech/singing/music），便于下游自定阈值


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    seg_id: int | None = None
    fatal: bool = False


class SessionClosed(BaseModel):
    type: Literal["session.closed"] = "session.closed"
    reason: str
