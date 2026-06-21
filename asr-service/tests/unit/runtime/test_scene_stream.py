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
    s, ex = _session(SceneTagger("Dog"), enter=0.0)
    try:
        # 全零（静音）+ 无内容信号（Dog 非内容桶）→ 能量门判 silence
        msgs = await _collect(s.feed_audio((np.zeros(16000, dtype="<i2")).tobytes()))
        scenes = [m for m in msgs if m["type"] == "scene"]
        assert scenes and scenes[0]["label"] == "silence"
    finally:
        ex.shutdown()


async def test_final_attaches_scene_from_window_log():
    # 喂音频累积窗级日志后，final 段聚合出 scene + scene_scores（per-seg，同离线）
    s, ex = _session(SceneTagger("Singing"), enter=0.0)
    try:
        await _collect(s.feed_audio(_pcm_ms(2000)))
        assert s._scene_window_log                       # 已留存窗级分数
        msg = {}
        s._attach_scene(msg, 0, 2000, "在唱歌")
        assert msg["scene"] == "singing" and msg["scene_scores"]
    finally:
        ex.shutdown()


async def test_final_scene_lyrics_recovers_singing_from_music():
    # 带伴奏歌声：模型给 Music、无 Singing，靠歌词文本救回 singing
    s, ex = _session(SceneTagger("Music"), enter=0.0)
    try:
        await _collect(s.feed_audio(_pcm_ms(2000)))
        msg = {}
        s._attach_scene(msg, 0, 2000, "流浪日子你再伴随")     # 有歌词 → singing
        assert msg["scene"] == "singing"
    finally:
        ex.shutdown()
    s2, ex2 = _session(SceneTagger("Music"), enter=0.0)       # 无文本(纯器乐) → 保持 music
    try:
        await _collect(s2.feed_audio(_pcm_ms(2000)))
        msg2 = {}
        s2._attach_scene(msg2, 0, 2000, "")
        assert msg2["scene"] == "music"
    finally:
        ex2.shutdown()


async def test_start_scene_preset_overrides_session():
    # start 消息 scene_preset 按会话覆盖判定权重（music 预设关人声优先）
    s, ex = _session(SceneTagger("Speech"), enter=0.0)
    try:
        s.configure({"audio_fs": 16000, "scene_preset": "music"})
        assert s._scene_vocal_priority is False
        s.configure({"audio_fs": 16000, "scene_preset": "live"})
        assert s._scene_vocal_priority is True and s._scene_singing_bias > 0
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
