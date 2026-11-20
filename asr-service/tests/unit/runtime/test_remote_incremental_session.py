import asyncio

import httpx
import numpy as np
import pytest

from app.engines.remote_asr_engine import RemoteASRError
from app.runtime.remote_stream_session import RemoteStreamBackend


VOICE = np.full(1600, 8192, dtype="<i2").tobytes()


async def collect(events):
    return [event async for event in events]


@pytest.fixture
def demo(monkeypatch):
    calls = []
    replies = {}
    client = httpx.AsyncClient

    async def handler(request):
        action = request.url.path.removeprefix("/demo/api/")
        assert request.method == "POST"
        calls.append((action, request.url.params.get("session_id"), request.content))
        if action in replies:
            return await replies[action](request)
        if action == "start":
            return httpx.Response(200, json={"session_id": "upstream"})
        if action == "chunk":
            assert request.headers["content-type"] == "application/octet-stream"
        return httpx.Response(200, json={"language": "English", "text": action})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(
        transport=httpx.MockTransport(handler), **kw))
    backend = RemoteStreamBackend("http://demo/demo/", 1, max_sessions=8, max_utterance_sec=1)
    return backend, calls, replies


async def test_wire_endpoint_flush_and_reconfigure(demo):
    backend, calls, _ = demo
    assert backend.mode == "remote" and backend.backend == "remote-qwen-streaming"
    assert backend.capabilities == {
        "partial_results": True, "languages_auto": True, "word_timestamps": False,
        "speaker_labels": False, "output_toggles": False,
    }
    assert await backend.acquire() and not await backend.acquire()
    session = backend.create_session("ws")
    assert session.configure({}) == []
    assert await collect(session.feed_audio(bytes(3200))) == []
    assert await collect(session.feed_audio(VOICE)) == [{"type": "partial", "seg_id": 0, "text": "chunk"}]
    assert session.configure({"audio_fs": 16000}) == []
    with pytest.raises(ValueError):
        session.configure({"audio_fs": 8000})
    assert await collect(session.feed_audio(VOICE)) == []
    final, = await collect(session.feed_audio(bytes(25600)))
    assert final == dict(type="final", text="finish", seg_id=0, start=100, end=1100)
    assert await collect(session.flush()) == []
    assert [name for name, _, _ in calls] == ["start", "chunk", "chunk", "chunk", "finish"]
    assert all(sid == "upstream" for _, sid, _ in calls[1:])
    np.testing.assert_array_equal(np.frombuffer(calls[1][2], "<f4"), np.full(1600, .25))
    for seg in (1, 2):
        partial, = await collect(session.feed_audio(VOICE))
        assert partial["seg_id"] == seg
        assert len(await collect(session.flush())) == 1
        call_count = len(calls)
        assert await collect(session.flush()) == []
        assert len(calls) == call_count
    assert "language" in session.configure({"audio_fs": 8000, "language": "en"})
    await collect(session.feed_audio(VOICE))
    assert len(calls[-1][2]) == 3200 * 4
    await session.aclose()
    await session.aclose()
    assert calls[-1][0] == "finish"
    backend.release(session)
    assert await backend.acquire()


async def test_long_frame_split_without_replay(demo):
    backend, calls, _ = demo
    session = backend.create_session("ws")
    events = await collect(session.feed_audio(VOICE * 21))
    events += await collect(session.flush())
    assert [e["end"] for e in events if e["type"] == "final"] == [1000, 2000, 2100]
    assert sum(len(body) for op, _, body in calls if op == "chunk") == 21 * 1600 * 4
    await session.aclose()


@pytest.mark.parametrize("action", ["start", "chunk", "finish"])
async def test_lost_response_is_terminal_and_never_replayed(demo, action):
    backend, calls, replies = demo
    async def fail(request):
        raise httpx.ReadTimeout("lost")
    replies[action] = fail
    session = backend.create_session("ws")
    with pytest.raises(RemoteASRError):
        await collect(session.feed_audio(VOICE))
        await collect(session.flush())
    with pytest.raises(RemoteASRError):
        await collect(session.feed_audio(VOICE))
    await session.aclose()
    await session.aclose()
    expected = ["start"] if action == "start" else ["start", "chunk", "finish"]
    assert [op for op, _, _ in calls] == expected


@pytest.mark.parametrize("action", ["start", "chunk", "finish"])
async def test_abort_drains_request_then_finishes_once(demo, action):
    backend, calls, replies = demo
    entered, resume = asyncio.Event(), asyncio.Event()
    async def delayed(request):
        entered.set()
        await resume.wait()
        return httpx.Response(200, json={"session_id": "upstream", "text": "ok"})
    replies[action] = delayed
    session = backend.create_session("ws")
    async def send():
        await collect(session.feed_audio(VOICE))
        await collect(session.flush())
    task = asyncio.create_task(send())
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    close = asyncio.create_task(session.aclose())
    await asyncio.sleep(0)
    assert not close.done()
    resume.set()
    await asyncio.wait_for(close, 1)
    assert [op for op, _, _ in calls] == (["start", "finish"] if action == "start"
                                          else ["start", "chunk", "finish"])


async def test_request_deadline_without_asyncio_timeout(demo, monkeypatch):
    _, calls, replies = demo
    monkeypatch.delattr(asyncio, "timeout", raising=False)

    async def stall(request):
        await asyncio.Event().wait()

    replies["start"] = stall
    backend = RemoteStreamBackend("http://demo/demo/", 0.05, max_sessions=1, max_utterance_sec=1)
    session = backend.create_session("ws")
    with pytest.raises(RemoteASRError, match="start"):
        await asyncio.wait_for(collect(session.feed_audio(VOICE)), 1)
    await session.aclose()
    assert [op for op, _, _ in calls] == ["start"]


async def test_empty_revision_clears_previously_visible_partial(demo):
    backend, _, replies = demo
    snapshots = iter(["", "今天", ""])

    async def chunk(request):
        return httpx.Response(200, json={"text": next(snapshots)})

    replies["chunk"] = chunk
    session = backend.create_session("ws")
    try:
        assert await collect(session.feed_audio(VOICE)) == []
        assert await collect(session.feed_audio(VOICE)) == [dict(type="partial", seg_id=0, text="今天")]
        assert await collect(session.feed_audio(VOICE)) == [dict(type="partial", seg_id=0, text="")]
    finally:
        await session.aclose()
