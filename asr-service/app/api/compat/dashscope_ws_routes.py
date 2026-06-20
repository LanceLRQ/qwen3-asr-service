"""DashScope Paraformer 实时识别兼容 WS（/compat/dashscope/api-ws/v1/inference）。

复用 ws_bridge 骨架，仅提供 header/payload 信封翻译 adapter。Stage A：route B 每句
final → `result-generated`（sentence_end=true，整句），不发中间结果（sentence_end=false，
route B 无逐字增量）。支持连接复用：task-finished 后可再 run-task 起新会话。
"""
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket

from app.api.compat.mappers import (
    final_to_dashscope_result,
    partial_to_dashscope_result,
    to_engine_language,
)
from app.api.compat.ws_bridge import run_compat_ws

logger = logging.getLogger(__name__)


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

    async def on_configured(self, ws: WebSocket, warnings, recording=None):
        if warnings:
            logger.info(f"[compat-ws/dashscope] 忽略未启用参数: {', '.join(warnings)}")
        payload = {}
        if recording:
            payload["recording"] = recording
        await ws.send_json({
            "header": {"task_id": self._task_id, "event": "task-started", "attributes": {}},
            "payload": payload,
        })

    def translate_partials(self, partial: dict):
        # R2：vLLM 路线 A 的累计 partial → 中间 result-generated(sentence_end=false)。
        # DashScope 中间结果本就累计，无需 diff，干净直发。route B 不产 partial 故不触发。
        return [partial_to_dashscope_result(partial, self._task_id)]

    def translate_finals(self, final: dict):
        return [final_to_dashscope_result(final, self._task_id)]

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


def build_dashscope_ws_router(prefix: str = "/compat/dashscope/api-ws/v1") -> APIRouter:
    r = APIRouter(prefix=prefix)

    @r.websocket("/inference")
    async def dashscope_realtime(ws: WebSocket):
        await run_compat_ws(ws, DashScopeRealtimeAdapter())

    return r
