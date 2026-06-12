"""vLLM 流式会话/后端单元测试（不依赖 vLLM / GPU）。

EnergyEndpointer 能量端点事件、VllmStreamSession 信封序列（mock 引擎）、
VllmStreamBackend 准入/释放/能力。在 standard venv 即可运行（模块不 import vllm）。
"""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from app.runtime.vllm_stream_session import (
    EnergyEndpointer, VllmStreamSession, VllmStreamBackend,
)

SR = 16000


def _pcm16_bytes(amp, ms):
    n = int(SR * ms / 1000)
    return (np.full(n, amp, dtype=np.float32) * 32767).astype("<i2").tobytes()


def _voice(ms=200):
    return _pcm16_bytes(0.2, ms)      # rms≈-14 dBFS ≥ -45 → 语音


def _silence(ms=200):
    return _pcm16_bytes(0.0, ms)      # -120 dBFS → 静音


async def _collect(agen):
    return [m async for m in agen]


# ─── EnergyEndpointer ───

def test_endpointer_start_then_end():
    ep = EnergyEndpointer(energy_floor_dbfs=-45.0, end_silence_ms=800)
    v = np.full(3200, 0.2, dtype=np.float32)      # 200ms 语音
    s = np.zeros(3200, dtype=np.float32)          # 200ms 静音

    assert ep.process(s, 200) == []               # 静音不起句
    ev = ep.process(v, 200)
    assert ev == [{"type": "start", "start": 200}]
    assert ep.in_speech is True
    assert ep.process(v, 200) == []               # 句内无事件
    # 尾静音累计：800ms（4×200）才判停
    assert ep.process(s, 200) == []
    assert ep.process(s, 200) == []
    assert ep.process(s, 200) == []
    ev = ep.process(s, 200)
    assert ev == [{"type": "end", "end": 1400}]
    assert ep.in_speech is False


def test_endpointer_reset():
    ep = EnergyEndpointer()
    ep.process(np.full(3200, 0.2, dtype=np.float32), 200)
    assert ep.in_speech is True
    ep.reset()
    assert ep.in_speech is False


# ─── mock 引擎 ───

class _MockEngine:
    """累积式 mock：每次 feed 追加一个字符，finish 补句号。"""

    def __init__(self):
        self.feeds = 0
        self.new_states = 0

    def new_state(self, language=None, chunk_size_sec=None):
        self.new_states += 1
        return SimpleNamespace(text="", language=language or "Chinese",
                               _acc="", chunk_size_sec=chunk_size_sec)

    def feed(self, arr, state):
        self.feeds += 1
        state._acc += "字"
        state.text = state._acc
        return state.text, state.language

    def finish(self, state):
        state.text = state._acc + "。"
        return state.text, state.language


def _make_session(engine=None, **bk):
    eng = engine or _MockEngine()
    backend = VllmStreamBackend(eng, **bk)
    return backend, backend.create_session("sid-test-0001")


# ─── VllmStreamSession.configure ───

def test_configure_warns_unsupported_params():
    _, sess = _make_session()
    warns = sess.configure({"audio_fs": 16000, "with_words": True, "diarize": True,
                            "with_punc": True, "speaker_threshold": 0.5})
    assert set(warns) == {"with_words", "diarize", "with_punc", "speaker_threshold"}


def test_configure_invalid_audio_fs_raises():
    _, sess = _make_session()
    with pytest.raises(ValueError):
        sess.configure({"audio_fs": 100})        # < 8000 下限


def test_configure_chunk_size_override_and_range():
    _, sess = _make_session()
    assert sess.configure({"chunk_size_sec": 1.5}) == []
    assert sess._chunk_size_sec == 1.5
    with pytest.raises(ValueError):
        sess.configure({"chunk_size_sec": 10})    # > 5.0 上限


# ─── VllmStreamSession.feed_audio / flush ───

def test_feed_audio_partial_then_final():
    eng = _MockEngine()
    backend, sess = _make_session(eng, end_silence_ms=800)
    sess.configure({"audio_fs": 16000})

    async def run():
        msgs = []
        for _ in range(3):                        # 语音 → 起句 + partial
            msgs += await _collect(sess.feed_audio(_voice()))
        for _ in range(4):                        # 800ms 静音 → 判停 final
            msgs += await _collect(sess.feed_audio(_silence()))
        return msgs

    msgs = asyncio.run(run())
    partials = [m for m in msgs if m["type"] == "partial"]
    finals = [m for m in msgs if m["type"] == "final"]

    assert len(partials) >= 3
    assert all(m["seg_id"] == 0 and m["text"] for m in partials)
    assert len(finals) == 1
    f = finals[0]
    assert f["seg_id"] == 0 and f["text"].endswith("。")
    assert f["start"] == 0 and f["end"] == 1400
    assert sess.state is None                     # 句尾已 reset
    assert eng.new_states == 1                    # 仅起了一句


def test_flush_emits_final_for_open_segment():
    eng = _MockEngine()
    _, sess = _make_session(eng)
    sess.configure({"audio_fs": 16000})

    async def run():
        msgs = []
        for _ in range(2):                        # 起句但不静音收尾
            msgs += await _collect(sess.feed_audio(_voice()))
        msgs += await _collect(sess.flush())      # stop → 冲刷末句
        return msgs

    msgs = asyncio.run(run())
    finals = [m for m in msgs if m["type"] == "final"]
    assert len(finals) == 1
    assert finals[0]["text"].endswith("。")
    assert sess.state is None


def test_feed_audio_silence_only_no_segment():
    eng = _MockEngine()
    _, sess = _make_session(eng)
    sess.configure({"audio_fs": 16000})

    async def run():
        msgs = []
        for _ in range(5):
            msgs += await _collect(sess.feed_audio(_silence()))
        return msgs

    msgs = asyncio.run(run())
    assert msgs == []                             # 全静音不起句、不解码
    assert eng.new_states == 0 and eng.feeds == 0


# ─── VllmStreamBackend ───

def test_backend_capabilities():
    backend, _ = _make_session()
    assert backend.mode == "vllm" and backend.backend == "vllm-native"
    assert backend.capabilities["partial_results"] is True
    assert backend.capabilities["word_timestamps"] is False
    assert backend.capabilities["speaker_labels"] is False


def test_backend_acquire_release_limits():
    backend = VllmStreamBackend(_MockEngine(), max_sessions=2)

    async def run():
        a = await backend.acquire()
        b = await backend.acquire()
        c = await backend.acquire()               # 超额
        return a, b, c

    a, b, c = asyncio.run(run())
    assert (a, b, c) == (True, True, False)
    backend.release(backend.create_session("x"))  # 释放一个名额
    assert asyncio.run(backend.acquire()) is True
    backend.shutdown()
