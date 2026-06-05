"""声纹库管理/识别 API（仅挂 /v2，全部端点强制鉴权）。

体例对齐 routes.py：模块级 DI（init_speaker_routes）+ 纯函数控制器 +
build_speakers_router 工厂；上传保存复用 submit_asr 的流式写盘与校验常量。

错误约定：401 鉴权 ｜ 400 质量门槛/consent（ValueError 透传）｜ 404 不存在
（SpeakerNotFoundError，store 层 rowcount==0 即抛——无 get-then-mutate 竞态窗口）｜
503 speaker_db_disabled（模块未启用）/ model_tag_mismatch（仅禁登记/识别，
管理端点保留——被遗忘权不受失配影响）｜ 500 其余 SpeakerStoreError。
"""
import asyncio
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.routes import (
    ALLOWED_EXTENSIONS,
    UPLOAD_CHUNK_SIZE,
    verify_api_key,
)
from app.api.schemas import (
    EnrollResponse,
    IdentifyResponse,
    SpeakerDeleteResponse,
    SpeakerInfo,
    SpeakerListResponse,
    SpeakerUpdateRequest,
    TemplateDeleteResponse,
)
from app.config import MAX_AUDIO_FILE_SIZE, UPLOADS_DIR
from app.runtime.speaker_store import SpeakerNotFoundError, SpeakerStoreError

logger = logging.getLogger(__name__)

# 运行时依赖，由 main.py 启动时注入
_service = None
_tag_mismatch = False


def init_speaker_routes(service, tag_mismatch: bool = False):
    """注入运行时依赖。service=None 表示模块未启用/已降级（端点统一 503）。"""
    global _service, _tag_mismatch
    _service = service
    _tag_mismatch = tag_mismatch


def _require_service():
    if _service is None:
        raise HTTPException(status_code=503, detail="speaker_db_disabled")
    return _service


def _require_writable():
    """登记/识别路径：model_tag 失配时禁用（库内模板与当前引擎不可比）。"""
    service = _require_service()
    if _tag_mismatch:
        raise HTTPException(status_code=503, detail="model_tag_mismatch")
    return service


async def _save_upload(file: UploadFile) -> str:
    """流式保存上传文件（扩展名白名单 + 边写边校验大小，照抄 submit_asr）。"""
    file_ext = os.path.splitext(file.filename or "audio.wav")[1].lower() or ".wav"
    if file_ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的音频格式 '{file_ext}'，支持：{', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )
    os.makedirs(UPLOADS_DIR, exist_ok=True)
    save_path = os.path.join(UPLOADS_DIR, f"spkup_{uuid.uuid4().hex}{file_ext}")
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
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件过大（>{MAX_AUDIO_FILE_SIZE}MB），"
                               f"最大支持 {MAX_AUDIO_FILE_SIZE}MB",
                    )
                f.write(chunk)
    except HTTPException:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise
    return save_path


def _cleanup(paths: list[str]):
    for p in paths:
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError as e:
            logger.warning(f"上传临时文件清理失败: {e}")


async def _run(fn, *args):
    """同步 Service 调用下沉线程池 + 统一错误映射。"""
    try:
        return await asyncio.to_thread(fn, *args)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except SpeakerNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except SpeakerStoreError as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── 控制器（纯函数）───

async def enroll_speaker(
    name: str = Form(...),
    consent: bool = Form(False),
    note: str | None = Form(None),
    files: list[UploadFile] = File(...),
) -> EnrollResponse:
    """登记说话人（multipart：≥1 个单人音频样本）。"""
    service = _require_writable()
    if consent is not True:
        raise HTTPException(status_code=400,
                            detail="登记必须携带 consent=true（确认已获得数据主体同意）")
    paths = []
    try:
        for f in files:
            paths.append(await _save_upload(f))
        result = await _run(service.enroll, name, note, paths, True)
        return EnrollResponse(**result)
    finally:
        _cleanup(paths)


async def list_speakers() -> SpeakerListResponse:
    service = _require_service()
    rows = await _run(service.store.list_speakers)
    return SpeakerListResponse(total=len(rows), speakers=rows)


async def get_speaker(speaker_id: str) -> SpeakerInfo:
    service = _require_service()
    info = await _run(service.store.get_speaker, speaker_id)
    if info is None:
        raise HTTPException(status_code=404, detail="说话人不存在")
    return SpeakerInfo(**info)


async def update_speaker(speaker_id: str, body: SpeakerUpdateRequest) -> SpeakerInfo:
    """改名/备注（不影响 speaker_id 与模板；自动登记的占位名据此改真名）。

    不做前置存在性检查（get-then-mutate 有 TOCTOU 竞态）：store 层不存在即抛
    SpeakerNotFoundError → 404；body 全空时 store 无操作，由回读兜 404。
    """
    service = _require_service()
    await _run(service.store.update_speaker, speaker_id, body.name, body.note)
    info = await _run(service.store.get_speaker, speaker_id)
    if info is None:
        raise HTTPException(status_code=404, detail=f"说话人不存在: {speaker_id}")
    return SpeakerInfo(**info)


async def delete_speaker(speaker_id: str) -> SpeakerDeleteResponse:
    """硬删除（级联模板 + 物理回收 + 留存音频清理，不可恢复——被遗忘权）。"""
    service = _require_service()
    await _run(service.delete_speaker, speaker_id)
    return SpeakerDeleteResponse(speaker_id=speaker_id)


async def add_template(speaker_id: str, file: UploadFile = File(...)) -> dict:
    """追加声纹模板（质心自动重算）。"""
    service = _require_writable()
    if await _run(service.store.get_speaker, speaker_id) is None:
        raise HTTPException(status_code=404, detail="说话人不存在")
    path = await _save_upload(file)
    try:
        return await _run(service.add_template, speaker_id, path)
    finally:
        _cleanup([path])


async def delete_template(speaker_id: str, template_id: int) -> TemplateDeleteResponse:
    """删除单条模板（不存在由 store 抛 NotFound → 404，覆盖人/模板两种缺失）。"""
    service = _require_service()
    remaining = await _run(service.store.delete_template, speaker_id, template_id)
    hint = None
    if remaining == 0:
        hint = "该说话人已无模板，识别仍使用最后一次质心；请追加样本或删除该说话人"
    return TemplateDeleteResponse(speaker_id=speaker_id, template_id=template_id,
                                  remaining=remaining, hint=hint)


async def identify_speaker(file: UploadFile = File(...)) -> IdentifyResponse:
    """单文件 1:N 识别（开集：低于阈值/近邻打架 → matched=false）。"""
    service = _require_writable()
    path = await _save_upload(file)
    try:
        return IdentifyResponse(**await _run(service.identify_file, path))
    finally:
        _cleanup([path])


def build_speakers_router() -> APIRouter:
    """仅挂 /v2（声纹库为 v2 专属能力）；体例对齐 build_offline_router。"""
    r = APIRouter(prefix="/v2")
    dep = [Depends(verify_api_key)]
    r.add_api_route("/speakers/identify", identify_speaker, methods=["POST"],
                    response_model=IdentifyResponse, dependencies=dep)
    r.add_api_route("/speakers", enroll_speaker, methods=["POST"], status_code=201,
                    response_model=EnrollResponse, dependencies=dep)
    r.add_api_route("/speakers", list_speakers, methods=["GET"],
                    response_model=SpeakerListResponse, dependencies=dep)
    r.add_api_route("/speakers/{speaker_id}", get_speaker, methods=["GET"],
                    response_model=SpeakerInfo, dependencies=dep)
    r.add_api_route("/speakers/{speaker_id}", update_speaker, methods=["PATCH"],
                    response_model=SpeakerInfo, dependencies=dep)
    r.add_api_route("/speakers/{speaker_id}", delete_speaker, methods=["DELETE"],
                    response_model=SpeakerDeleteResponse, dependencies=dep)
    r.add_api_route("/speakers/{speaker_id}/templates", add_template, methods=["POST"],
                    status_code=201, dependencies=dep)
    r.add_api_route("/speakers/{speaker_id}/templates/{template_id}", delete_template,
                    methods=["DELETE"], response_model=TemplateDeleteResponse,
                    dependencies=dep)
    return r
