"""app/web/views.py 路由冒烟测试（离线页 + 实时测试页，Vue3 + Naive UI 无构建版）。

web_router 仅在 --web 时挂载；此处直接挂到 TestClient 验证返回与关键标记。
页面骨架在 HTML，业务模板/逻辑在 /web-ui/assets 下的 JS（见 test_web_assets.py）。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.views import web_router


def _client():
    app = FastAPI()
    app.include_router(web_router)
    return TestClient(app)


def test_web_ui_offline_page():
    resp = _client().get("/web-ui")
    assert resp.status_code == 200
    html = resp.text
    assert "<!DOCTYPE html>" in html
    assert "/web-ui/stream" in html                       # 导航指向实时页
    assert "/web-ui/docs" in html                         # 导航指向文档中心
    assert 'id="app"' in html                             # Vue 挂载点
    # vendor 与页面脚本引用
    assert "vue-3.5.35.global.prod.js" in html
    assert "naive-ui-2.44.1.prod.js" in html
    assert "/web-ui/assets/app.css" in html
    assert "/web-ui/assets/common.js" in html
    assert "/web-ui/assets/offline.js" in html


def test_web_ui_stream_page():
    resp = _client().get("/web-ui/stream")
    assert resp.status_code == 200
    html = resp.text
    assert "<!DOCTYPE html>" in html
    assert 'href="/web-ui"' in html                       # 导航返回离线页
    assert 'id="app"' in html
    assert "vue-3.5.35.global.prod.js" in html
    assert "naive-ui-2.44.1.prod.js" in html
    assert "/web-ui/assets/app.css" in html
    assert "/web-ui/assets/common.js" in html
    assert "/web-ui/assets/stream.js" in html


def test_stream_page_loaded_from_disk():
    # page.py 应已成功读入 stream.html（非空）
    from app.web.page import STREAM_PAGE
    assert STREAM_PAGE and len(STREAM_PAGE) > 500
