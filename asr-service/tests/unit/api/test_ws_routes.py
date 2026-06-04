"""app/api/ws_routes.py 测试（TestClient websocket + 注入 fake backend）。

验证 session.created 握手、start→PCM→stop 流程、session.closed 收尾、release 释放，
以及鉴权(1008)与并发超额(1013)、错误信封（invalid_config/frame_too_large/
session_timeout/feed_failed/backlog_overflow）。
"""
import asyncio

import pytest
from fastapi import FastAPI, WebSocketDisconnect
from fastapi.testclient import TestClient


class FakeSession:
    def __init__(self):
        self.configured = None

    def configure(self, msg):
        self.configured = msg

    async def feed_audio(self, data):
        yield {"type": "final", "seg_id": 0, "text": "hello", "start": 0, "end": 100}

    async def flush(self):
        yield {"type": "final", "seg_id": 1, "text": "bye", "start": 100, "end": 200}


class FakeBackend:
    mode = "standard"
    backend = "vad-offline"
    capabilities = {"partial_results": False, "word_timestamps": True, "languages_auto": True}

    def __init__(self, allow=True):
        self._allow = allow
        self.released = False

    async def acquire(self):
        return self._allow

    def create_session(self, sid):
        return FakeSession()

    def release(self, session):
        self.released = True


def _make_client(backend):
    from app.api import ws_routes
    ws_routes.init_ws_stream(backend)
    app = FastAPI()
    app.include_router(ws_routes.ws_router_stream)
    return TestClient(app)


def test_session_created_and_full_flow():
    backend = FakeBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["mode"] == "standard"
        assert created["backend"] == "vad-offline"
        assert created["capabilities"]["word_timestamps"] is True
        assert created["protocol"] == "qwen3-asr-stream"

        ws.send_json({"type": "start", "audio_fs": 16000})
        ws.send_bytes(b"\x00\x00\x00\x00")
        final1 = ws.receive_json()
        assert final1["type"] == "final" and final1["text"] == "hello"

        ws.send_json({"type": "stop"})
        final2 = ws.receive_json()
        assert final2["type"] == "final" and final2["seg_id"] == 1
        closed = ws.receive_json()
        assert closed["type"] == "session.closed"

    assert backend.released is True


def test_auth_rejected_without_token(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "secret")
    client = _make_client(FakeBackend())
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v2/asr/stream") as ws:
            ws.receive_json()
    assert exc.value.code == 1008


def test_auth_accepts_valid_token(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "secret")
    client = _make_client(FakeBackend())
    with client.websocket_connect("/v2/asr/stream?token=secret") as ws:
        assert ws.receive_json()["type"] == "session.created"


def test_over_limit_rejected(monkeypatch):
    monkeypatch.setattr("app.config.API_KEY", "")
    client = _make_client(FakeBackend(allow=False))
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v2/asr/stream") as ws:
            ws.receive_json()
    assert exc.value.code == 1013


def test_invalid_config_returns_error_and_releases():
    class BadConfigSession(FakeSession):
        def configure(self, msg):
            raise ValueError("audio_fs 必须在 [8000, 96000] 范围内，收到 0")

    class BadConfigBackend(FakeBackend):
        def create_session(self, sid):
            return BadConfigSession()

    backend = BadConfigBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        assert ws.receive_json()["type"] == "session.created"
        ws.send_json({"type": "start", "audio_fs": 0})
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "invalid_config"
        assert err["fatal"] is True
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True


def test_frame_too_large_rejected_session_continues(monkeypatch):
    monkeypatch.setattr("app.config.STREAM_MAX_FRAME_BYTES", 8)
    backend = FakeBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_bytes(b"\x00" * 16)                  # 超限 → 拒帧不断连
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "frame_too_large"
        ws.send_bytes(b"\x00\x00")                   # 正常帧仍可处理
        assert ws.receive_json()["type"] == "final"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["seg_id"] == 1
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True


def test_session_timeout_sends_error_and_releases(monkeypatch):
    monkeypatch.setattr("app.config.STREAM_MAX_SESSION_SECONDS", 0)
    backend = FakeBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        assert ws.receive_json()["type"] == "session.created"
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "session_timeout"
        assert err["fatal"] is True
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True


def test_release_called_when_create_session_fails():
    # acquire 成功后任何异常路径（如 create_session 抛错）都必须释放计数
    class BoomBackend(FakeBackend):
        def create_session(self, sid):
            raise RuntimeError("boom")

    backend = BoomBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        assert ws.receive_json()["type"] == "session.created"
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "internal"
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True


def test_feed_error_sends_feed_failed_session_continues():
    # 处理异常 → feed_failed 信封（非致命），会话可继续直至 stop
    class FailingFeedSession(FakeSession):
        async def feed_audio(self, data):
            raise RuntimeError("boom")
            yield  # pragma: no cover  # 使其成为异步生成器

    class FailingFeedBackend(FakeBackend):
        def create_session(self, sid):
            return FailingFeedSession()

    backend = FailingFeedBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_bytes(b"\x00\x00")
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "feed_failed"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "final"          # flush 仍工作
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True


def test_backlog_overflow_closes_session(monkeypatch):
    # 消费侧阻塞时积压超限 → backlog_overflow 致命错误 + 收尾
    monkeypatch.setattr("app.config.STREAM_MAX_BACKLOG_BYTES", 4)

    class SlowSession(FakeSession):
        async def feed_audio(self, data):
            await asyncio.sleep(30)                          # 模拟推理争锁阻塞
            yield {"type": "final", "seg_id": 0, "text": "x", "start": 0, "end": 1}

    class SlowBackend(FakeBackend):
        def create_session(self, sid):
            return SlowSession()

    backend = SlowBackend()
    client = _make_client(backend)
    with client.websocket_connect("/v2/asr/stream") as ws:
        ws.receive_json()
        ws.send_json({"type": "start"})
        ws.send_bytes(b"\x00\x00")                           # backlog=2，消费侧挂起
        ws.send_bytes(b"\x00\x00\x00")                       # 2+3 > 4 → 溢出
        err = ws.receive_json()
        assert err["type"] == "error" and err["code"] == "backlog_overflow"
        assert err["fatal"] is True
        assert ws.receive_json()["type"] == "session.closed"
    assert backend.released is True
