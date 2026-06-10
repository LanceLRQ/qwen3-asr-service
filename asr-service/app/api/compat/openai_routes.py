"""OpenAI 音频转写兼容接口（/compat/openai/v1/*）。

复用现有离线链路：流式落盘（同 routes 常量/思路）→ TaskManager.submit 入队 →
wait_done 同步等待终态 → mappers 渲染 response_format。鉴权复用 Bearer 校验逻辑，
但错误信封走 OpenAI 风格（OpenAICompatError）。不支持的能力诚实降级：translation→501、
prompt/temperature→忽略+日志、stream→400，绝不伪造。
"""
import asyncio
import json
import logging
import os
import queue
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import app.config as cfg
from app.api.compat.errors import OpenAICompatError
from app.api.compat.mappers import (
    result_to_openai, result_to_openai_sse_events, to_engine_language)
from app.api.routes import ALLOWED_EXTENSIONS, UPLOAD_CHUNK_SIZE, api_key_matches
from app.config import MAX_AUDIO_FILE_SIZE, UPLOADS_DIR

logger = logging.getLogger(__name__)

_VALID_FORMATS = {"json", "text", "srt", "verbose_json", "vtt"}
_bearer_scheme = HTTPBearer(auto_error=False)

# 运行时依赖（由 init_openai_routes 注入）
_task_manager = None
_service_info = None


def init_openai_routes(*, task_manager, service_info=None):
    """注入运行时依赖：task_manager 入队/等待；service_info 供 GET /models 读模型信息。"""
    global _task_manager, _service_info
    _task_manager = task_manager
    _service_info = service_info or {}


async def verify_openai_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
):
    """Bearer 校验（复用 routes.api_key_matches），失败抛 OpenAI 风格 401。"""
    if not api_key_matches(credentials):
        raise OpenAICompatError(401, "Invalid or missing API key", code="invalid_api_key")


async def _save_upload(file: UploadFile) -> str:
    """流式落盘 + 扩展名/大小校验（复用 v2 常量），错误抛 OpenAI 风格。"""
    file_ext = os.path.splitext(file.filename or "audio.wav")[1].lower() or ".wav"
    if file_ext not in ALLOWED_EXTENSIONS:
        raise OpenAICompatError(
            400, f"Unsupported audio format '{file_ext}'", param="file", code="invalid_value")

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    save_path = os.path.join(UPLOADS_DIR, f"{uuid.uuid4()}{file_ext}")
    max_bytes = MAX_AUDIO_FILE_SIZE * 1024 * 1024
    total = 0
    try:
        with open(save_path, "wb") as f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise OpenAICompatError(
                        413, f"File too large (>{MAX_AUDIO_FILE_SIZE}MB)",
                        param="file", code="invalid_value")
                f.write(chunk)
    except OpenAICompatError:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise
    return save_path


async def create_transcription(
    file: UploadFile = File(...),
    model: str = Form(...),
    language: str | None = Form(None),
    prompt: str | None = Form(None),
    response_format: str = Form("json"),
    temperature: float | None = Form(None),
    stream: bool = Form(False),
    timestamp_granularities: list[str] | None = Form(
        None, alias="timestamp_granularities[]"),
):
    """同步转写：上传音频，响应即返回结果（内部走队列等待，上限 --openai-sync-timeout）。"""
    if _task_manager is None:
        raise OpenAICompatError(503, "Service not ready", err_type="server_error",
                                code="service_unavailable")
    if response_format not in _VALID_FORMATS:
        raise OpenAICompatError(
            400, f"Invalid value for 'response_format': {response_format}",
            param="response_format", code="invalid_value")

    ignored = [p for p, v in (("prompt", prompt), ("temperature", temperature)) if v is not None]
    if ignored:
        logger.info(f"[compat/openai] 忽略不支持参数: {', '.join(ignored)}")

    want_word_ts = "word" in (timestamp_granularities or [])
    path = await _save_upload(file)
    try:
        task_id = _task_manager.submit(
            file_path=path, language=to_engine_language(language), wav_name=file.filename,
            options={"with_words": want_word_ts})
    except queue.Full:
        os.remove(path)
        raise OpenAICompatError(503, "Task queue is full, retry later",
                                err_type="server_error", code="overloaded")

    task = await asyncio.to_thread(_task_manager.wait_done, task_id, cfg.OPENAI_SYNC_TIMEOUT)
    if task is None:
        raise OpenAICompatError(
            504, f"Transcription timed out (>{cfg.OPENAI_SYNC_TIMEOUT}s)",
            err_type="server_error", code="timeout")

    status = task.get("status")
    if status != "completed":
        msg = task.get("error") or f"Transcription {status}"
        raise OpenAICompatError(500, msg, err_type="server_error", code="internal_error")

    result = task.get("result") or {}
    if stream:
        # stream=true → SSE：整段解码后分句吐 delta，末尾 done（response_format 此时不适用）
        return StreamingResponse(
            _sse_events(result_to_openai_sse_events(result)),
            media_type="text/event-stream")

    rendered = result_to_openai(
        result, response_format=response_format,
        want_word_ts=want_word_ts, language=language)
    if isinstance(rendered, str):
        return PlainTextResponse(rendered)
    return rendered


async def _sse_events(events):
    """SSE 编码：每事件一行 `data: <json>`，事件间空行分隔。"""
    for ev in events:
        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


async def create_translation(
    file: UploadFile = File(...),
    model: str = Form(...),
):
    """翻译端点：本服务为纯语音识别，无翻译能力——固定 501，不伪造。"""
    raise OpenAICompatError(
        501, "This service performs speech recognition only; translation is not supported.",
        code="unsupported")


async def list_models():
    """模型清单：id 反映服务实际加载的模型大小。"""
    return {"object": "list", "data": [{
        "id": _model_id(), "object": "model", "created": 0, "owned_by": "qwen3-asr"}]}


def _model_id() -> str:
    size = (_service_info or {}).get("model_size") or "unknown"
    return f"qwen3-asr-{size}"


def build_openai_router(prefix: str = "/compat/openai/v1") -> APIRouter:
    """OpenAI 兼容路由工厂；仅在 --enable-openai-api 时挂载。"""
    r = APIRouter(prefix=prefix)
    dep = [Depends(verify_openai_key)]
    r.add_api_route("/audio/transcriptions", create_transcription, methods=["POST"], dependencies=dep)
    r.add_api_route("/audio/translations", create_translation, methods=["POST"], dependencies=dep)
    r.add_api_route("/models", list_models, methods=["GET"], dependencies=dep)
    return r
