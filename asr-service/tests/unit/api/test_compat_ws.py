"""实时兼容 WS 集成测试（TestClient.websocket_connect + FakeBackend）。

不加载模型；FakeSession 在 flush 时产可控 final。覆盖：OpenAI session.created→update→
append(base64)→commit→completed；DashScope run-task→帧→finish-task→result-generated→
task-finished + 连接复用；鉴权 1008、容量 1013、invalid_config。
"""
import base64

import pytest

import app.config as cfg
from app.api.compat import ws_bridge
from app.api.compat.dashscope_ws_routes import build_dashscope_ws_router
from app.api.compat.openai_ws_routes import build_openai_ws_router

OPENAI_WS = "/compat/openai/v1/realtime"
DASHSCOPE_WS = "/compat/dashscope/api-ws/v1/inference"

FINAL = {"type": "final", "seg_id": 0, "text": "你好世界", "start": 0, "end": 1000,
         "words": [{"text": "你好", "start": 0.0, "end": 0.5},
                   {"text": "世界", "start": 0.5, "end": 1.0}]}


class FakeSession:
    def __init__(self, flush_finals=None):
        self.configured = []
        self._flush_finals = flush_finals if flush_finals is not None else [FINAL]

    def configure(self, msg):
        self.configured.append(msg)
        fs = msg.get("audio_fs")
        if fs is not None and fs < 0:
            raise ValueError(f"audio_fs 非法: {fs}")
        return []

    async def feed_audio(self, pcm):
        return
        yield   # 使其成为 async generator（feed 不产 final）

    async def flush(self):
        for f in self._flush_finals:
            yield f


class FakePartialSession(FakeSession):
    """模拟 vLLM 路线 A：feed_audio 产 2 个累计 partial，flush 产整句 final（R2 用）。"""

    async def feed_audio(self, pcm):
        yield {"type": "partial", "seg_id": 0, "text": "你好"}
        yield {"type": "partial", "seg_id": 0, "text": "你好世界"}


class FakeBackend:
    mode = "standard"
    backend = "vad-offline"
    capabilities = {"partial_results": False, "word_timestamps": True}

    def __init__(self, session_factory=None, capacity=10):
        self._factory = session_factory or (lambda: FakeSession())
        self._capacity = capacity
        self._active = 0
        self.released = 0

    async def acquire(self):
        if self._active >= self._capacity:
            return False
        self._active += 1
        return True

    def create_session(self, sid):
        return self._factory()

    def release(self, session):
        self._active = max(0, self._active - 1)
        self.released += 1


@pytest.fixture
def ws_app(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def _make(backend, api_key=""):
        monkeypatch.setattr(cfg, "API_KEY", api_key)
        ws_bridge.init_compat_ws(backend)
        app = FastAPI()
        app.include_router(build_openai_ws_router())
        app.include_router(build_dashscope_ws_router())
        return TestClient(app)

    return _make


# ─── OpenAI Realtime ───

def test_openai_full_flow(ws_app):
    backend = FakeBackend()
    client = ws_app(backend)
    with client.websocket_connect(OPENAI_WS) as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["session"]["object"] == "realtime.transcription_session"

        ws.send_json({"type": "session.update", "session": {"audio": {"input": {
            "format": {"rate": 16000}, "transcription": {"language": "zh"}}}}})
        updated = ws.receive_json()
        assert updated["type"] == "session.updated"
        # 回显服务端采用的采样率（客户端据此可检测 mismatch）
        assert updated["session"]["audio"]["input"]["format"]["rate"] == 16000

        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(b"\x00\x00" * 100).decode()})
        ws.send_json({"type": "input_audio_buffer.commit"})

        ev = ws.receive_json()
        assert ev["type"] == "conversation.item.input_audio_transcription.completed"
        assert ev["transcript"] == "你好世界"
        assert ev["item_id"] == "item_0"
        assert "delta" not in ev["type"]   # Stage A 不发逐字增量
    assert backend.released == 1


def test_openai_session_update_no_rate_echoes_default(ws_app):
    client = ws_app(FakeBackend())
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()   # session.created
        ws.send_json({"type": "session.update", "session": {}})   # 未声明 rate
        updated = ws.receive_json()
        # OpenAI pcm16 惯例默认 24000，回显让客户端可见服务端假设
        assert updated["session"]["audio"]["input"]["format"]["rate"] == 24000


def test_openai_invalid_config(ws_app):
    client = ws_app(FakeBackend())
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()   # session.created
        # rate=-1 → cfg audio_fs=-1 → configure 抛 ValueError → error
        ws.send_json({"type": "session.update", "session": {"audio": {"input": {
            "format": {"rate": -1}}}}})
        ev = ws.receive_json()
        assert ev["type"] == "error"
        assert ev["error"]["code"] == "invalid_config"


def test_openai_multiple_finals_increment_item_id(ws_app):
    backend = FakeBackend(session_factory=lambda: FakeSession(
        flush_finals=[dict(FINAL, text="第一句"), dict(FINAL, text="第二句")]))
    client = ws_app(backend)
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "session.update", "session": {}})
        ws.receive_json()
        ws.send_json({"type": "input_audio_buffer.commit"})
        e0 = ws.receive_json()
        e1 = ws.receive_json()
        assert e0["item_id"] == "item_0" and e0["transcript"] == "第一句"
        assert e1["item_id"] == "item_1" and e1["transcript"] == "第二句"


# ─── DashScope Realtime ───

def test_dashscope_full_flow(ws_app):
    backend = FakeBackend()
    client = ws_app(backend)
    with client.websocket_connect(DASHSCOPE_WS) as ws:
        ws.send_json({"header": {"action": "run-task", "task_id": "task-1", "streaming": "duplex"},
                      "payload": {"task_group": "audio",
                                  "parameters": {"sample_rate": 16000, "language_hints": ["zh"]}}})
        started = ws.receive_json()
        assert started["header"]["event"] == "task-started"
        assert started["header"]["task_id"] == "task-1"

        ws.send_bytes(b"\x00\x00" * 100)
        ws.send_json({"header": {"action": "finish-task", "task_id": "task-1"}})

        result = ws.receive_json()
        assert result["header"]["event"] == "result-generated"
        assert result["header"]["task_id"] == "task-1"
        sent = result["payload"]["output"]["sentence"]
        assert sent["sentence_end"] is True
        assert sent["text"] == "你好世界"
        assert sent["begin_time"] == 0 and sent["end_time"] == 1000
        # words 秒→毫秒
        assert sent["words"][0] == {"begin_time": 0, "end_time": 500,
                                    "text": "你好", "punctuation": ""}

        finished = ws.receive_json()
        assert finished["header"]["event"] == "task-finished"


def test_dashscope_connection_reuse(ws_app):
    backend = FakeBackend()
    client = ws_app(backend)
    with client.websocket_connect(DASHSCOPE_WS) as ws:
        # 第一轮
        ws.send_json({"header": {"action": "run-task", "task_id": "t1"}, "payload": {}})
        assert ws.receive_json()["header"]["task_id"] == "t1"
        ws.send_json({"header": {"action": "finish-task"}})
        assert ws.receive_json()["header"]["event"] == "result-generated"
        assert ws.receive_json()["header"]["event"] == "task-finished"
        # 第二轮（同连接复用）
        ws.send_json({"header": {"action": "run-task", "task_id": "t2"}, "payload": {}})
        started2 = ws.receive_json()
        assert started2["header"]["event"] == "task-started"
        assert started2["header"]["task_id"] == "t2"
        ws.send_json({"header": {"action": "finish-task"}})
        assert ws.receive_json()["header"]["event"] == "result-generated"
        assert ws.receive_json()["header"]["event"] == "task-finished"
    assert backend.released == 1   # 整连接只释放一次（跨两轮）


# ─── 鉴权 / 容量 ───

def test_ws_auth_rejected(ws_app):
    from starlette.websockets import WebSocketDisconnect
    client = ws_app(FakeBackend(), api_key="sk-secret")
    with pytest.raises(WebSocketDisconnect) as ei:
        with client.websocket_connect(OPENAI_WS) as ws:
            ws.receive_json()
    assert ei.value.code == 1008


def test_ws_auth_accepted_with_token(ws_app):
    client = ws_app(FakeBackend(), api_key="sk-secret")
    with client.websocket_connect(OPENAI_WS + "?token=sk-secret") as ws:
        assert ws.receive_json()["type"] == "session.created"


def test_ws_capacity_rejected(ws_app):
    from starlette.websockets import WebSocketDisconnect
    backend = FakeBackend(capacity=0)
    client = ws_app(backend)
    with pytest.raises(WebSocketDisconnect) as ei:
        with client.websocket_connect(DASHSCOPE_WS) as ws:
            ws.receive_json()
    assert ei.value.code == 1013


# ─── R2：实时增量转译（vLLM 路线 A partial）───

def test_openai_r2_partial_delta(ws_app):
    """vLLM 累计 partial → OpenAI delta（增量后缀，与 completed 共用 item_id）。"""
    backend = FakeBackend(session_factory=FakePartialSession)
    client = ws_app(backend)
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()                                       # session.created
        ws.send_json({"type": "session.update", "session": {}})
        ws.receive_json()                                       # session.updated
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(b"\x00\x00" * 100).decode()})
        d0 = ws.receive_json()
        d1 = ws.receive_json()
        assert d0["type"] == "conversation.item.input_audio_transcription.delta"
        assert d0["delta"] == "你好" and d0["item_id"] == "item_0"
        assert d1["delta"] == "世界" and d1["item_id"] == "item_0"   # 增量后缀，非累计
        ws.send_json({"type": "input_audio_buffer.commit"})
        ev = ws.receive_json()
        assert ev["type"] == "conversation.item.input_audio_transcription.completed"
        assert ev["transcript"] == "你好世界" and ev["item_id"] == "item_0"  # delta 与 completed 同 item


def test_dashscope_r2_intermediate(ws_app):
    """vLLM 累计 partial → DashScope 中间 result-generated(sentence_end=false)，干净直发。"""
    backend = FakeBackend(session_factory=FakePartialSession)
    client = ws_app(backend)
    with client.websocket_connect(DASHSCOPE_WS) as ws:
        ws.send_json({"header": {"action": "run-task", "task_id": "t1"}, "payload": {}})
        assert ws.receive_json()["header"]["event"] == "task-started"
        ws.send_bytes(b"\x00\x00" * 100)
        for txt in ("你好", "你好世界"):
            r = ws.receive_json()
            assert r["header"]["event"] == "result-generated"
            s = r["payload"]["output"]["sentence"]
            assert s["sentence_end"] is False and s["text"] == txt
        ws.send_json({"header": {"action": "finish-task"}})
        fin = ws.receive_json()
        assert fin["payload"]["output"]["sentence"]["sentence_end"] is True
        assert ws.receive_json()["header"]["event"] == "task-finished"


def test_openai_adapter_partial_revision_best_effort():
    """OpenAI best-effort：纯追加取后缀；无新增/修订(非追加) → 不发；final 后基准归零+item 递增。"""
    from app.api.compat.openai_ws_routes import OpenAIRealtimeAdapter
    a = OpenAIRealtimeAdapter()
    assert a.translate_partials({"text": "你好"})[0]["delta"] == "你好"
    assert a.translate_partials({"text": "你好世界"})[0]["delta"] == "世界"
    assert a.translate_partials({"text": "你好世界"}) == []      # 无新增 → 不发
    assert a.translate_partials({"text": "你们好"}) == []        # 修订(非纯追加) → best-effort 跳过
    a.translate_finals({"text": "你们好", "start": 0, "end": 1})  # item_0 → seq 递增 + 基准归零
    nxt = a.translate_partials({"text": "早"})
    assert nxt[0]["delta"] == "早" and nxt[0]["item_id"] == "item_1"
