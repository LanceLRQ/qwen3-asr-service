"""离线音频标注集成测试（fake tagger，不触真模型/音频）。

覆盖：run() 级别启用/scene 关闭/失败降级/关闭四态——容错对齐说话人分离：
打标失败只丢标签不破坏转写；关闭时结果与现状逐字节一致。
"""
import types

import numpy as np
import pytest

from app.engines.audio_tagger import TagResult
from app.pipeline.asr_pipeline import ASRPipeline


class FakeTagger:
    """恒返回高 Singing 概率：scene→singing，top 含 Singing 触发事件段。"""

    def __init__(self):
        self.calls = 0

    def predict_window(self, wav, sr, topk=5):
        self.calls += 1
        scores = {"Speech": 0.05, "Singing": 0.9, "Music": 0.2}
        top = [("Singing", 0.9), ("Music", 0.2), ("Speech", 0.05)][:topk]
        return TagResult(top=top, scores=scores)


class BoomTagger:
    def predict_window(self, wav, sr, topk=5):
        raise RuntimeError("boom")


def _patch_io(monkeypatch, tmp_path, audio):
    monkeypatch.setattr("app.pipeline.asr_pipeline.sf.read",
                        lambda p, dtype=None: (audio.copy(), 16000))
    monkeypatch.setattr("app.pipeline.asr_pipeline.sf.write", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.convert_to_wav", lambda a, b: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.get_audio_duration", lambda p: 12.0)
    monkeypatch.setattr("app.pipeline.asr_pipeline.UPLOADS_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.pipeline.asr_pipeline.AUDIO_CHUNKS_DIR", str(tmp_path / "chunks"))


@pytest.fixture
def noise_env(monkeypatch, tmp_path):
    """12s 噪声（非静音，dBFS≈-20）。"""
    rng = np.random.default_rng(0)
    _patch_io(monkeypatch, tmp_path, rng.standard_normal(16000 * 12).astype(np.float32) * 0.1)


def _make_pipe(tagger, vad_segments):
    asr = types.SimpleNamespace(align_enabled=False,
                                transcribe=lambda audio_path, language: "你好")
    vad = types.SimpleNamespace(detect=lambda p: vad_segments)
    return ASRPipeline(asr_engine=asr, vad_engine=vad, punc_engine=None, tagger=tagger)


def test_run_tagging_enabled(noise_env, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCENE_ENABLE", True)
    pipe = _make_pipe(FakeTagger(), [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert result["audio_events"]
    assert any(e["label"] == "Singing" for e in result["audio_events"])
    assert all(seg.get("scene") == "singing" for seg in result["segments"])


def test_run_tagging_scene_disabled_keeps_events(noise_env, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.SCENE_ENABLE", False)
    pipe = _make_pipe(FakeTagger(), [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert "audio_events" in result                                  # 通用标签仍给
    assert all("scene" not in seg for seg in result["segments"])     # 不出场景视图


def test_run_tagging_failure_degrades(noise_env, tmp_path):
    pipe = _make_pipe(BoomTagger(), [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert result["full_text"] == "你好你好"
    assert "audio_events" not in result
    assert all("scene" not in seg for seg in result["segments"])


def test_run_tagging_disabled_no_fields(noise_env, tmp_path):
    pipe = _make_pipe(None, [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert "audio_events" not in result
    assert all("scene" not in seg for seg in result["segments"])


def test_run_tagging_silence_scene(monkeypatch, tmp_path):
    # 全零音频 → 每窗 dBFS < silence_dbfs → scene=silence（即便模型给高 Singing）
    _patch_io(monkeypatch, tmp_path, np.zeros(16000 * 12, dtype=np.float32))
    monkeypatch.setattr("app.config.SCENE_ENABLE", True)
    pipe = _make_pipe(FakeTagger(), [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert all(seg.get("scene") == "silence" for seg in result["segments"])
