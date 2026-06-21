"""流式场景标注测试（mock svad/asr + fake tagger，真实 executor/Semaphore）。

验证：满推理窗即产 scene 信封、迟滞未达不产、关闭/无引擎不产、backend 能力位。
异步用例依赖 pytest-asyncio（asyncio_mode=auto）。
"""
import asyncio
import types
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import numpy as np

from app.engines.audio_tagger import TagResult
from app.engines.vad_engine import VADEngine
from app.runtime.stream_session import StreamSession, VadOfflineBackend


class FakeSVAD:
    def new_cache(self):
        return {}

    def process_chunk(self, arr, cache, is_final, max_end_silence_ms=None):
        return []                          # 不产 VAD 事件，专测 scene 旁路


class SceneTagger:
    def __init__(self, label="Singing"):
        self.label = label
        self.calls = 0

    def predict_window(self, wav, sr, topk=5):
        self.calls += 1
        return TagResult(top=[(self.label, 0.9)], scores={self.label: 0.9})


def _pcm_ms(ms, sr=16000, amp=3000):
    """非静音 PCM16（amp/32768≈-20dBFS，> silence 门）。"""
    return (np.ones(int(ms * sr / 1000), dtype="<i2") * amp).tobytes()


def _session(tagger, *, scene_enable=True, enter=0.0, exit=0.0):
    asr = MagicMock()
    asr.transcribe_array.return_value = [types.SimpleNamespace(text="hi")]
    ex = ThreadPoolExecutor(max_workers=2)
    s = StreamSession("sid", FakeSVAD(), asr, None, ex, asyncio.Semaphore(1),
                      tagger=tagger, scene_enable=scene_enable, scene_enter_sec=enter,
                      scene_exit_sec=exit, scene_silence_dbfs=-50.0, tag_interval_ms=960)
    s.configure({"audio_fs": 16000})
    return s, ex


async def _collect(agen):
    return [m async for m in agen]


async def test_stream_emits_scene_on_confirm():
    s, ex = _session(SceneTagger("Singing"), enter=0.0)
    try:
        msgs = await _collect(s.feed_audio(_pcm_ms(1000)))   # >960ms → 一次打标
        scenes = [m for m in msgs if m["type"] == "scene"]
        assert scenes and scenes[0]["label"] == "singing"
        assert scenes[0]["scores"].get("singing") == 0.9
        assert "since" in scenes[0]
    finally:
        ex.shutdown()


async def test_stream_hysteresis_holds_before_dwell():
    s, ex = _session(SceneTagger("Singing"), enter=10.0)
    try:
        msgs = await _collect(s.feed_audio(_pcm_ms(1000)))
        assert [m for m in msgs if m["type"] == "scene"] == []   # 未达 enter dwell
    finally:
        ex.shutdown()


async def test_stream_silence_scene_on_quiet_audio():
    s, ex = _session(SceneTagger("Singing"), enter=0.0)
    try:
        # 全零（静音）→ 即便模型给 Singing，能量门判 silence
        msgs = await _collect(s.feed_audio((np.zeros(16000, dtype="<i2")).tobytes()))
        scenes = [m for m in msgs if m["type"] == "scene"]
        assert scenes and scenes[0]["label"] == "silence"
    finally:
        ex.shutdown()


async def test_stream_no_scene_when_disabled():
    s, ex = _session(SceneTagger(), scene_enable=False, enter=0.0)
    try:
        assert [m for m in await _collect(s.feed_audio(_pcm_ms(1000)))
                if m["type"] == "scene"] == []
    finally:
        ex.shutdown()


async def test_stream_no_scene_without_tagger():
    s, ex = _session(None, enter=0.0)
    try:
        assert [m for m in await _collect(s.feed_audio(_pcm_ms(1000)))
                if m["type"] == "scene"] == []
    finally:
        ex.shutdown()


def test_backend_capability_scene_flag():
    vad = VADEngine()
    vad._model = MagicMock()
    asr = MagicMock()
    asr.align_enabled = False
    on = VadOfflineBackend(asr, vad, None, tagger=SceneTagger())
    off = VadOfflineBackend(asr, vad, None, tagger=None)
    try:
        assert on.capabilities["scene"] is True
        assert off.capabilities["scene"] is False
    finally:
        on.shutdown()
        off.shutdown()
