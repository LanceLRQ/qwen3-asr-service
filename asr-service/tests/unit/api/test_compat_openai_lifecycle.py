"""OpenAI Realtime 生命周期事件测试（speech / commit / item / completed）。

不加载模型、不触网、无长等待：
- adapter 层直接调用 translate_vad_start / translate_vad_end / on_commit / translate_finals；
- ws_bridge 层用 FakeSession 注入 VAD 帧，验证 _emit 分发与 commit 回执顺序；
- StreamSession / VllmStreamSession 用脚本化 VAD / mock 引擎验证 emit_vad_events 开关
  （默认关闭时原生 final 帧序列不变）。

funasr 仅在 app.runtime.stream_session 的 import 链上（VADEngine 顶层 import）；未安装时
注入最小 stub，测试不调用真实推理。
"""
import asyncio
import json
import sys
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import numpy as np
import pytest

try:                                       # 运行期依赖未装时给 import 链一个最小 stub
    import funasr  # noqa: F401
except ModuleNotFoundError:
    _stub = types.ModuleType("funasr")
    _stub.AutoModel = object
    sys.modules["funasr"] = _stub

from app.api.compat import ws_bridge
from app.api.compat.openai_ws_routes import (
    OpenAIRealtimeAdapter,
    _map_session_update,
    build_openai_ws_router,
)
from app.runtime.stream_session import StreamSession
from app.runtime.vllm_stream_session import VllmStreamBackend

OPENAI_WS = "/compat/openai/v1/realtime"
FINAL = {"type": "final", "seg_id": 0, "text": "你好世界", "start": 0, "end": 1000}

_TS = [
    "input_audio_buffer.speech_started",
    "input_audio_buffer.speech_stopped",
    "input_audio_buffer.committed",
    "conversation.item.created",
    "conversation.item.input_audio_transcription.completed",
]


class FakeWS:
    """记录 adapter 直接下发的事件（on_commit / on_configured 用）。"""

    def __init__(self):
        self.sent = []

    async def send_json(self, ev):
        self.sent.append(ev)


def _item_id(ev):
    """兼容 committed（顶层 item_id）与 item.created（item.id）。"""
    if ev["type"] == "conversation.item.created":
        return ev["item"]["id"]
    return ev.get("item_id")


# ─── adapter：服务端 VAD / 手动 commit ───

def test_server_vad_lifecycle_order_and_item_id():
    a = OpenAIRealtimeAdapter()
    events = []
    events += a.translate_vad_start({"type": "vad_start", "start": 0})
    events += a.translate_partials({"type": "partial", "text": "你好"})
    events += a.translate_partials({"type": "partial", "text": "你好世界"})
    events += a.translate_vad_end({"type": "vad_end", "start": 0, "end": 1000})
    events += a.translate_finals(dict(FINAL))

    assert [e["type"] for e in events] == [
        _TS[0],
        "conversation.item.input_audio_transcription.delta",
        "conversation.item.input_audio_transcription.delta",
        _TS[1], _TS[2], _TS[3], _TS[4],
    ]
    assert {_item_id(e) for e in events} == {"item_0"}
    completed = events[-1]
    assert completed["transcript"] == "你好世界"
    # speech 边界字段
    assert events[0]["audio_start_ms"] == 0
    assert events[3]["audio_end_ms"] == 1000


async def test_manual_commit_immediate_receipt_then_completed():
    a = OpenAIRealtimeAdapter()
    ws = FakeWS()
    await a.on_commit(ws, has_audio=True)
    assert [e["type"] for e in ws.sent] == [
        "input_audio_buffer.committed",
        "conversation.item.created",
    ]
    events = a.translate_finals(dict(FINAL))
    # 已 commit → final 只补 completed，不重复回执
    assert [e["type"] for e in events] == [
        "conversation.item.input_audio_transcription.completed"]
    assert ws.sent[0]["item_id"] == ws.sent[1]["item"]["id"] == events[0]["item_id"] == "item_0"


async def test_empty_commit_returns_error_without_allocating_item():
    a = OpenAIRealtimeAdapter()
    ws = FakeWS()
    await a.on_commit(ws, has_audio=False)
    assert ws.sent == [{
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "input_audio_buffer_commit_empty",
            "message": "Error committing input audio buffer: buffer is empty.",
        },
    }]
    assert a._item_seq == 0
    assert a._cur_item_id is None


async def test_null_turn_detection_suppresses_vad_but_commit_and_final_share_item():
    a = OpenAIRealtimeAdapter()
    a.classify({"text": json.dumps({"type": "session.update", "session": {
        "audio": {"input": {"turn_detection": None}}}})})
    assert a.translate_vad_start({"type": "vad_start", "start": 25}) == []
    assert a.translate_vad_end({"type": "vad_end", "start": 25, "end": 500}) == []

    ws = FakeWS()
    await a.on_commit(ws, has_audio=True)
    completed = a.translate_finals(dict(FINAL))
    assert [e["type"] for e in ws.sent] == [
        "input_audio_buffer.committed", "conversation.item.created"]
    assert [e["type"] for e in completed] == [
        "conversation.item.input_audio_transcription.completed"]
    assert ws.sent[0]["item_id"] == ws.sent[1]["item"]["id"] == completed[0]["item_id"] == "item_0"


async def test_late_vad_end_after_commit_is_suppressed():
    a = OpenAIRealtimeAdapter()
    ws = FakeWS()
    await a.on_commit(ws)                       # 客户端先手动 commit
    assert a.translate_vad_end({"type": "vad_end", "start": 0, "end": 1000}) == []
    events = a.translate_finals(dict(FINAL))
    assert [e["type"] for e in events] == [
        "conversation.item.input_audio_transcription.completed"]
    assert all(e["type"] != "input_audio_buffer.speech_stopped" for e in events)


def test_duplicate_vad_end_commits_once_and_next_item_id_advances():
    a = OpenAIRealtimeAdapter()
    first = a.translate_vad_end({"type": "vad_end", "start": 0, "end": 1000})
    assert [e["type"] for e in first] == _TS[1:4]
    # 同一句重复 VAD end：不再补 speech_stopped / committed
    assert a.translate_vad_end({"type": "vad_end", "start": 0, "end": 1000}) == []
    events = a.translate_finals(dict(FINAL))
    assert [e["type"] for e in events] == [_TS[-1]]
    assert {_item_id(e) for e in first + events} == {"item_0"}

    # 下一句换新 item_id，且生命周期仍一致
    nxt = a.translate_vad_start({"type": "vad_start", "start": 1000})
    assert _item_id(nxt[0]) == "item_1"
    assert a.translate_partials({"type": "partial", "text": "早"})[0]["item_id"] == "item_1"


def test_session_update_vad_flag_and_turn_detection_ga_beta():
    a = OpenAIRealtimeAdapter()
    # GA：session.audio.input.turn_detection
    kind, cfg_msg = a.classify({"text": json.dumps({
        "type": "session.update",
        "session": {"audio": {"input": {
            "format": {"rate": 16000},
            "transcription": {"language": "zh"},
            "turn_detection": {"type": "server_vad", "silence_duration_ms": 700},
        }}},
    })})
    assert kind == "configure"
    assert cfg_msg["emit_vad_events"] is True     # 兼容层按需开启后端 VAD 帧
    assert a._turn_detection == {"type": "server_vad", "silence_duration_ms": 700}

    # GA 路径标记并保留显式值
    assert a._turn_detection_path == "ga"

    # beta：session.turn_detection
    a.classify({"text": json.dumps({
        "type": "session.update",
        "session": {"turn_detection": None},
    })})
    assert a._turn_detection is None
    assert a._turn_detection_path == "beta"


async def test_session_updated_echoes_turn_detection():
    # 未指定路径时采用 transcription session 的 GA 结构。
    default = OpenAIRealtimeAdapter()
    default_ws = FakeWS()
    await default.on_configured(default_ws, [])
    default_input = default_ws.sent[0]["session"]["audio"]["input"]
    assert default_input["turn_detection"] == {"type": "server_vad"}
    assert "turn_detection" not in default_ws.sent[0]["session"]

    a = OpenAIRealtimeAdapter()
    ws = FakeWS()
    a.classify({"text": json.dumps({
        "type": "session.update",
        "session": {"turn_detection": {"type": "server_vad"}},
    })})
    a._audio_fs = 16000
    await a.on_configured(ws, [])
    assert ws.sent[0]["type"] == "session.updated"
    assert ws.sent[0]["session"]["audio"]["input"]["format"]["rate"] == 16000
    assert ws.sent[0]["session"]["turn_detection"] == {"type": "server_vad"}
    assert "turn_detection" not in ws.sent[0]["session"]["audio"]["input"]

    # GA 路径回显于 GA 位置，且显式 null 保持为 null。
    ga = OpenAIRealtimeAdapter()
    ga_ws = FakeWS()
    ga.classify({"text": json.dumps({"type": "session.update", "session": {
        "audio": {"input": {"turn_detection": None}}}})})
    await ga.on_configured(ga_ws, [])
    assert ga_ws.sent[0]["session"]["audio"]["input"]["turn_detection"] is None
    assert "turn_detection" not in ga_ws.sent[0]["session"]
    assert ga.translate_vad_start({"type": "vad_start", "start": 0}) == []

    # beta null 原样回显，且关闭 speech 事件。
    ws2 = FakeWS()
    a.classify({"text": json.dumps({
        "type": "session.update", "session": {"turn_detection": None}})})
    await a.on_configured(ws2, [])
    assert ws2.sent[0]["session"]["turn_detection"] is None
    assert a.translate_vad_start({"type": "vad_start", "start": 0}) == []
    assert a.translate_vad_end({"type": "vad_end", "start": 0, "end": 100}) == []


def test_map_session_update_requests_vad_events():
    assert _map_session_update({"session": {}})["emit_vad_events"] is True


# ─── ws_bridge：VAD 帧分发 + commit 回执顺序 ───

FINAL_VAD = dict(FINAL)


class FakeVadSession:
    """首帧即产 vad_start / vad_end / final（模拟服务端 VAD 整句）。"""

    def __init__(self):
        self._fed = 0

    def configure(self, msg):
        return []

    async def feed_audio(self, pcm):
        self._fed += 1
        if self._fed == 1:
            yield {"type": "vad_start", "start": 0}
            yield {"type": "vad_end", "start": 0, "end": 1000}
            yield dict(FINAL_VAD)

    async def flush(self):
        return
        yield                              # async generator：commit 后无末句


class FakeBackend:
    def __init__(self, factory):
        self._factory = factory
        self.released = 0

    async def acquire(self):
        return True

    def create_session(self, sid):
        return self._factory()

    def release(self, session):
        self.released += 1


@pytest.fixture
def ws_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.config as cfg

    def _make(backend):
        monkeypatch.setattr(cfg, "API_KEY", "")
        ws_bridge.init_compat_ws(backend)
        app = FastAPI()
        app.include_router(build_openai_ws_router())
        return TestClient(app)

    return _make


def test_bridge_server_vad_then_manual_commit_sequence(ws_client):
    backend = FakeBackend(lambda: FakeVadSession())
    client = ws_client(backend)
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()                                    # session.created
        ws.send_json({"type": "session.update", "session": {}})
        ws.receive_json()                                    # session.updated
        ws.send_json({"type": "input_audio_buffer.append",
                      "audio": "AAAA"})
        # 不等待 append 的处理结果，立即 commit；队列必须先处理音频再回 commit。
        ws.send_json({"type": "input_audio_buffer.commit"})

        got = [ws.receive_json() for _ in range(5)]
        assert [e["type"] for e in got] == _TS
        assert {_item_id(e) for e in got} == {"item_0"}
        # 音频已由 VAD 自动收句；排在其后的 commit 是空缓冲，不再分配 item_1。
        empty = ws.receive_json()
        assert empty["type"] == "error"
        assert empty["error"]["code"] == "input_audio_buffer_commit_empty"
        assert all(e["type"] not in (
            "input_audio_buffer.committed", "conversation.item.created")
            for e in [empty])


def test_ws_empty_commit_without_append_returns_error(ws_client):
    client = ws_client(FakeBackend(lambda: FakeVadSession()))
    with client.websocket_connect(OPENAI_WS) as ws:
        ws.receive_json()
        ws.send_json({"type": "session.update", "session": {}})
        ws.receive_json()
        ws.send_json({"type": "input_audio_buffer.commit"})
        ev = ws.receive_json()
        assert ev["type"] == "error"
        assert ev["error"]["code"] == "input_audio_buffer_commit_empty"


# ─── 后端 emit_vad_events：默认关闭 / 开启 ───

class FakeSVAD:
    def __init__(self, events_by_call):
        self.events_by_call = events_by_call
        self.calls = 0

    def new_cache(self):
        return {}

    def process_chunk(self, arr, cache, is_final, max_end_silence_ms=None):
        if is_final:
            return []
        ev = self.events_by_call.get(self.calls, [])
        self.calls += 1
        return list(ev)


def _pcm_ms(ms):
    n = int(ms * 16000 / 1000)
    return (np.ones(n, dtype="<i2") * 1000).tobytes()


def _stream_session(configure_msg):
    svad = FakeSVAD(events_by_call={
        0: [{"type": "start", "start": 0, "end": None}],
        1: [{"type": "end", "start": None, "end": 1000}],
    })
    asr = MagicMock()
    asr.transcribe_array.return_value = [types.SimpleNamespace(text="hi")]
    ex = ThreadPoolExecutor(max_workers=2)
    s = StreamSession("sid", svad, asr, None, ex, asyncio.Semaphore(1))
    s.configure(configure_msg)
    return s, ex


async def _collect(agen):
    return [m async for m in agen]


async def test_stream_session_vad_events_off_keeps_native_frames():
    s, ex = _stream_session({"audio_fs": 16000})
    try:
        assert await _collect(s.feed_audio(_pcm_ms(500))) == []
        assert [m["type"] for m in await _collect(s.feed_audio(_pcm_ms(500)))] == ["final"]
    finally:
        ex.shutdown(wait=False)


async def test_stream_session_vad_events_on():
    s, ex = _stream_session({"audio_fs": 16000, "emit_vad_events": True})
    try:
        first = await _collect(s.feed_audio(_pcm_ms(500)))
        second = await _collect(s.feed_audio(_pcm_ms(500)))
        assert [m["type"] for m in first] == ["vad_start"]
        assert [m["type"] for m in second] == ["vad_end", "final"]
        assert first[0]["start"] == 0
        assert second[0]["start"] == 0 and second[0]["end"] == 1000
    finally:
        ex.shutdown(wait=False)


class _MockEngine:
    def new_state(self, language=None, chunk_size_sec=None):
        return types.SimpleNamespace(text="", _acc="")

    def feed(self, arr, state):
        state._acc += "字"
        state.text = state._acc
        return state.text, None

    def finish(self, state):
        return state._acc + "。", None


def _vllm_session(configure_msg):
    backend = VllmStreamBackend(_MockEngine(), end_silence_ms=800)
    sess = backend.create_session("sid-vllm")
    sess.configure(configure_msg)
    return sess


async def test_vllm_session_vad_events_off_keeps_native_frames():
    sess = _vllm_session({"audio_fs": 16000})
    voice = (np.full(3200, 0.2, dtype=np.float32) * 32767).astype("<i2").tobytes()
    silence = np.zeros(3200, dtype="<i2").tobytes()
    msgs = []
    for _ in range(3):
        msgs += await _collect(sess.feed_audio(voice))
    for _ in range(4):
        msgs += await _collect(sess.feed_audio(silence))
    assert [m["type"] for m in msgs if m["type"] in ("vad_start", "vad_end")] == []
    assert [m["type"] for m in msgs].count("final") == 1


async def test_vllm_session_vad_events_on():
    sess = _vllm_session({"audio_fs": 16000, "emit_vad_events": True})
    voice = (np.full(3200, 0.2, dtype=np.float32) * 32767).astype("<i2").tobytes()
    silence = np.zeros(3200, dtype="<i2").tobytes()
    msgs = []
    for _ in range(3):
        msgs += await _collect(sess.feed_audio(voice))
    for _ in range(4):
        msgs += await _collect(sess.feed_audio(silence))
    types_seq = [m["type"] for m in msgs]
    assert types_seq[0] == "vad_start"
    assert types_seq[-2:] == ["vad_end", "final"]
    assert msgs[0]["start"] == 0
    assert msgs[-2]["start"] == 0 and msgs[-2]["end"] == 1400
