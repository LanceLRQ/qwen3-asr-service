from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.web import docs_site
from app.web.page import HTML_PAGE, STREAM_PAGE

web_router = APIRouter()


@web_router.get("/web-ui", response_class=HTMLResponse)
async def web_ui():
    """返回 Web UI 单页应用（离线转写）"""
    return HTML_PAGE


@web_router.get("/web-ui/stream", response_class=HTMLResponse)
async def web_ui_stream():
    """返回实时语音转写测试页（麦克风 / 文件模拟推流）"""
    return STREAM_PAGE


@web_router.get("/web-ui/docs", response_class=HTMLResponse)
async def web_ui_docs_index():
    """文档中心首页（渲染 README，自带文档导航表）"""
    page = docs_site.render_doc_page("readme")
    if page is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return page


@web_router.get("/web-ui/docs/{slug:path}", response_class=HTMLResponse)
async def web_ui_docs_page(slug: str):
    """渲染指定文档（slug 仅在白名单注册表内查找）"""
    page = docs_site.render_doc_page(slug)
    if page is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return page
