"""Qwen3-ASR-Flash-Realtime 兼容 WS（/compat/dashscope/api-ws/v1/realtime）单测。

覆盖：路由挂载开关、adapter 事件映射（session.created/updated、transcription.text/
completed、VAD/commit）、客户端 base64 音频解码、错误事件与正常结束。不加载模型、
不触网；WS 集成用 FakeBackend/FakeSession 直连 ASGI。
"""
import base64
import logging
import sys
import types

import pytest

# 轻量测试 venv 可能未装 PyYAML；main.py 顶层 import config_file 需要它。
# 本文件只装配路由、不读配置文件，缺省时注入最小 stub（真实环境不受影响）。
try:  # pragma: no cover - 仅在缺依赖的轻量 venv 触发
    import yaml  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    _yaml = types.ModuleType("yaml")

    class _YAMLError(Exception):
        pass

    class _SafeLoader:
        pass

    _yaml.YAMLError = _YAMLError
    _yaml.SafeLoader = _SafeLoader
    _yaml.load = lambda *a, **k: {}
    _yaml.safe_load = lambda *a, **k: {}
    sys.modules["yaml"] = _yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.config as cfg
from app.api.compat import ws_bridge
from app.api.compat.dashscope_ws_routes import (
    QwenASRRealtimeAdapter,
    build_dashscope_ws_router,
)

QWEN_WS = "/compat/dashscope/api-ws/v1/realtime"
FINAL = {"type": "final", "seg_id": 0, "text": "你好世界", "start": 0, "end": 1000}


class FakeWS:
    """adapter 单测用的最小 WebSocket 替身（只实现 query_params/send_json/close）。"""

    def __init__(self, query=None):
        self.query_params = query or {}
        self.sent = []
        self.closed = None

    async def send_json(self, obj):
        self.sent.append(obj)

    async def close(self, code=1000):
        self.closed = code


class FakeSession:
    def __init__(self, flush_finals=None, feed_error=None):
        self.configured = []
        self.fed = []
        self._flush_finals = flush_finals if flush_finals is not None else [FINAL]
        self._feed_error = feed_error

    def configure(self, msg):
        self.configured.append(msg)
        fs = msg.get("audio_fs")
        if fs is not None and fs < 0:
            raise ValueError(f"audio_fs 非法: {fs}")
        return []

    async def feed_audio(self, pcm):
        if self._feed_error:
            raise RuntimeError(self._feed_error)
        self.fed.append(pcm)
        return
        yield   # 使其成为 async generator（feed 不产 final）

    async def flush(self):
        # 与真实会话一致：flush 后缓冲清空，重复 flush 不再产 final
        finals, self._flush_finals = self._flush_finals, []
        for f in finals:
            yield f


class FakeVADSession(FakeSession):
    """Fake session emitting the same VAD lifecycle frame shape as stream sessions."""

    async def feed_audio(self, pcm):
        self.fed.append(pcm)
        yield {"type": "vad_start", "start": 10}
        yield {"type": "vad_end", "start": 10, "end": 120}


class FakeBackend:
    mode = "standard"
    backend = "vad-offline"
    capabilities = {"partial_results": False, "word_timestamps": True}

    def __init__(self, session_factory=None, capacity=10):
        self._factory = session_factory or (lambda: FakeSession())
        self._capacity = capacity
        self._active = 0
        self.released = 0
        self.session = None

    async def acquire(self):
        if self._active >= self._capacity:
            return False
        self._active += 1
        return True

    def create_session(self, sid):
        self.session = self._factory()
        return self.session

    def release(self, session):
        self._active = max(0, self._active - 1)
        self.released += 1


@pytest.fixture
def ws_client(monkeypatch):
    def _make(backend, api_key=""):
        monkeypatch.setattr(cfg, "API_KEY", api_key)
        ws_bridge.init_compat_ws(backend)
        app = FastAPI()
        app.include_router(build_dashscope_ws_router())
        return TestClient(app)

    return _make


# ─── 路由注册 ───

def test_router_keeps_inference_and_adds_realtime():
    paths = {r.path for r in build_dashscope_ws_router().routes}
    assert "/compat/dashscope/api-ws/v1/inference" in paths
    assert QWEN_WS in paths


def _args(**over):
    base = dict(
        serve_mode="standard", device="cpu", model_size=None, enable_align=True,
        enable_punc=False, model_source="modelscope", host=None, port=None,
        web=False, max_segment=5, asr_batch_size=4, api_key=None, max_queue_size=None,
        enable_stream=True, enable_openai_api=False, enable_dashscope_api=False,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def isolated_create_app(tmp_path, monkeypatch):
    """隔离 create_app 的日志/cfg 全局副作用（同 test_main_serve_mode）。"""
    from app.utils import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(logger_mod, "LOG_FILE", str(tmp_path / "logs" / "asr.log"))

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    keys = ("SERVE_MODE", "ENABLE_STREAM", "MAX_STREAM_SESSIONS", "STREAM_ASR_CONCURRENCY",
            "ENABLE_DASHSCOPE_API", "ENABLE_OPENAI_API", "API_KEY", "CONFIG_FILE")
    snapshot = {k: getattr(cfg, k) for k in keys if hasattr(cfg, k)}

    yield

    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for k, v in snapshot.items():
        setattr(cfg, k, v)


def _mock_standard(monkeypatch):
    import app.main as main

    class FakeEngine:
        BACKEND = "fake"
        def __init__(self, *a, **k): pass
        def load(self): pass
        def unload(self): pass
        @property
        def align_enabled(self): return True
        def transcribe(self, *a, **k): return {"text": ""}

    class FakeVAD:
        BACKEND = "fake-vad"
        def __init__(self, *a, **k): pass
        def load(self): pass
        def unload(self): pass

    class FakeTM:
        def __init__(self, *a, **k): pass
        def set_processor(self, fn): pass
        def start(self): pass
        def shutdown(self): pass
        def list_tasks(self, status=None): return []
        def get_task(self, task_id): return None

    monkeypatch.setattr(main, "detect_device", lambda: {"type": "cpu", "vram_gb": None})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cpu")
    qwen_mod = types.ModuleType("app.engines.qwen_asr_engine")
    qwen_mod.QwenASREngine = FakeEngine
    monkeypatch.setitem(sys.modules, "app.engines.qwen_asr_engine", qwen_mod)
    vad_mod = types.ModuleType("app.engines.vad_engine")
    vad_mod.VADEngine = FakeVAD
    monkeypatch.setitem(sys.modules, "app.engines.vad_engine", vad_mod)
    monkeypatch.setattr("app.runtime.task_manager.TaskManager", FakeTM)


def _ws_paths(app):
    paths = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is not None:
            paths.add(path)
        paths.update(getattr(child, "path", None) for child in getattr(route, "routes", []))
    return paths


def test_realtime_route_mounted_by_real_vllm_assembly(isolated_create_app, monkeypatch):
    """走 create_app → _assemble_vllm 的真实挂载分支，仅替换引擎/任务运行时。"""
    import app.main as main

    class FakeVLLMEngine:
        def __init__(self, *a, **k): pass
        def load(self): pass
        @property
        def align_enabled(self): return True

    class FakeTaskManager:
        def __init__(self, *a, **k): pass
        def set_processor(self, fn): pass
        def start(self): pass
        def shutdown(self): pass
        def list_tasks(self, status=None): return []
        def get_task(self, task_id): return None

    vllm_mod = types.ModuleType("app.engines.vllm_asr_engine")
    vllm_mod.VLLMASREngine = FakeVLLMEngine
    monkeypatch.setitem(sys.modules, "app.engines.vllm_asr_engine", vllm_mod)
    monkeypatch.setattr(main, "detect_device",
                        lambda: {"type": "cuda", "vram_gb": 24.0, "name": "FakeGPU"})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cuda")
    monkeypatch.setattr("app.runtime.task_manager.TaskManager", FakeTaskManager)

    enabled = main.create_app(_args(serve_mode="vllm", device="auto",
                                    enable_dashscope_api=True))
    with TestClient(enabled).websocket_connect(QWEN_WS) as ws:
        assert ws.receive_json()["type"] == "session.created"

    from starlette.websockets import WebSocketDisconnect
    disabled = main.create_app(_args(serve_mode="vllm", device="auto",
                                     enable_dashscope_api=False))
    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(disabled).websocket_connect(QWEN_WS):
            pass
    assert exc.value.code == 1000


# ─── adapter：会话建立 / 更新 ───

async def test_on_open_sends_session_created_with_default_model():
    ws = FakeWS()
    adapter = QwenASRRealtimeAdapter()
    await adapter.on_open(ws, backend=None)

    created = ws.sent[0]
    assert created["type"] == "session.created"
    assert created["session"]["object"] == "realtime.session"
    assert created["session"]["model"] == "qwen3-asr-flash-realtime"
    assert created["session"]["input_audio_format"] == "pcm16"


async def test_on_open_accepts_supported_model_query():
    ws = FakeWS({"model": "qwen3-asr-flash-realtime-2026-01-01"})
    await QwenASRRealtimeAdapter().on_open(ws, backend=None)
    assert ws.sent[0]["session"]["model"] == "qwen3-asr-flash-realtime-2026-01-01"


async def test_on_open_rejects_unknown_model_with_error_and_close():
    ws = FakeWS({"model": "gpt-4o-realtime"})
    await QwenASRRealtimeAdapter().on_open(ws, backend=None)

    assert ws.sent[0]["type"] == "error"
    assert ws.sent[0]["error"]["code"] == "invalid_model"
    assert ws.sent[0]["error"]["type"] == "server_error"
    assert ws.closed == 1008


def test_session_update_maps_rate_language_and_vad_switch():
    adapter = QwenASRRealtimeAdapter()
    kind, cfg_msg = adapter.classify({"text": (
        '{"type":"session.update","session":{"sample_rate":48000,'
        '"input_audio_transcription":{"language":"en"},'
        '"turn_detection":{"type":"server_vad","silence_duration_ms":300}}}'
    )})

    assert kind == "configure"
    assert cfg_msg["audio_fs"] == 48000
    assert cfg_msg["language"] == "English"
    assert cfg_msg["emit_vad_events"] is True
    assert adapter._language == "en"
    assert adapter._turn_detection["silence_duration_ms"] == 300


def test_session_update_accepts_ga_rate_path_and_defaults():
    adapter = QwenASRRealtimeAdapter()
    _, cfg_msg = adapter.classify({"text": (
        '{"type":"session.update","session":{"audio":{"input":'
        '{"format":{"rate":16000}}}}}'
    )})
    assert cfg_msg["audio_fs"] == 16000

    _, cfg_msg = adapter.classify({"text": '{"type":"session.update","session":{}}'})
    assert cfg_msg["audio_fs"] == 16000


async def test_on_configured_echoes_session_updated():
    ws = FakeWS()
    adapter = QwenASRRealtimeAdapter()
    await adapter.on_configured(ws, ["ignored_param"])
    updated = ws.sent[0]
    assert updated["type"] == "session.updated"
    assert updated["session"]["model"] == "qwen3-asr-flash-realtime"


# ─── adapter：音频 / 控制帧 ───

def test_append_decodes_base64_pcm():
    adapter = QwenASRRealtimeAdapter()
    pcm = b"\x00\x01" * 64
    kind, payload = adapter.classify({"text": (
        '{"type":"input_audio_buffer.append","audio":"'
        + base64.b64encode(pcm).decode() + '"}'
    )})
    assert kind == "audio" and payload == pcm


@pytest.mark.parametrize("payload", ["%%%not-base64%%%", "a", "ab="])
def test_invalid_base64_returns_protocol_error_without_raising(payload):
    adapter = QwenASRRealtimeAdapter()
    kind, cfg_msg = adapter.classify({"text": (
        '{"type":"input_audio_buffer.append","audio":"' + payload + '"}'
    )})
    assert kind == "configure"
    assert cfg_msg == {"audio_fs": -1}
    error = adapter.translate_error("invalid_config", "audio_fs 非法: -1", fatal=True)
    assert error["type"] == "error"
    assert error["error"]["code"] == "invalid_value"
    assert error["error"]["type"] == "invalid_request_error"


def test_oversized_base64_is_rejected_before_decode(monkeypatch):
    monkeypatch.setattr(cfg, "STREAM_MAX_FRAME_BYTES", 8)
    adapter = QwenASRRealtimeAdapter()
    kind, payload = adapter.classify({"text": (
        '{"type":"input_audio_buffer.append","audio":"' + "A" * 20 + '"}'
    )})
    assert kind == "audio"
    assert len(payload) == cfg.STREAM_MAX_FRAME_BYTES + 1


def test_commit_without_final_then_append_allocates_next_item():
    import asyncio

    adapter = QwenASRRealtimeAdapter()
    pcm = base64.b64encode(b"\x00\x01" * 80).decode()
    assert adapter.classify({"text": (
        '{"type":"input_audio_buffer.append","audio":"' + pcm + '"}'
    )})[0] == "audio"

    ws = FakeWS()
    asyncio.run(adapter.on_commit(ws, has_audio=True))
    assert ws.sent[0]["item_id"] == "item_0"
    assert adapter._cur_item_id == "item_0"

    kind, decoded = adapter.classify({"text": (
        '{"type":"input_audio_buffer.append","audio":"' + pcm + '"}'
    )})
    assert kind == "audio" and decoded == b"\x00\x01" * 80
    # classify is receive-side only; item changes after the consumer observes VAD start.
    assert adapter._cur_item_id == "item_0"
    assert adapter._item_seq == 1
    adapter.translate_vad_start({"start": 0})
    assert adapter._cur_item_id == "item_1"
    assert adapter._item_seq == 2
    events = adapter.translate_finals({"text": "第二句"})
    assert events[-1]["item_id"] == "item_1"
    assert events[0]["item_id"] == "item_1"


def test_commit_and_finish_control_frames():
    adapter = QwenASRRealtimeAdapter()
    assert adapter.classify({"text": '{"type":"input_audio_buffer.commit"}'}) == ("flush", None)
    assert adapter.classify({"text": '{"type":"session.finish"}'}) == ("end", None)
    # 未知事件 / 二进制帧忽略
    assert adapter.classify({"text": '{"type":"response.cancel"}'}) == ("ignore", None)
    assert adapter.classify({"bytes": b"\x00"}) == ("ignore", None)


# ─── adapter：事件映射 ───

def test_partial_and_final_share_item_and_emit_completed():
    adapter = QwenASRRealtimeAdapter()

    partial = adapter.translate_partials({"text": "你好"})[0]
    assert partial["type"] == "conversation.item.input_audio_transcription.text"
    assert partial["text"] == "" and partial["stash"] == "你好"
    assert partial["item_id"] == "item_0"

    events = adapter.translate_finals(FINAL)
    types = [e["type"] for e in events]
    assert types == [
        "input_audio_buffer.committed",
        "conversation.item.created",
        "conversation.item.input_audio_transcription.completed",
    ]
    assert events[0]["item_id"] == "item_0"
    completed = events[-1]
    assert completed["transcript"] == "你好世界"
    assert completed["item_id"] == "item_0"
    assert completed["language"] == "zh"

    # 下一句递增 item_id
    assert adapter.translate_finals(FINAL)[-1]["item_id"] == "item_1"


def test_vad_lifecycle_shares_item_and_commit_is_idempotent():
    adapter = QwenASRRealtimeAdapter()

    started = adapter.translate_vad_start({"start": 120})[0]
    assert started["type"] == "input_audio_buffer.speech_started"
    assert started["audio_start_ms"] == 120 and started["item_id"] == "item_0"

    stopped = adapter.translate_vad_end({"end": 980})
    assert [e["type"] for e in stopped] == [
        "input_audio_buffer.speech_stopped",
        "input_audio_buffer.committed",
        "conversation.item.created",
    ]
    assert stopped[0]["item_id"] == "item_0"

    # 已 commit 后迟到的 VAD end 不补发 speech_stopped
    assert adapter.translate_vad_end({"end": 1000}) == []


async def test_on_commit_sends_committed_receipt_once():
    ws = FakeWS()
    adapter = QwenASRRealtimeAdapter()
    await adapter.on_commit(ws)
    await adapter.on_commit(ws)
    assert [e["type"] for e in ws.sent] == [
        "input_audio_buffer.committed",
        "conversation.item.created",
    ]


async def test_on_commit_without_audio_reports_error_without_creating_item():
    ws = FakeWS()
    adapter = QwenASRRealtimeAdapter()

    await adapter.on_commit(ws, has_audio=False)

    assert len(ws.sent) == 1
    assert ws.sent[0]["type"] == "error"
    assert ws.sent[0]["error"]["code"] == "input_audio_buffer_commit_empty"
    assert adapter._cur_item_id is None
    assert adapter._item_seq == 0


def test_error_event_shape_and_finish_event():
    adapter = QwenASRRealtimeAdapter()
    err = adapter.translate_error("feed_failed", "音频处理失败")
    assert err["type"] == "error"
    assert err["error"] == {"type": "invalid_request_error",
                            "code": "feed_failed", "message": "音频处理失败"}
    fatal = adapter.translate_error("internal", "内部错误", fatal=True)
    assert fatal["error"]["type"] == "server_error"


async def test_on_finish_sends_session_finished():
    ws = FakeWS()
    await QwenASRRealtimeAdapter().on_finish(ws)
    assert ws.sent[0]["type"] == "session.finished"


# ─── WS 集成：完整一轮 ───

def test_qwen_ws_full_flow(ws_client):
    backend = FakeBackend()
    client = ws_client(backend)
    pcm = b"\x00\x02" * 100

    with client.websocket_connect(QWEN_WS + "?model=qwen3-asr-flash-realtime") as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["session"]["model"] == "qwen3-asr-flash-realtime"

        ws.send_json({"type": "session.update", "session": {
            "sample_rate": 16000, "input_audio_transcription": {"language": "zh"}}})
        updated = ws.receive_json()
        assert updated["type"] == "session.updated"

        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(pcm).decode()})
        ws.send_json({"type": "input_audio_buffer.commit"})

        assert ws.receive_json()["type"] == "input_audio_buffer.committed"
        assert ws.receive_json()["type"] == "conversation.item.created"
        completed = ws.receive_json()
        assert completed["type"] == "conversation.item.input_audio_transcription.completed"
        assert completed["transcript"] == "你好世界"

        ws.send_json({"type": "session.finish"})
        assert ws.receive_json()["type"] == "session.finished"

    assert backend.session.configured[0]["audio_fs"] == 16000
    assert backend.session.fed == [pcm]          # base64 解码后原样入会话
    assert backend.released == 1


def test_vad_events_flow_through_ws_bridge(ws_client):
    backend = FakeBackend(session_factory=FakeVADSession)
    client = ws_client(backend)
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "session.update", "session": {}})
        ws.receive_json()  # session.updated
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(b"\x00\x00" * 32).decode()})
        started = ws.receive_json()
        stopped = ws.receive_json()
        committed = ws.receive_json()
        item = ws.receive_json()
        assert started["type"] == "input_audio_buffer.speech_started"
        assert started["item_id"] == "item_0"
        assert stopped["type"] == "input_audio_buffer.speech_stopped"
        assert stopped["item_id"] == "item_0"
        assert committed["type"] == "input_audio_buffer.committed"
        assert item["type"] == "conversation.item.created"

        ws.send_json({"type": "session.finish"})
        assert ws.receive_json()["type"] == "conversation.item.input_audio_transcription.completed"
        assert ws.receive_json()["type"] == "session.finished"


def test_turn_detection_null_suppresses_vad_events_but_keeps_final(ws_client):
    backend = FakeBackend(session_factory=FakeVADSession)
    client = ws_client(backend)
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "session.update", "session": {"turn_detection": None}})
        ws.receive_json()  # session.updated
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(b"\x00\x00" * 32).decode()})
        ws.send_json({"type": "session.finish"})

        events = [ws.receive_json() for _ in range(4)]
        types_seen = [event["type"] for event in events]
        assert "input_audio_buffer.speech_started" not in types_seen
        assert "input_audio_buffer.speech_stopped" not in types_seen
        assert "input_audio_buffer.committed" in types_seen
        assert "conversation.item.input_audio_transcription.completed" in types_seen
        assert types_seen[-1] == "session.finished"


def test_commit_sentinel_empty_buffer_reports_error(ws_client):
    client = ws_client(FakeBackend(session_factory=lambda: FakeSession(flush_finals=[])))
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "input_audio_buffer.commit"})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "input_audio_buffer_commit_empty"
        ws.send_json({"type": "session.finish"})
        assert ws.receive_json()["type"] == "session.finished"


def test_commit_then_immediate_append_keeps_completed_items_separate(ws_client, monkeypatch):
    import asyncio
    import threading

    first = {**FINAL, "text": "第一句"}
    second = {**FINAL, "text": "第二句"}
    first_pcm = b"\\x01\\x00" * 80
    second_pcm = b"\\x02\\x00" * 80

    second_append_classified = threading.Event()

    class FrameOrderedSession(FakeSession):
        def __init__(self):
            super().__init__(flush_finals=[])
            self.fed_frames = []
            self.final_trace = []

        async def feed_audio(self, pcm):
            frame_no = len(self.fed_frames) + 1
            self.fed_frames.append(pcm)
            if pcm == second_pcm:
                # 第二句 final 只能由第二帧的 feed 产生。
                assert frame_no == 2
                self.final_trace.append(("第二句", frame_no))
                yield second

        async def flush(self):
            # 固定排程：等接收侧已分类第二 append 后才让 commit flush 产出第一句。
            # 这样旧版 classify() 若抢先推进 item，回归会稳定复现，而非依赖调度运气。
            if self.fed_frames == [first_pcm]:
                await asyncio.to_thread(second_append_classified.wait)
                self.final_trace.append(("第一句", len(self.fed_frames)))
                yield first

    original_classify = QwenASRRealtimeAdapter.classify

    def classify_and_mark_second_append(adapter, message):
        result = original_classify(adapter, message)
        if result == ("audio", second_pcm):
            second_append_classified.set()
        return result

    monkeypatch.setattr(QwenASRRealtimeAdapter, "classify", classify_and_mark_second_append)
    backend = FakeBackend(session_factory=FrameOrderedSession)
    client = ws_client(backend)
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(first_pcm).decode()})
        ws.send_json({"type": "input_audio_buffer.commit"})
        # 不读取 commit 回执，立即追加第二帧；接收侧不能抢先改变 item 状态。
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(second_pcm).decode()})
        ws.send_json({"type": "session.finish"})

        events = []
        while True:
            event = ws.receive_json()
            events.append(event)
            if event["type"] == "session.finished":
                break

        completed = [e for e in events if e["type"] ==
                     "conversation.item.input_audio_transcription.completed"]
        assert [(e["item_id"], e["transcript"]) for e in completed] == [
            ("item_0", "第一句"),
            ("item_1", "第二句"),
        ]
        assert [e["item"]["id"] for e in events
                if e["type"] == "conversation.item.created"] == ["item_0", "item_1"]
        assert backend.session.fed_frames == [first_pcm, second_pcm]
        assert backend.session.final_trace == [("第一句", 1), ("第二句", 2)]
        assert backend.session.final_trace[1][1] == len(backend.session.fed_frames)


def test_qwen_ws_feed_error_reports_event_then_finishes(ws_client):
    backend = FakeBackend(session_factory=lambda: FakeSession(
        flush_finals=[], feed_error="模拟上游失败"))
    client = ws_client(backend)

    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()   # session.created
        ws.send_json({"type": "session.update", "session": {"sample_rate": 16000}})
        ws.receive_json()   # session.updated
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": base64.b64encode(b"\x00\x00").decode()})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["error"]["code"] == "feed_failed"

        # 错误未吞掉连接：仍可正常结束
        ws.send_json({"type": "session.finish"})
        assert ws.receive_json()["type"] == "session.finished"


@pytest.mark.parametrize("audio", ["%%%not-base64%%%", "a", "ab="])
def test_qwen_ws_invalid_base64_reports_error(ws_client, audio):
    client = ws_client(FakeBackend())
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "input_audio_buffer.append", "audio": audio})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "invalid_value"


def test_qwen_ws_oversized_base64_rejected_by_frame_limit(ws_client, monkeypatch):
    monkeypatch.setattr(cfg, "STREAM_MAX_FRAME_BYTES", 8)
    client = ws_client(FakeBackend())
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()  # session.created
        ws.send_json({"type": "input_audio_buffer.append", "audio": "A" * 20})
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["error"]["code"] == "frame_too_large"


def test_qwen_ws_invalid_config_reports_error(ws_client):
    client = ws_client(FakeBackend())
    with client.websocket_connect(QWEN_WS) as ws:
        ws.receive_json()   # session.created
        ws.send_json({"type": "session.update", "session": {"sample_rate": -1}})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["error"]["code"] == "invalid_config"


def test_qwen_ws_capacity_rejected(ws_client):
    from starlette.websockets import WebSocketDisconnect

    client = ws_client(FakeBackend(capacity=0))
    with pytest.raises(WebSocketDisconnect) as ei:
        with client.websocket_connect(QWEN_WS) as ws:
            ws.receive_json()
    assert ei.value.code == 1013
