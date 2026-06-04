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


class ErrorMsg(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str
    seg_id: int | None = None
    fatal: bool = False


class SessionClosed(BaseModel):
    type: Literal["session.closed"] = "session.closed"
    reason: str
