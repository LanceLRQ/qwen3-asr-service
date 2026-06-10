"""DashScope Paraformer 录音文件识别兼容接口（/compat/dashscope/api/v1/*）。

异步模型与 v2 天然同构：提交 file_urls → 父任务聚合 N 个子任务（服务端下载 URL 入队
TaskManager）→ 轮询返回 results[]（含二跳 transcription_url）→ 二跳下载转写文档。
下载+入队走 FastAPI BackgroundTasks（响应先返回再执行，保持异步语义）。父子任务注册表
仅内存 + TTL（重启丢失未取结果的映射，reference 注明）。诚实降级：speaker_count/channel_id
等忽略+日志；下载失败/队列满 → 子任务 FAILED 隔离。
"""
import asyncio
import logging
import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass

import anyio
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import app.config as cfg
from app.api.compat.errors import DashScopeCompatError
from app.api.compat.fetch import FetchError, _safe_remove, fetch_to_local
from app.api.compat.mappers import (
    result_to_dashscope_transcript, to_engine_language, v2status_to_dashscope)
from app.api.compat.schemas import DashScopeSubmitRequest
from app.api.routes import api_key_matches

logger = logging.getLogger(__name__)

MAX_FILE_URLS = 16          # 单请求 file_urls 数量上限（防滥用）
_FETCH_CONCURRENCY = 4      # 父任务内子任务下载并发上限
_REGISTRY_TTL = 3600        # 父任务注册表保留秒数（惰性清理）

_bearer_scheme = HTTPBearer(auto_error=False)

# 运行时依赖（由 init_dashscope_routes 注入）
_task_manager = None

# 父任务注册表（内存 + TTL）
_registry: dict = {}
_registry_lock = threading.Lock()


@dataclass
class SubTask:
    idx: int
    file_url: str
    inner_id: str | None = None        # TaskManager task_id；下载/入队失败为 None
    status: str | None = None          # 固定 FAILED（下载失败/队列满）；否则随 inner 状态
    code: str | None = None
    message: str | None = None


@dataclass
class ParentRec:
    parent_id: str
    subtasks: list
    language: str | None
    options: dict
    created_at: float
    last_access: float       # TTL 基于最后访问，避免活跃长轮询任务被误删


def init_dashscope_routes(*, task_manager):
    """注入运行时依赖：task_manager 入队/查询。"""
    global _task_manager
    _task_manager = task_manager


async def verify_dashscope_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
):
    """Bearer 校验（复用 routes.api_key_matches），失败抛 DashScope 风格 401。"""
    if not api_key_matches(credentials):
        raise DashScopeCompatError(401, "Invalid or missing API key", code="InvalidApiKey")


# ─── 注册表辅助 ───

def _purge_expired_locked() -> None:
    now = time.time()
    expired = [pid for pid, r in _registry.items() if now - r.last_access > _REGISTRY_TTL]
    for pid in expired:
        del _registry[pid]


def _get_rec(task_id: str) -> ParentRec:
    with _registry_lock:
        _purge_expired_locked()          # 读路径也清理，不依赖新 submit 到来
        rec = _registry.get(task_id)
        if rec is not None:
            rec.last_access = time.time()
    if rec is None:
        raise DashScopeCompatError(404, "task_id 不存在或已过期", code="UNKNOWN_TASK")
    return rec


def _external_base(request: Request) -> str:
    """回链外部基址：优先配置项，否则按 X-Forwarded-* / 请求地址推导。"""
    base = cfg.COMPAT_EXTERNAL_BASE_URL
    if base:
        return base.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (request.headers.get("x-forwarded-host")
            or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host}"


def _transcription_url(request: Request, task_id: str, idx: int) -> str:
    return f"{_external_base(request)}/compat/dashscope/api/v1/tasks/{task_id}/transcription/{idx}"


# ─── 后台下载 + 入队（BackgroundTasks）───

async def _process_parent(rec: ParentRec) -> None:
    """并发下载各 file_url 并入队 TaskManager；失败隔离到子任务。"""
    max_mb = cfg.COMPAT_FETCH_MAX_MB or cfg.MAX_AUDIO_FILE_SIZE
    limiter = anyio.CapacityLimiter(_FETCH_CONCURRENCY)

    async def _one(sub: SubTask):
        async with limiter:
            try:
                path = await fetch_to_local(
                    sub.file_url, max_mb=max_mb, timeout_s=cfg.COMPAT_FETCH_TIMEOUT,
                    allow_private=cfg.COMPAT_FETCH_ALLOW_PRIVATE)
            except FetchError as e:
                sub.status, sub.code, sub.message = "FAILED", e.code, e.message
                logger.warning(f"[compat/dashscope] 下载失败 {sub.file_url}: {e.message}")
                return
            try:
                # submit 是同步阻塞（队列 + 可选 SQLite 写）→ to_thread 避免阻塞事件循环
                sub.inner_id = await asyncio.to_thread(
                    _task_manager.submit,
                    file_path=path, language=rec.language,
                    wav_name=os.path.basename(sub.file_url) or "audio.wav",
                    options=rec.options)
            except queue.Full:
                _safe_remove(path)
                sub.status, sub.code, sub.message = "FAILED", "Throttling", "任务队列已满"

    async with anyio.create_task_group() as tg:
        for sub in rec.subtasks:
            tg.start_soon(_one, sub)


# ─── 端点 ───

async def create_transcription(body: DashScopeSubmitRequest, request: Request,
                               background: BackgroundTasks):
    """提交（异步）：要求 X-DashScope-Async: enable；登记父任务，后台下载+入队后立即返回。"""
    if request.headers.get("x-dashscope-async", "").lower() != "enable":
        raise DashScopeCompatError(
            400, "缺少必需 header X-DashScope-Async: enable", code="InvalidParameter")
    if _task_manager is None:
        raise DashScopeCompatError(503, "Service not ready", code="ServiceUnavailable")

    file_urls = body.input.file_urls
    if not file_urls:
        raise DashScopeCompatError(400, "input.file_urls 不能为空", code="InvalidParameter")
    if len(file_urls) > MAX_FILE_URLS:
        raise DashScopeCompatError(
            400, f"file_urls 数量超过上限 {MAX_FILE_URLS}", code="InvalidParameter")

    params = body.parameters
    language = to_engine_language(
        params.language_hints[0] if params and params.language_hints else None)
    options = {}
    if params:
        if params.diarization_enabled is not None:
            options["diarize"] = params.diarization_enabled
        ignored = [k for k, v in (("speaker_count", params.speaker_count),
                                  ("channel_id", params.channel_id)) if v is not None]
        if ignored:
            logger.info(f"[compat/dashscope] 忽略不支持参数: {', '.join(ignored)}")

    parent_id = uuid.uuid4().hex
    now = time.time()
    rec = ParentRec(parent_id, [SubTask(idx=i, file_url=u) for i, u in enumerate(file_urls)],
                    language, options, created_at=now, last_access=now)
    with _registry_lock:
        _purge_expired_locked()
        _registry[parent_id] = rec

    background.add_task(_process_parent, rec)
    return {"output": {"task_id": parent_id, "task_status": "PENDING"},
            "request_id": uuid.uuid4().hex}


def _aggregate(statuses: list[str]) -> str:
    """子任务状态聚合为父任务 task_status。"""
    if any(s in ("PENDING", "RUNNING") for s in statuses):
        return "RUNNING" if any(s == "RUNNING" for s in statuses) else "PENDING"
    if any(s == "SUCCEEDED" for s in statuses):
        return "SUCCEEDED"
    return "FAILED"


async def query_task(task_id: str, request: Request):
    """轮询（GET/POST）：返回各子任务状态与二跳 transcription_url（completed 时）。"""
    rec = _get_rec(task_id)
    results = []
    for sub in rec.subtasks:
        if sub.status == "FAILED":        # 下载失败/队列满（固定终态）
            st, url = "FAILED", None
        elif sub.inner_id is None:        # 尚未入队（后台进行中）
            st, url = "PENDING", None
        else:
            t = _task_manager.get_task(sub.inner_id) or {}
            st = v2status_to_dashscope(t.get("status", "pending"))
            url = (_transcription_url(request, task_id, sub.idx)
                   if t.get("status") == "completed" else None)
        results.append({
            "file_url": sub.file_url,
            "transcription_url": url,
            "subtask_status": st,
            "code": sub.code,
            "message": sub.message,
        })

    # 单一来源：父状态与计数都从 statuses 派生，避免两处规则漂移
    statuses = [r["subtask_status"] for r in results]
    metrics = {"TOTAL": len(statuses),
               "SUCCEEDED": statuses.count("SUCCEEDED"),
               "FAILED": statuses.count("FAILED")}
    return {"request_id": uuid.uuid4().hex,
            "output": {"task_id": task_id, "task_status": _aggregate(statuses),
                       "results": results, "task_metrics": metrics},
            "usage": {"duration": 0}}


async def get_transcription(task_id: str, idx: int):
    """二跳：返回子任务的 DashScope 转写结果文档（毫秒）。"""
    rec = _get_rec(task_id)
    sub = next((s for s in rec.subtasks if s.idx == idx), None)
    if sub is None or sub.inner_id is None:
        raise DashScopeCompatError(404, "子任务不存在或未就绪", code="UNKNOWN_TASK")
    t = _task_manager.get_task(sub.inner_id)
    if not t or t.get("status") != "completed":
        raise DashScopeCompatError(404, "转写结果尚未就绪", code="UNKNOWN_TASK")
    return result_to_dashscope_transcript(t.get("result") or {}, sub.file_url)


def build_dashscope_router(prefix: str = "/compat/dashscope/api/v1") -> APIRouter:
    """DashScope 兼容路由工厂；仅在 --enable-dashscope-api 时挂载。"""
    r = APIRouter(prefix=prefix)
    dep = [Depends(verify_dashscope_key)]
    r.add_api_route("/services/audio/asr/transcription", create_transcription,
                    methods=["POST"], dependencies=dep)
    r.add_api_route("/tasks/{task_id}", query_task, methods=["GET", "POST"], dependencies=dep)
    r.add_api_route("/tasks/{task_id}/transcription/{idx}", get_transcription,
                    methods=["GET"], dependencies=dep)
    return r
