from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.web.page import HTML_PAGE

web_router = APIRouter()


@web_router.get("/web-ui", response_class=HTMLResponse)
async def web_ui():
    """返回 Web UI 单页应用"""
    return HTML_PAGE
