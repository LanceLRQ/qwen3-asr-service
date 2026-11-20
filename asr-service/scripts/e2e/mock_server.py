"""E2E mock 服务：mock TaskManager + FakeBackend，挂全部 /compat/* 路由（无真模型）。

复用项目的 compat 实现代码，用固定转写结果验证「端点路由 + SDK 字段对齐 + WS 握手/
信封翻译 + SSE + 错误码 + DashScope 下载链路」——不验证转写质量（那需真模型，走真服务）。
依赖只需 web 运行期栈（fastapi/uvicorn/pydantic/httpx），不触 torch/funasr 等模型依赖。
"""
import argparse
import os
import sys
import tempfile
import uuid

# 允许 import 项目 app.*（run.sh 已设 PYTHONPATH，此处兜底：scripts/e2e → asr-service）
_ASR_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ASR_ROOT not in sys.path:
    sys.path.insert(0, _ASR_ROOT)

import app.config as cfg

cfg.API_KEY = os.environ.get("E2E_API_KEY", "")
cfg.COMPAT_FETCH_ALLOW_PRIVATE = True          # 允许下载 compat_e2e 起的回环 URL
cfg.COMPAT_EXTERNAL_BASE_URL = None
cfg.OPENAI_SYNC_TIMEOUT = 30
cfg.UPLOADS_DIR = tempfile.mkdtemp(prefix="e2e_uploads_")

from fastapi import FastAPI

from app.api.routes import build_offline_router, init_routes
from app.api.ws_routes import init_ws_stream, ws_router_stream
from app.api.compat import init_compat
from app.api.compat.dashscope_routes import build_dashscope_router
from app.api.compat.dashscope_ws_routes import build_dashscope_ws_router
from app.api.compat.errors import register_compat_exception_handlers
from app.api.compat.openai_routes import build_openai_router
from app.api.compat.openai_ws_routes import build_openai_ws_router

# 固定 mock 结果（离线 result / 实时 final），覆盖映射所需字段
RESULT = {
    "segments": [
        {"start": 0.0, "end": 1.5, "text": "你好世界",
         "words": [{"text": "你好", "start": 0.0, "end": 0.8},
                   {"text": "世界", "start": 0.8, "end": 1.5}]},
    ],
    "full_text": "你好世界", "language": "zh",
}
FINAL = {"type": "final", "seg_id": 0, "text": "你好世界", "start": 0, "end": 1500,
         "words": [{"text": "你好", "start": 0.0, "end": 0.8}]}


class MockTaskManager:
    """离线兼容用：submit 立即返回 id，wait_done/get_task 返回固定 completed 结果。"""

    def __init__(self):
        self.task_ids = set()

    def submit(self, **kwargs):
        task_id = "task-" + uuid.uuid4().hex[:8]
        self.task_ids.add(task_id)
        return task_id

    def wait_done(self, task_id, timeout):
        return {"status": "completed", "result": RESULT, "error": None}

    def get_task(self, task_id):
        if task_id in self.task_ids:
            return {"task_id": task_id, "status": "completed", "progress": 1.0, "result": RESULT}


class FakeSession:
    def configure(self, msg):
        return []

    async def feed_audio(self, pcm):
        return
        yield

    async def flush(self):
        yield FINAL


class FakeBackend:
    mode = "standard"
    backend = "vad-offline"
    capabilities = {"partial_results": False, "word_timestamps": True}
    _active = 0

    async def acquire(self):
        self._active += 1
        return True

    def create_session(self, sid):
        return FakeSession()

    def release(self, session):
        self._active = max(0, self._active - 1)


def build_app() -> FastAPI:
    manager = MockTaskManager()
    backend = FakeBackend()
    init_routes(manager)
    init_ws_stream(backend)
    init_compat(task_manager=manager,
                service_info={"model_size": "0.6b"}, backend=backend)
    app = FastAPI()
    app.include_router(build_offline_router("/v2"))
    app.include_router(ws_router_stream)
    register_compat_exception_handlers(app)
    app.include_router(build_openai_router())
    app.include_router(build_dashscope_router())
    app.include_router(build_openai_ws_router())
    app.include_router(build_dashscope_ws_router())
    return app


app = build_app()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E2E mock 服务（无真模型）")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
