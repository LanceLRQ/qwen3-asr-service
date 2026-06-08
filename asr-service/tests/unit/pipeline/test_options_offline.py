"""离线按请求覆盖（D5/D6）：降级开关 + warnings 软提示（fake 引擎，不触模型）。"""
import types

import numpy as np
import pytest

from app.pipeline.asr_pipeline import ASRPipeline

DIM = 192


class FakeSpeakerEngine:
    def embed_windows(self, wav, windows):
        v = np.zeros((len(windows), DIM), dtype=np.float32)
        v[:, 0] = 1.0
        return v


class FakePunc:
    def restore(self, text):
        return text + "。"


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.pipeline.asr_pipeline.sf.read",
        lambda p, dtype=None: (np.zeros(16000 * 12, dtype=np.float32), 16000),
    )
    monkeypatch.setattr("app.pipeline.asr_pipeline.sf.write", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.convert_to_wav", lambda a, b: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.get_audio_duration", lambda p: 12.0)
    monkeypatch.setattr("app.pipeline.asr_pipeline.UPLOADS_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.pipeline.asr_pipeline.AUDIO_CHUNKS_DIR", str(tmp_path / "chunks"))
    return tmp_path


def _make_pipe(*, speaker=None, punc=None):
    asr = types.SimpleNamespace(align_enabled=False,
                                transcribe=lambda audio_path, language: "你好")
    vad = types.SimpleNamespace(detect=lambda p: [(0, 3000), (8000, 11000)])
    return ASRPipeline(asr_engine=asr, vad_engine=vad, punc_engine=punc,
                       speaker_engine=speaker, speaker_service=None)


def test_diarize_off_skips_speakers(run_env, tmp_path):
    pipe = _make_pipe(speaker=FakeSpeakerEngine())
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", options={"diarize": False})
    assert "speakers" not in result
    assert all("speaker" not in seg for seg in result["segments"])


def test_diarize_on_by_default(run_env, tmp_path):
    pipe = _make_pipe(speaker=FakeSpeakerEngine())
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert "speakers" in result


def test_with_punc_false_skips_punctuation(run_env, tmp_path):
    pipe = _make_pipe(punc=FakePunc())
    off = pipe.run(str(tmp_path / "a.mp3"), "t1", options={"with_punc": False})
    on = pipe.run(str(tmp_path / "a.mp3"), "t2")
    assert all(not seg["text"].endswith("。") for seg in off["segments"])
    assert all(seg["text"].endswith("。") for seg in on["segments"])


def test_warnings_for_unavailable_features(run_env, tmp_path):
    pipe = _make_pipe()      # 无 speaker / punc / service
    result = pipe.run(
        str(tmp_path / "a.mp3"), "t1", identify_speakers=True,
        options={"diarize": True, "with_punc": True, "speaker_id_threshold": 0.5},
    )
    w = result["warnings"]
    assert "diarize" in w
    assert "with_punc" in w
    assert "identify_speakers" in w
    assert "speaker_id_threshold/margin" in w


def test_no_warnings_key_when_clean(run_env, tmp_path):
    pipe = _make_pipe(punc=FakePunc())
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", options={"with_punc": False})
    assert "warnings" not in result
