"""OpenAI Realtime transcription 兼容 WS（/compat/openai/v1/realtime）。

复用 ws_bridge 骨架，仅提供协议翻译 adapter。Stage A：route B 每句 final → OpenAI
`conversation.item.input_audio_transcription.completed`（整句），不发 `.delta`（route B
无逐字增量，capabilities partial_results=false）。音频经 base64-in-JSON 解码后喂入会话。

信封以 GA transcription session 为准，session.update 同时接受 GA / beta 字段路径。
"""
import base64
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket

from app.api.compat.mappers import final_to_openai_completed
from app.api.compat.ws_bridge import run_compat_ws

logger = logging.getLogger(__name__)


def _map_session_update(obj: dict) -> dict:
    """OpenAI session.update → StreamSession.configure 的 cfg_msg（兼容 GA / beta 字段路径）。"""
    sess = obj.get("session") or {}
    # GA: session.audio.input.{format.rate, transcription.language}
    audio_in = (sess.get("audio") or {}).get("input") or {}
    rate = (audio_in.get("format") or {}).get("rate")
    lang = (audio_in.get("transcription") or {}).get("language")
    # beta: session.input_audio_transcription.language（无显式 rate）
    if lang is None:
        lang = (sess.get("input_audio_transcription") or {}).get("language")
    cfg_msg = {}
    # OpenAI Realtime pcm16 惯例 24kHz；客户端未声明 rate 时按此默认（本服务内部重采样到 16k）
    cfg_msg["audio_fs"] = rate if rate is not None else 24000
    if lang is not None:
        cfg_msg["language"] = lang
    return cfg_msg


class OpenAIRealtimeAdapter:
    """每连接一个实例：持有 item 序号。OpenAI 单会话（不复用连接）。"""

    reusable = False

    def __init__(self):
        self._item_seq = 0
        self._audio_fs = None      # 上次 configure 采用的采样率（session.updated 回显）

    async def on_open(self, ws: WebSocket, backend):
        await ws.send_json({
            "type": "session.created",
            "session": {
                "id": f"sess_{uuid4().hex}",
                "object": "realtime.transcription_session",
                "expires_at": 0,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": "qwen3-asr"},
                "turn_detection": {"type": "server_vad"},
            },
        })

    def classify(self, m: dict):
        if m.get("bytes") is not None:
            return ("ignore", None)              # OpenAI 音频走 base64-in-JSON，不收二进制
        text = m.get("text")
        if not text:
            return ("ignore", None)
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return ("ignore", None)
        t = obj.get("type")
        if t == "session.update":
            cfg_msg = _map_session_update(obj)
            self._audio_fs = cfg_msg.get("audio_fs")
            return ("configure", cfg_msg)
        if t == "input_audio_buffer.append":
            b64 = obj.get("audio")
            if not b64:
                return ("ignore", None)
            try:
                return ("audio", base64.b64decode(b64))
            except Exception:
                logger.warning("[compat-ws/openai] base64 解码失败，忽略该帧")
                return ("ignore", None)
        if t == "input_audio_buffer.commit":
            return ("flush", None)
        return ("ignore", None)

    async def on_configured(self, ws: WebSocket, warnings):
        if warnings:
            logger.info(f"[compat-ws/openai] 忽略未启用参数: {', '.join(warnings)}")
        # 回显服务端采用的采样率，客户端据此可检测与实发 PCM 的不一致（未声明 rate 时为默认 24000）
        await ws.send_json({
            "type": "session.updated",
            "session": {
                "object": "realtime.transcription_session",
                "audio": {"input": {"format": {"type": "audio/pcm", "rate": self._audio_fs}}},
            },
        })

    def translate_finals(self, final: dict):
        item_id = f"item_{self._item_seq}"
        self._item_seq += 1
        return [final_to_openai_completed(final, item_id)]

    def translate_error(self, code: str, message: str, *, fatal: bool = False):
        return {
            "type": "error",
            "error": {
                "type": "server_error" if fatal else "invalid_request_error",
                "code": code,
                "message": message,
            },
        }

    async def on_finish(self, ws: WebSocket):
        # OpenAI 无显式结束事件（靠 VAD 自动产 completed / 连接关闭），无需额外消息
        pass


def build_openai_ws_router(prefix: str = "/compat/openai/v1") -> APIRouter:
    r = APIRouter(prefix=prefix)

    @r.websocket("/realtime")
    async def openai_realtime(ws: WebSocket):
        await run_compat_ws(ws, OpenAIRealtimeAdapter())

    return r
