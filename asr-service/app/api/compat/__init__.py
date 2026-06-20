"""OpenAI / DashScope 兼容接口包（/compat/* 命名空间，与 v1/v2 隔离）。

设计与契约见 docs/plan/features/20260609_compat_api/。Phase 1 仅 OpenAI 离线；
DashScope 离线（Phase 2）、实时（Phase 3）后续并入。
"""
from app.api.compat.dashscope_routes import init_dashscope_routes
from app.api.compat.openai_routes import init_openai_routes
from app.api.compat.ws_bridge import init_compat_ws


def init_compat(*, task_manager, task_store=None, backend=None, service_info=None,
                recording_manager=None):
    """由 main.py 装配末尾注入运行时依赖。

    task_manager：离线兼容入队/同步等待/轮询；service_info：GET /models 读模型信息；
    backend：实时兼容活动后端（None=未启用实时，WS 端点不挂）。仅注入引用，与开关无关
    （按开关决定挂哪些路由）。
    """
    init_openai_routes(task_manager=task_manager, service_info=service_info)
    init_dashscope_routes(task_manager=task_manager)
    init_compat_ws(backend, recording_manager=recording_manager)
