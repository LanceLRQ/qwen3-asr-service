"""流式录音下载 / 删除接口。"""
import hmac

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import app.config as cfg
from app.api.schemas import StreamRecordingDeleteResponse

_bearer_scheme = HTTPBearer(auto_error=False)
_recording_manager = None


def init_stream_recording_routes(manager) -> None:
    """注入 StreamRecordingManager。"""
    global _recording_manager
    _recording_manager = manager


async def require_recording_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    if not cfg.API_KEY:
        raise HTTPException(status_code=503, detail="录音下载/删除要求服务端配置 api_key")
    if credentials is None or not hmac.compare_digest(credentials.credentials, cfg.API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _manager_or_503():
    if _recording_manager is None:
        raise HTTPException(status_code=503, detail="流式录音服务尚未就绪")
    return _recording_manager


async def download_recording(
    recording_id: str,
    _auth: None = Depends(require_recording_auth),
):
    manager = _manager_or_503()
    path = manager.path_for(recording_id)
    if path is None:
        raise HTTPException(status_code=404, detail="录音文件不存在")
    return FileResponse(path, media_type="audio/wav", filename=manager.filename_for(recording_id))


async def delete_recording(
    recording_id: str,
    _auth: None = Depends(require_recording_auth),
) -> StreamRecordingDeleteResponse:
    manager = _manager_or_503()
    deleted = manager.delete(recording_id)
    return StreamRecordingDeleteResponse(
        recording_id=recording_id,
        status="deleted" if deleted else "not_found",
        deleted=deleted,
    )


def build_stream_recordings_router(prefix: str = "/v2") -> APIRouter:
    r = APIRouter(prefix=prefix)
    r.add_api_route("/stream-recordings/{recording_id}", download_recording, methods=["GET"])
    r.add_api_route(
        "/stream-recordings/{recording_id}",
        delete_recording,
        methods=["DELETE"],
        response_model=StreamRecordingDeleteResponse,
    )
    return r
