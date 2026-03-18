import os
import uuid
import logging
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from app.api.schemas import ASRResponse, TaskStatusResponse, HealthResponse
from app.config import UPLOADS_DIR, MAX_AUDIO_FILE_SIZE

logger = logging.getLogger(__name__)
router = APIRouter()

# 运行时依赖，由 main.py 启动时注入
_task_manager = None
_service_info = None


def init_routes(task_manager, service_info: dict):
    """注入运行时依赖"""
    global _task_manager, _service_info
    _task_manager = task_manager
    _service_info = service_info


@router.post("/asr", response_model=ASRResponse)
async def submit_asr(
    file: UploadFile = File(...),
    language: str | None = Form(None),
):
    """提交 ASR 任务"""
    # 1. 保存上传文件
    os.makedirs(UPLOADS_DIR, exist_ok=True)

    file_id = str(uuid.uuid4())
    file_ext = os.path.splitext(file.filename or "audio.wav")[1] or ".wav"
    save_path = os.path.join(UPLOADS_DIR, f"{file_id}{file_ext}")

    content = await file.read()

    # 2. 文件大小检查
    file_size_mb = len(content) / (1024 * 1024)
    if file_size_mb > MAX_AUDIO_FILE_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大（{file_size_mb:.0f}MB），最大支持 {MAX_AUDIO_FILE_SIZE}MB",
        )

    with open(save_path, "wb") as f:
        f.write(content)

    # 3. 提交到任务队列
    task_id = _task_manager.submit(
        file_path=save_path,
        language=language,
    )

    return ASRResponse(task_id=task_id)


@router.get("/asr/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """查询任务状态"""
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


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """健康检查，返回当前运行模式和加载的模型信息"""
    return HealthResponse(**_service_info)
