"""实时端点集成测试：真实 VadOfflineBackend 多会话准入、断连清理、错误信封、未注入后端。

模型层全程 mock（vad._model / asr）；不加载真实权重、不触网。
"""
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient

from app.api import ws_routes


def _client(backend):
    ws_routes.init_ws_stream(backend)
    app = FastAPI()
    app.include_router(ws_routes.ws_router_stream)
    return TestClient(app)


# ─── 真实 backend 并发准入 ───

def test_real_backend_concurrent_limit_1013():
    from app.engines.vad_engine import VADEngine
    from app.runtime.stream_session import VadOfflineBackend

    vad = VADEngine()
    vad._model = MagicMock()
    asr = MagicMock()
    asr.align_enabled = False
    backend = VadOfflineBackend(asr, vad, None, max_sessions=1, asr_concurrency=1)
    client = _client(backend)
    try:
        with client.websocket_connect("/v2/asr/stream") as ws1:
            assert ws1.receive_json()["type"] == "session.created"
            # 已达上限，第二个连接被 1013 拒
            with pytest.raises(WebSocketDisconnect) as exc:
                with client.websocket_connect("/v2/asr/stream") as ws2:
                    ws2.receive_json()
            assert exc.value.code == 1013
    finally:
        backend.shutdown()


def test_real_backend_releases_after_disconnect():
    """断连后会话计数释放，后续仍可接入。"""
    from app.engines.vad_engine import VADEngine
    from app.runtime.stream_session import VadOfflineBackend

    vad = VADEngine()
    vad._model = MagicMock()
    asr = MagicMock()
    asr.align_enabled = False
    backend = VadOfflineBackend(asr, vad, None, max_sessions=1, asr_concurrency=1)
    client = _client(backend)
    try:
        with client.websocket_connect("/v2/asr/stream") as ws:
            ws.receive_json()           # 占用唯一名额，断开后应释放
        # 再次连接应成功（名额已释放）
        with client.websocket_connect("/v2/asr/stream") as ws2:
            assert ws2.receive_json()["type"] == "session.created"
    finally:
        backend.shutdown()


# ─── 错误信封 / 未注入后端 ───

class _ErrorSession:
    def configure(self, msg):
        pass

    async def feed_audio(self, data):
        raise RuntimeError("boom")
        yield  # noqa: 使其成为 async generator

    async def flush(self):
        return
        yield


class _ErrorBackend:
    mode = "standard"
    backend = "vad-offline"
    capabilities = {"partial_results": False, "word_timestamps": False, "languages_auto": True}

    def __init__(self):
        self.released = False

    async def acquire(self):
        return True

    def create_session(self, sid):
        return _ErrorSession()

    def release(self, session):
        self.released = True


def test_feed_error_emits_error_envelope_without_disconnect():
    backend = _ErrorBackend()
    client = _client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        ws.receive_json()                       # session.created
        ws.send_json({"type": "start"})
        ws.send_bytes(b"\x00\x00\x00\x00")
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "feed_failed"
        # 非致命，连接仍在 → stop 正常收尾
        ws.send_json({"type": "stop"})
        closed = ws.receive_json()
        assert closed["type"] == "session.closed"
    assert backend.released is True


def test_backend_not_initialized_closes_1011():
    ws_routes.init_ws_stream(None)
    app = FastAPI()
    app.include_router(ws_routes.ws_router_stream)
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v2/asr/stream") as ws:
            ws.receive_json()
    assert exc.value.code == 1011
