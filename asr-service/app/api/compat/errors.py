"""兼容层错误信封：各上游风格独立，按异常类型分派，不污染 v2 的 {"detail":...}。

v2 的 HTTPException 仍由 FastAPI 默认 handler 处理；这里只接管兼容层自有异常，
按异常**类型**分派（非按路径），对 /v1、/v2 零回归。
"""
import uuid

from fastapi import HTTPException
from fastapi.responses import JSONResponse


class OpenAICompatError(HTTPException):
    """OpenAI 风格错误：响应体 {"error":{message,type,param,code}}。"""

    def __init__(self, status_code: int, message: str, *,
                 err_type: str = "invalid_request_error",
                 param: str | None = None, code: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.err_type = err_type
        self.param = param
        self.code = code


class DashScopeCompatError(HTTPException):
    """DashScope 风格错误：响应体 {"code","message","request_id"}。"""

    def __init__(self, status_code: int, message: str, *,
                 code: str, request_id: str | None = None):
        super().__init__(status_code=status_code, detail=message)
        self.code = code
        self.request_id = request_id or uuid.uuid4().hex


def register_compat_exception_handlers(app) -> None:
    """注册兼容层异常 handler（按类型分派，不触碰 v2 默认 handler）。"""

    @app.exception_handler(OpenAICompatError)
    async def _handle_openai(_request, exc: OpenAICompatError):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {
                "message": exc.detail,
                "type": exc.err_type,
                "param": exc.param,
                "code": exc.code,
            }},
        )

    @app.exception_handler(DashScopeCompatError)
    async def _handle_dashscope(_request, exc: DashScopeCompatError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.detail,
                "request_id": exc.request_id,
            },
        )
