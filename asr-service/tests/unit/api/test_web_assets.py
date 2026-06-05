"""/web-ui/assets 静态资源测试（vendored 前端库 + 页面 JS + GZip）。

assets 由 main.py 在 --web 分支经 app.mount 挂载（app 层操作，web_router 单测拿不到），
此处按 main 的装配方式手动构造 app 验证。
"""
import os

import pytest
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.web.views import web_router, ASSETS_DIR

VENDOR_FILES = ["vue-3.5.35.global.prod.js", "naive-ui-2.44.1.prod.js"]
PAGE_SCRIPTS = ["common.js", "offline.js", "stream.js", "pcm-worklet.js"]


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.mount("/web-ui/assets", StaticFiles(directory=ASSETS_DIR), name="web-assets")
    app.include_router(web_router)
    return TestClient(app)


def test_vendor_files_exist_on_disk():
    # 防 clone 后 vendored 库缺失（这两个文件必须提交进 git）
    for name in VENDOR_FILES:
        path = os.path.join(ASSETS_DIR, "vendor", name)
        assert os.path.isfile(path), f"vendored 文件缺失: {name}"
        assert os.path.getsize(path) > 100_000, f"vendored 文件疑似损坏（过小）: {name}"


@pytest.mark.parametrize("name", VENDOR_FILES)
def test_vendor_served(client, name):
    resp = client.get(f"/web-ui/assets/vendor/{name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


@pytest.mark.parametrize("name", PAGE_SCRIPTS)
def test_page_scripts_served(client, name):
    resp = client.get(f"/web-ui/assets/{name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_app_css_served(client):
    resp = client.get("/web-ui/assets/app.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


def test_gzip_applied_to_large_asset(client):
    resp = client.get(
        "/web-ui/assets/vendor/naive-ui-2.44.1.prod.js",
        headers={"accept-encoding": "gzip"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


def test_unknown_asset_404(client):
    assert client.get("/web-ui/assets/vendor/nope.js").status_code == 404


def test_worklet_contains_processor_registration():
    # 实时页 audioWorklet.addModule 依赖该文件注册 'pcm-worklet' 处理器
    with open(os.path.join(ASSETS_DIR, "pcm-worklet.js"), encoding="utf-8") as f:
        src = f.read()
    assert "registerProcessor('pcm-worklet'" in src


def test_offline_js_uses_v2_endpoints():
    # 离线页已从 /v1 切换到 /v2（含历史任务查询）
    with open(os.path.join(ASSETS_DIR, "offline.js"), encoding="utf-8") as f:
        src = f.read()
    assert "/v2/asr" in src
    assert "/v2/tasks" in src
    assert "history=true" in src
    assert "/v1/" not in src


def test_stream_js_protocol_markers():
    # 实时页协议关键标记：WS 端点、capabilities 探测、外置 worklet 加载
    with open(os.path.join(ASSETS_DIR, "stream.js"), encoding="utf-8") as f:
        src = f.read()
    assert "/v2/asr/stream" in src
    assert "/v2/capabilities" in src
    assert "/web-ui/assets/pcm-worklet.js" in src
