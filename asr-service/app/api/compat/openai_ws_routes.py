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

from app.api.compat.mappers import (
    final_to_openai_completed,
    partial_to_openai_delta,
    to_engine_language,
)
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
    # 让后端额外上抛 VAD start/end 帧，用于 speech_started / speech_stopped 事件
    cfg_msg["emit_vad_events"] = True
    engine_lang = to_engine_language(lang)
    if engine_lang is not None:
        cfg_msg["language"] = engine_lang
    return cfg_msg


class OpenAIRealtimeAdapter:
    """每连接一个实例：持有 item 序号。OpenAI 单会话（不复用连接）。"""

    reusable = False

    def __init__(self):
        self._item_seq = 0
        self._audio_fs = None      # 上次 configure 采用的采样率（session.updated 回显）
        self._last_partial = ""    # 当前句已下发的累计 partial 文本（R2 delta diff 基准）
        # VAD/commit 生命周期状态：同一句的 speech_started → speech_stopped →
        # committed → item.created → completed 必须共用同一个 item_id。
        self._cur_item_id = None
        self._prev_item_id = None
        self._committed = False
        self._turn_detection = {"type": "server_vad"}
        self._turn_detection_path = "unspecified"

    @staticmethod
    def _event_id() -> str:
        return f"event_{uuid4().hex}"

    def _ensure_item(self) -> str:
        if self._cur_item_id is None:
            self._cur_item_id = f"item_{self._item_seq}"
            self._item_seq += 1
            self._committed = False
        return self._cur_item_id

    def _commit_events(self, item_id: str) -> list[dict]:
        """committed + item.created（每句只发一次）。"""
        if self._committed:
            return []
        self._committed = True
        prev = self._prev_item_id
        return [
            {
                "type": "input_audio_buffer.committed",
                "event_id": self._event_id(),
                "previous_item_id": prev,
                "item_id": item_id,
            },
            {
                "type": "conversation.item.created",
                "event_id": self._event_id(),
                "previous_item_id": prev,
                "item": {
                    "id": item_id,
                    "object": "realtime.item",
                    "type": "message",
                    "status": "completed",
                    "role": "user",
                    "content": [{"type": "input_audio", "transcript": None}],
                },
            },
        ]

    def translate_vad_start(self, f: dict):
        # turn_detection=null 按 OpenAI 协议关闭服务端 VAD，因此不向客户端下发 speech 事件。
        if self._turn_detection is None:
            return []
        item_id = self._ensure_item()
        return [{
            "type": "input_audio_buffer.speech_started",
            "event_id": self._event_id(),
            "audio_start_ms": int(f.get("start") or 0),
            "item_id": item_id,
        }]

    def translate_vad_end(self, f: dict):
        # turn_detection=null 按 OpenAI 协议关闭服务端 VAD，不下发 speech 事件。
        if self._turn_detection is None:
            return []
        item_id = self._ensure_item()
        if self._committed:
            # 客户端已手动 commit 并收到回执；迟到的 VAD end 不再补发 speech_stopped，
            # 避免 committed 之后又出现 speech_stopped 的乱序事件。
            return []
        events = [{
            "type": "input_audio_buffer.speech_stopped",
            "event_id": self._event_id(),
            "audio_end_ms": int(f.get("end") or 0),
            "item_id": item_id,
        }]
        events.extend(self._commit_events(item_id))
        return events

    async def on_commit(self, ws: WebSocket, *, has_audio: bool = True):
        """客户端 commit 回执；队列已处理此前音频，空缓冲按 OpenAI 协议返回错误。"""
        if not has_audio:
            # 对齐 OpenAI：空缓冲 commit 返回错误，不创建 item 或提交回执。
            await ws.send_json({
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "code": "input_audio_buffer_commit_empty",
                    "message": "Error committing input audio buffer: buffer is empty.",
                },
            })
            return
        item_id = self._ensure_item()
        for ev in self._commit_events(item_id):
            await ws.send_json(ev)

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
            sess = obj.get("session") or {}
            missing = object()
            td = sess.get("turn_detection", missing)
            if td is not missing:
                self._turn_detection = td
                self._turn_detection_path = "beta"
            else:
                # GA transcription session: turn_detection 位于 session.audio.input 下
                td = ((sess.get("audio") or {}).get("input") or {}).get(
                    "turn_detection", missing)
                if td is not missing:
                    self._turn_detection = td
                    self._turn_detection_path = "ga"
                else:
                    self._turn_detection_path = "unspecified"
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
        # transcription session 默认使用 GA 结构；若客户端使用 beta 路径，则按 beta 回显。
        session = {
            "object": "realtime.transcription_session",
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": self._audio_fs}}},
        }
        if self._turn_detection_path == "beta":
            session["turn_detection"] = self._turn_detection
        else:
            session["audio"]["input"]["turn_detection"] = self._turn_detection
        await ws.send_json({"type": "session.updated", "session": session})

    def translate_partials(self, partial: dict):
        # R2 best-effort：vLLM partial 是累计且可修订，OpenAI delta 要增量片段。仅纯追加时取
        # 新增后缀作 delta（与即将到来的 completed 共用同一 item_id）；修订帧跳过 delta，
        # 权威全文以 completed 为准。route B 不产 partial 故不触发。
        new = partial.get("text", "")
        old = self._last_partial
        self._last_partial = new
        if not new.startswith(old):
            return []                        # 非纯追加（修订）→ 不发误导性增量
        delta = new[len(old):]
        if not delta:
            return []
        return [partial_to_openai_delta(delta, self._ensure_item())]

    def translate_finals(self, final: dict):
        item_id = self._ensure_item()
        # 长句兜底切分/未走 VAD end 的 flush 路径：补发 committed，保证客户端能收到回执。
        events = self._commit_events(item_id)
        events.append(final_to_openai_completed(final, item_id))
        self._prev_item_id = item_id
        self._cur_item_id = None
        self._committed = False
        self._last_partial = ""              # 本句结束 → 下一句 delta 基准归零
        return events

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
