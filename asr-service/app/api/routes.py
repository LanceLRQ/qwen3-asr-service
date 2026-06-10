import asyncio
import os
import uuid
import hmac
import logging
import queue
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.api.schemas import ASRResponse, TaskStatusResponse, TaskListResponse, CancelResponse
from app.config import UPLOADS_DIR, MAX_AUDIO_FILE_SIZE
from app.utils.validation import (
    coerce_num_in_range, MAX_SEGMENT_RANGE,
    SPK_ID_THRESHOLD_RANGE, SPK_ID_MARGIN_RANGE,
)
import app.config as cfg

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


def api_key_matches(credentials: HTTPAuthorizationCredentials | None) -> bool:
    """Bearer 凭证是否匹配 cfg.API_KEY；未配置 API_KEY 时恒 True（放行）。

    单一鉴权谓词：兼容层 verify_openai_key/verify_dashscope_key 共用，仅错误信封各异。
    """
    if not cfg.API_KEY:
        return True
    return credentials is not None and hmac.compare_digest(
        credentials.credentials, cfg.API_KEY)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
):
    """配置了 API_KEY 时，要求请求携带有效的 Bearer token"""
    if not api_key_matches(credentials):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
        )


# 支持的音频文件扩展名
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".amr", ".opus"}

# 流式写入磁盘的分块大小
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

# 运行时依赖，由 main.py 启动时注入
_task_manager = None
_task_store = None


def init_routes(task_manager, task_store=None):
    """注入运行时依赖（task_store 可选：任务持久化关闭时为 None，读路径行为与现状一致）"""
    global _task_manager, _task_store
    _task_manager = task_manager
    _task_store = task_store


# ─── 离线批处理控制器（纯函数，v1/v2 共用同一组实现）───

async def submit_asr(
    file: UploadFile = File(...),
    language: str | None = Form(None),
    identify_speakers: bool = Form(False),
    with_punc: bool | None = Form(None),
    with_words: bool | None = Form(None),
    diarize: bool | None = Form(None),
    max_segment: int | None = Form(None),
    speaker_id_threshold: float | None = Form(None),
    speaker_id_margin: float | None = Form(None),
) -> ASRResponse:
    """提交 ASR 任务。

    可选按请求覆盖（缺省=服务端默认）：with_punc/with_words/diarize 降级开关、
    max_segment 分段时长、speaker_id_threshold/margin 声纹识别严格度。功能未启用的
    覆盖项不报错，转写结果的 result.warnings 列出被忽略项。
    """
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    # 数值覆盖项范围校验（越界 → 400）；布尔与降级开关无需范围校验
    try:
        options = {}
        if with_punc is not None:
            options["with_punc"] = with_punc
        if with_words is not None:
            options["with_words"] = with_words
        if diarize is not None:
            options["diarize"] = diarize
        if max_segment is not None:
            options["max_segment"] = coerce_num_in_range(
                max_segment, MAX_SEGMENT_RANGE, "max_segment", cast=int)
        if speaker_id_threshold is not None:
            options["speaker_id_threshold"] = coerce_num_in_range(
                speaker_id_threshold, SPK_ID_THRESHOLD_RANGE, "speaker_id_threshold")
        if speaker_id_margin is not None:
            options["speaker_id_margin"] = coerce_num_in_range(
                speaker_id_margin, SPK_ID_MARGIN_RANGE, "speaker_id_margin")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 1. 校验文件扩展名
    file_ext = os.path.splitext(file.filename or "audio.wav")[1].lower() or ".wav"
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的音频格式 '{file_ext}'，支持：{', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # 2. 流式保存上传文件，边写边检查大小
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    file_id = str(uuid.uuid4())
    save_path = os.path.join(UPLOADS_DIR, f"{file_id}{file_ext}")
    max_bytes = MAX_AUDIO_FILE_SIZE * 1024 * 1024

    total_size = 0
    try:
        with open(save_path, "wb") as f:
            while True:
                chunk = await file.read(UPLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大（>{MAX_AUDIO_FILE_SIZE}MB），最大支持 {MAX_AUDIO_FILE_SIZE}MB",
                    )
                f.write(chunk)
    except HTTPException:
        # 清理已写入的文件
        if os.path.exists(save_path):
            os.remove(save_path)
        raise

    # 3. 提交到任务队列
    try:
        task_id = _task_manager.submit(
            file_path=save_path,
            language=language,
            wav_name=file.filename,
            identify_speakers=identify_speakers,
            options=options,
        )
    except queue.Full:
        os.remove(save_path)
        raise HTTPException(status_code=503, detail="任务队列已满，请稍后重试")

    return ASRResponse(task_id=task_id)


async def list_tasks(
    status: str | None = None,
    history: bool = False,
    limit: int = 50,
) -> TaskListResponse:
    """获取任务列表，可通过 status 参数筛选（pending/processing/completed/failed/cancelled）。

    history=true 且任务持久化开启时，合并库内历史任务（task_id 去重内存优先，
    created_at 倒序，截断 limit 条）。
    """
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    tasks = _task_manager.list_tasks(status=status)
    if history and _task_store is not None:
        seen = {t["task_id"] for t in tasks}
        rows = await asyncio.to_thread(_task_store.list_history, limit, status)
        tasks.extend(r for r in rows if r["task_id"] not in seen)
        tasks.sort(key=lambda t: t.get("created_at") or "", reverse=True)
        tasks = tasks[:limit]
    return TaskListResponse(total=len(tasks), tasks=tasks)


async def get_task_detail(task_id: str) -> TaskStatusResponse:
    """查询单个任务详情（含识别结果）。内存未命中时查持久化库（历史任务兜底）。"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    task = _task_manager.get_task(task_id)
    if not task and _task_store is not None:
        task = await asyncio.to_thread(_task_store.get_task, task_id)
    if not task:
        return TaskStatusResponse(
            task_id=task_id,
            status="not_found",
            progress=0.0,
        )

    return TaskStatusResponse(
        task_id=task["task_id"],
        status=task["status"],
        progress=task["progress"],
        result=task.get("result"),
        error=task.get("error"),
        wav_name=task.get("wav_name"),
        created_at=task.get("created_at"),
        finished_at=task.get("finished_at"),
    )


async def get_task_status(task_id: str) -> TaskStatusResponse:
    """查询任务状态（已过时，请使用 GET /tasks/{task_id}）"""
    return await get_task_detail(task_id)


async def cancel_asr(task_id: str) -> CancelResponse:
    """取消 ASR 任务；对仅存在于持久化库的历史（终态）任务 = 删除记录"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    previous_status = _task_manager.cancel_task(task_id)

    if previous_status is None:
        # 内存不存在：若持久化库有该历史记录则删除（对终态任务"取消"即"删除"）
        if _task_store is not None and await asyncio.to_thread(_task_store.delete_task, task_id):
            return CancelResponse(
                task_id=task_id,
                status="deleted",
                message="历史任务记录已删除",
            )
        return CancelResponse(
            task_id=task_id,
            status="not_found",
            message="任务不存在",
        )

    if previous_status == "pending":
        return CancelResponse(
            task_id=task_id,
            status="cancelled",
            message="任务已取消",
        )

    if previous_status == "processing":
        return CancelResponse(
            task_id=task_id,
            status="cancelled",
            message="已发送取消请求，任务将在当前 chunk 处理完成后停止",
        )

    return CancelResponse(
        task_id=task_id,
        status=f"already_{previous_status}",
        message=f"任务已处于 {previous_status} 状态，无法取消",
    )


def build_offline_router(prefix: str, *, include_deprecated: bool = False) -> APIRouter:
    """离线批处理路由工厂；v1 与 v2 共用同一组控制器函数（零逻辑重复）。

    参数:
        prefix: 路由前缀，如 "/v1" 或 "/v2"
        include_deprecated: 是否注册已过时的 GET /asr/{task_id}（仅 v1 保留以兼容旧客户端）
    """
    r = APIRouter(prefix=prefix)
    dep = [Depends(verify_api_key)]
    r.add_api_route("/asr", submit_asr, methods=["POST"],
                    response_model=ASRResponse, dependencies=dep)
    r.add_api_route("/tasks", list_tasks, methods=["GET"],
                    response_model=TaskListResponse, dependencies=dep)
    r.add_api_route("/tasks/{task_id}", get_task_detail, methods=["GET"],
                    response_model=TaskStatusResponse, dependencies=dep)
    r.add_api_route("/tasks/{task_id}", cancel_asr, methods=["DELETE"],
                    response_model=CancelResponse, dependencies=dep)
    if include_deprecated:
        r.add_api_route("/asr/{task_id}", get_task_status, methods=["GET"],
                        response_model=TaskStatusResponse, dependencies=dep, deprecated=True)
    return r
