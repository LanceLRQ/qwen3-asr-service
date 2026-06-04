"""共享测试夹具。

约束：测试不修改项目源代码，只通过 mock / monkeypatch / 依赖注入(init_routes) 验证。
所有重模型/网络调用均 mock，不加载真实模型、不触网、无长等待。
"""
import time

import numpy as np
import pytest


def wait_for(cond, timeout=5.0, interval=0.02) -> bool:
    """轮询等待条件成立，超时返回 False（用于异步工作线程断言）。"""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(interval)
    return cond()


@pytest.fixture
def make_wav(tmp_path):
    """生成一个静音 16k 单声道 WAV，返回路径。用于需要真实音频文件的测试。"""
    import soundfile as sf

    def _make(duration_sec=2.0, sr=16000, name="audio.wav"):
        path = tmp_path / name
        samples = np.zeros(int(duration_sec * sr), dtype="float32")
        sf.write(str(path), samples, sr)
        return str(path)

    return _make


@pytest.fixture
def tm_factory():
    """创建 TaskManager 的工厂，测试结束统一 shutdown，避免线程/线程池泄漏。"""
    from app.runtime.task_manager import TaskManager

    created = []

    def _make(max_queue_size=100, start=False, processor=None, store=None):
        tm = TaskManager(max_queue_size=max_queue_size, store=store)
        if processor is not None:
            tm.set_processor(processor)
        if start:
            tm.start()
        created.append(tm)
        return tm

    yield _make

    for tm in created:
        try:
            tm.shutdown()
        except Exception:
            pass


@pytest.fixture
def make_client(tmp_path, monkeypatch):
    """构建注入了依赖的 FastAPI TestClient，按 main.py 的方式装配 v1/v2 工厂路由。

    - 离线路由：init_routes(task_manager) + build_offline_router("/v1", deprecated)/("/v2")
    - 共性路由：init_common(service_info) + build_common_router("/v1")/("/v2")
    不启动真实服务/模型；上传目录重定向到临时目录，避免污染真实缓存路径。
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import routes, common_routes

    monkeypatch.setattr(routes, "UPLOADS_DIR", str(tmp_path / "uploads"))

    def _make(task_manager=None, service_info=None, task_store=None,
              include_offline=True, include_common=True):
        app = FastAPI()
        if include_common:
            common_routes.init_common(service_info)
            app.include_router(common_routes.build_common_router("/v1"))
            app.include_router(common_routes.build_common_router("/v2"))
        if include_offline:
            routes.init_routes(task_manager, task_store)
            app.include_router(routes.build_offline_router("/v1", include_deprecated=True))
            app.include_router(routes.build_offline_router("/v2"))
        return TestClient(app)

    return _make
