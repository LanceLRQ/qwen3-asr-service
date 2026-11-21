"""DashScope 实时识别兼容 WS（/compat/dashscope/api-ws/v1/inference、/realtime）。

`/inference` 为 Paraformer 协议（header/payload 信封）；`/realtime` 为
Qwen3-ASR-Flash-Realtime 协议（与 DashScope 官方 `/api-ws/v1/realtime` 同构）。
两者复用 ws_bridge 骨架，仅提供信封翻译 adapter。Paraformer 每句 final →
`result-generated`（sentence_end=true，整句）；vLLM 路线 A 的累计 partial → 中间
`result-generated`（sentence_end=false），route B 无逐字增量故只发 final。sentence 带
sentence_id：每个任务从 1 递增，同一句的 partial/final 共用。支持连接复用：task-finished
后可再 run-task 起新会话。
"""
import base64
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket

import app.config as cfg
from app.api.compat.mappers import (
    final_to_dashscope_result,
    partial_to_dashscope_result,
    to_engine_language,
)
from app.api.compat.ws_bridge import run_compat_ws

logger = logging.getLogger(__name__)


class _OversizedAudioFrame:
    """让 ws_bridge 的既有帧上限检查拒绝编码超限帧，不分配大块哨兵内存。"""

    def __len__(self):
        return cfg.STREAM_MAX_FRAME_BYTES + 1


# 上游 Qwen3-ASR-Flash-Realtime 模型名；客户端可用 ?model= 覆盖（须为同一系列）
_DEFAULT_QWEN_MODEL = "qwen3-asr-flash-realtime"
_QWEN_MODEL_PREFIXES = ("qwen3-asr", "qwen-asr")


def _is_supported_qwen_model(model: str) -> bool:
    return model.lower().startswith(_QWEN_MODEL_PREFIXES)


def _map_run_task(obj: dict) -> dict:
    """DashScope run-task → StreamSession.configure 的 cfg_msg。"""
    params = (obj.get("payload") or {}).get("parameters") or {}
    cfg_msg = {}
    if params.get("sample_rate") is not None:
        cfg_msg["audio_fs"] = params["sample_rate"]
    hints = params.get("language_hints")
    engine_lang = to_engine_language(hints[0] if hints else None)
    if engine_lang is not None:
        cfg_msg["language"] = engine_lang
    return cfg_msg


class DashScopeRealtimeAdapter:
    """每连接一个实例：持有当前 task_id。支持连接复用（多轮 run-task）。"""

    reusable = True

    def __init__(self):
        self._task_id = None
        self._sentence_id = 1

    async def on_open(self, ws: WebSocket, backend):
        # DashScope 不在连接建立时发消息；等 run-task 后回 task-started
        pass

    def classify(self, m: dict):
        if m.get("bytes") is not None:
            return ("audio", m["bytes"])
        text = m.get("text")
        if not text:
            return ("ignore", None)
        try:
            obj = json.loads(text)
        except (ValueError, TypeError):
            return ("ignore", None)
        action = (obj.get("header") or {}).get("action")
        if action == "run-task":
            self._task_id = (obj.get("header") or {}).get("task_id") or uuid4().hex
            return ("configure", _map_run_task(obj))
        if action == "finish-task":
            return ("end", None)
        return ("ignore", None)

    async def on_configured(self, ws: WebSocket, warnings):
        self._sentence_id = 1
        if warnings:
            logger.info(f"[compat-ws/dashscope] 忽略未启用参数: {', '.join(warnings)}")
        await ws.send_json({
            "header": {"task_id": self._task_id, "event": "task-started", "attributes": {}},
            "payload": {},
        })

    def translate_partials(self, partial: dict):
        # R2：vLLM 路线 A 的累计 partial → 中间 result-generated(sentence_end=false)。
        # DashScope 中间结果本就累计，无需 diff，干净直发。route B 不产 partial 故不触发。
        event = partial_to_dashscope_result(partial, self._task_id)
        event["payload"]["output"]["sentence"]["sentence_id"] = self._sentence_id
        return [event]

    def translate_finals(self, final: dict):
        event = final_to_dashscope_result(final, self._task_id)
        event["payload"]["output"]["sentence"]["sentence_id"] = self._sentence_id
        # 同一句的 partial/final 共用编号；完成后才递增，避免客户端覆盖前句。
        self._sentence_id += 1
        return [event]

    def translate_error(self, code: str, message: str, *, fatal: bool = False):
        return {
            "header": {
                "task_id": self._task_id,
                "event": "task-failed",
                "error_code": code,
                "error_message": message,
                "attributes": {},
            },
            "payload": {},
        }

    async def on_finish(self, ws: WebSocket):
        await ws.send_json({
            "header": {"task_id": self._task_id, "event": "task-finished", "attributes": {}},
            "payload": {"output": {}, "usage": None},
        })
        # 清空，避免连接复用时两轮之间的错误事件误用上一轮 task_id
        self._task_id = None


class QwenASRRealtimeAdapter:
    """Qwen3-ASR-Flash-Realtime（DashScope `/api-ws/v1/realtime`）协议适配。

    事件名与 OpenAI transcription session 相同，但 `session.update` 的采样率放在
    `session.sample_rate`（OpenAI GA 在 `session.audio.input.format.rate`），
    结束会话用 `session.finish` / `session.finished`，中间结果用
    `conversation.item.input_audio_transcription.text`（text/stash）。

    音频解码、item 序号与 VAD/commit 生命周期自持，不依赖 openai_ws_routes 的
    VAD 扩展（基线未合并时也能独立工作）；只在协议信封上沿用 OpenAI 事件名。
    """

    reusable = False

    def __init__(self):
        self._item_seq = 0
        self._cur_item_id = None
        self._prev_item_id = None
        self._committed = False
        self._pending_input_error = None
        self._last_partial = ""
        self._audio_fs = None
        self._model = _DEFAULT_QWEN_MODEL
        self._language = "zh"
        self._session_id = f"sess_{uuid4().hex}"
        self._turn_detection = {
            "type": "server_vad",
            "threshold": 0.5,
            "silence_duration_ms": 200,
        }

    def _ensure_item(self, *, new_turn: bool = False) -> str:
        # Only consumer-side events can advance item state. A committed item remains current
        # until a later VAD start/partial proves that another utterance has begun; a delayed
        # final during the commit flush therefore still belongs to the committed item.
        if new_turn and self._committed:
            self._prev_item_id = self._cur_item_id
            self._cur_item_id = None
            self._committed = False
        if self._cur_item_id is None:
            self._cur_item_id = f"item_{self._item_seq}"
            self._item_seq += 1
            self._committed = False
        return self._cur_item_id

    def _commit_events(self, item_id: str) -> list[dict]:
        if self._committed:
            return []
        self._committed = True
        previous = self._prev_item_id
        return [
            {"type": "input_audio_buffer.committed", "event_id": self._event_id(),
             "previous_item_id": previous, "item_id": item_id},
            {"type": "conversation.item.created", "event_id": self._event_id(),
             "previous_item_id": previous,
             "item": {"id": item_id, "object": "realtime.item", "type": "message",
                      "status": "completed", "role": "user",
                      "content": [{"type": "input_audio", "transcript": None}]}},
        ]

    @staticmethod
    def _event_id() -> str:
        return f"event_{uuid4().hex}"

    def _session_payload(self) -> dict:
        return {
            "id": self._session_id,
            "object": "realtime.session",
            "model": self._model,
            "modalities": ["text"],
            "input_audio_format": "pcm16",
            "input_audio_transcription": {"language": self._language},
            "turn_detection": self._turn_detection,
        }

    async def on_open(self, ws: WebSocket, backend):
        model = ws.query_params.get("model")
        if model:
            if not _is_supported_qwen_model(model):
                # 本服务只提供 Qwen ASR 系列；明确拒绝而不是静默用默认模型跑
                await ws.send_json(self.translate_error(
                    "invalid_model", f"不支持的模型: {model}", fatal=True))
                await ws.close(code=1008)
                return
            self._model = model
        await ws.send_json({
            "event_id": self._event_id(),
            "type": "session.created",
            "session": self._session_payload(),
        })

    def _map_qwen_session_update(self, obj: dict) -> dict:
        sess = obj.get("session") or {}
        rate = sess.get("sample_rate")
        if rate is None:
            audio_in = (sess.get("audio") or {}).get("input") or {}
            rate = (audio_in.get("format") or {}).get("rate")
        try:
            rate = int(rate) if rate is not None else 16000
        except (TypeError, ValueError):
            rate = 16000
        lang = (sess.get("input_audio_transcription") or {}).get("language")
        if lang:
            self._language = lang
        cfg_msg = {"audio_fs": rate, "emit_vad_events": True}
        engine_lang = to_engine_language(lang)
        if engine_lang is not None:
            cfg_msg["language"] = engine_lang
        self._audio_fs = rate
        return cfg_msg

    def classify(self, m: dict):
        if m.get("bytes") is not None:
            return ("ignore", None)          # Qwen 音频同样走 base64-in-JSON
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
            if "turn_detection" in sess:
                self._turn_detection = sess["turn_detection"]
            return ("configure", self._map_qwen_session_update(obj))
        if t == "input_audio_buffer.append":
            b64 = obj.get("audio")
            if not b64:
                return ("ignore", None)
            max_encoded_bytes = 4 * ((cfg.STREAM_MAX_FRAME_BYTES + 2) // 3)
            if not isinstance(b64, str) or len(b64) > max_encoded_bytes:
                logger.warning("[compat-ws/qwen] base64 音频帧超过编码长度上限")
                return ("audio", _OversizedAudioFrame())
            try:
                pcm = base64.b64decode(b64, validate=True)
            except (ValueError, TypeError) as exc:
                logger.warning("[compat-ws/qwen] base64 音频帧格式非法: %s", exc)
                self._pending_input_error = ("invalid_value", "audio 必须是有效的 base64")
                # 基线 ws_bridge 没有独立的 adapter error 分支；走其 invalid_config
                # 回执路径，再由 translate_error 保持 Qwen 协议的 invalid_value 错误码。
                return ("configure", {"audio_fs": -1})
            return ("audio", pcm)
        if t == "input_audio_buffer.commit":
            return ("flush", None)
        if t == "session.finish":
            return ("end", None)
        return ("ignore", None)

    async def on_configured(self, ws: WebSocket, warnings):
        if warnings:
            logger.info(f"[compat-ws/qwen] 忽略未启用参数: {', '.join(warnings)}")
        await ws.send_json({
            "event_id": self._event_id(),
            "type": "session.updated",
            "session": self._session_payload(),
        })

    def translate_vad_start(self, f: dict):
        # 即使事件被 turn_detection=null 抑制，消费侧仍用 VAD 边界推进 item。
        item_id = self._ensure_item(new_turn=True)
        if self._turn_detection is None:
            return []
        return [{
            "event_id": self._event_id(),
            "type": "input_audio_buffer.speech_started",
            "audio_start_ms": int(f.get("start") or 0),
            "item_id": item_id,
        }]

    def translate_vad_end(self, f: dict):
        # turn_detection=null 时仅由客户端 commit 控制边界，不回传服务端 VAD 事件。
        if self._turn_detection is None:
            return []
        item_id = self._ensure_item()
        if self._committed:
            # 客户端已手动 commit 并收到回执；迟到的 VAD end 不再补发 speech_stopped
            return []
        events = [{
            "event_id": self._event_id(),
            "type": "input_audio_buffer.speech_stopped",
            "audio_end_ms": int(f.get("end") or 0),
            "item_id": item_id,
        }]
        events.extend(self._commit_events(item_id))
        return events

    async def on_commit(self, ws: WebSocket, *, has_audio: bool = True) -> None:
        """Qwen realtime 有 input_audio_buffer.commit；空缓冲报错，不创建空 item。

        DashScope Paraformer `/inference` 使用 finish-task，语义与此 Qwen commit 不同。
        默认值兼容尚未采用 has_audio 参数的旧 ws_bridge 调用方。
        """
        if not has_audio:
            # Qwen 文档规定空 buffer commit 产生 error；错误码沿用 OpenAI Realtime
            # input_audio_buffer_commit_empty（Qwen 文档未规定独立 code）。
            await ws.send_json(self.translate_error(
                "input_audio_buffer_commit_empty", "没有可提交的音频", fatal=False))
            return
        item_id = self._ensure_item()
        for ev in self._commit_events(item_id):
            await ws.send_json(ev)

    def translate_partials(self, partial: dict):
        # Qwen 中间结果：text=已确认前缀，stash=草稿后缀。本服务 partial 为累计全文且
        # 可能修订，整体放入 stash，客户端按 text+stash 展示即为当前全文。
        item_id = self._ensure_item(new_turn=True)
        return [{
            "event_id": self._event_id(),
            "type": "conversation.item.input_audio_transcription.text",
            "item_id": item_id,
            "content_index": 0,
            "language": self._language,
            "emotion": "neutral",
            "text": "",
            "stash": partial.get("text", ""),
        }]

    def translate_finals(self, final: dict):
        item_id = self._ensure_item()
        events = self._commit_events(item_id)
        events.append({
            "event_id": self._event_id(),
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "content_index": 0,
            "language": self._language,
            "emotion": "neutral",
            "transcript": final.get("text", ""),
        })
        self._prev_item_id = item_id
        self._cur_item_id = None
        self._committed = False
        self._last_partial = ""
        return events

    def translate_error(self, code: str, message: str, *, fatal: bool = False):
        if self._pending_input_error is not None:
            code, message = self._pending_input_error
            self._pending_input_error = None
            fatal = False
        return {
            "event_id": self._event_id(),
            "type": "error",
            "error": {
                "type": "server_error" if fatal else "invalid_request_error",
                "code": code,
                "message": message,
            },
        }

    async def on_finish(self, ws: WebSocket):
        await ws.send_json({
            "event_id": self._event_id(),
            "type": "session.finished",
            "session": {"id": self._session_id},
        })


def build_dashscope_ws_router(prefix: str = "/compat/dashscope/api-ws/v1") -> APIRouter:
    r = APIRouter(prefix=prefix)

    @r.websocket("/inference")
    async def dashscope_realtime(ws: WebSocket):
        await run_compat_ws(ws, DashScopeRealtimeAdapter())

    # Qwen3-ASR-Flash-Realtime（DashScope 官方 /api-ws/v1/realtime 同路径）
    @r.websocket("/realtime")
    async def dashscope_qwen_realtime(ws: WebSocket):
        await run_compat_ws(ws, QwenASRRealtimeAdapter())

    return r
