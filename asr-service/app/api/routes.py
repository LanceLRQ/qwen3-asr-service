import os
import uuid
import hmac
import logging
import queue
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.api.schemas import ASRResponse, TaskStatusResponse, TaskListResponse, CancelResponse, HealthResponse
from app.config import UPLOADS_DIR, MAX_AUDIO_FILE_SIZE
import app.config as cfg

logger = logging.getLogger(__name__)

_bearer_scheme = HTTPBearer(auto_error=False)


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
):
    """配置了 API_KEY 时，要求请求携带有效的 Bearer token"""
    if not cfg.API_KEY:
        return
    if credentials is None or not hmac.compare_digest(credentials.credentials, cfg.API_KEY):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key",
        )


router = APIRouter(prefix="/v1")

# 支持的音频文件扩展名
ALLOWED_EXTENSIONS = {".wav", ".mp3", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".amr", ".opus"}

# 流式写入磁盘的分块大小
UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1MB

# 运行时依赖，由 main.py 启动时注入
_task_manager = None
_service_info = None


def init_routes(task_manager, service_info: dict):
    """注入运行时依赖"""
    global _task_manager, _service_info
    _task_manager = task_manager
    _service_info = service_info


@router.post("/asr", response_model=ASRResponse, dependencies=[Depends(verify_api_key)])
async def submit_asr(
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    """提交 ASR 任务"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

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
        )
    except queue.Full:
        os.remove(save_path)
        raise HTTPException(status_code=503, detail="任务队列已满，请稍后重试")

    return ASRResponse(task_id=task_id)


@router.get("/asr", response_model=TaskListResponse, dependencies=[Depends(verify_api_key)])
async def list_tasks(status: str | None = None):
    """获取任务列表，可通过 status 参数筛选（pending/processing/completed/failed/cancelled）"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    tasks = _task_manager.list_tasks(status=status)
    return TaskListResponse(total=len(tasks), tasks=tasks)


@router.get("/asr/{task_id}", response_model=TaskStatusResponse, dependencies=[Depends(verify_api_key)])
async def get_task_status(task_id: str):
    """查询任务状态"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    task = _task_manager.get_task(task_id)
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
    )


@router.delete("/asr/{task_id}", response_model=CancelResponse, dependencies=[Depends(verify_api_key)])
async def cancel_asr(task_id: str):
    """取消 ASR 任务"""
    if _task_manager is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")

    previous_status = _task_manager.cancel_task(task_id)

    if previous_status is None:
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


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查，返回当前运行模式和加载的模型信息"""
    if _service_info is None:
        raise HTTPException(status_code=503, detail="服务尚未就绪，请稍后重试")
    return HealthResponse(**_service_info)
