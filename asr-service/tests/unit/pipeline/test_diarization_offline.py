"""离线说话人分离集成测试（fake speaker 引擎，不触真模型/音频）。

覆盖：_run_diarization 窗生成/抽稀、DiarizationResult 投票与衔接面、
run() 级别的启用/降级/关闭三态（容错对齐标点：失败不污染 segments）。
"""
import types

import numpy as np
import pytest

from app.engines.asr_result import ASRResult, BackendCapabilities
from app.pipeline.asr_pipeline import ASRPipeline
from app.runtime.speaker_cluster import DiarizationResult

DIM = 192


def _unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


class FakeSpeakerEngine:
    """t<5s 的窗 → 说话人 0，之后 → 说话人 1。"""

    def __init__(self):
        self.calls: list[list] = []

    def embed_windows(self, wav, windows):
        self.calls.append(list(windows))
        return np.stack([_unit(0 if st < 5.0 else 1) for st, _ in windows])


class BoomSpeakerEngine:
    def embed_windows(self, wav, windows):
        raise RuntimeError("boom")


@pytest.fixture
def fake_wav_io(monkeypatch):
    """sf.read 返回 12s 假音频；sf.write 不落盘。"""
    monkeypatch.setattr(
        "app.pipeline.asr_pipeline.sf.read",
        lambda p, dtype=None: (np.zeros(16000 * 12, dtype=np.float32), 16000),
    )
    monkeypatch.setattr("app.pipeline.asr_pipeline.sf.write", lambda *a, **k: None)


def _make_pipe(speaker, vad_segments):
    asr = types.SimpleNamespace(
        capabilities=BackendCapabilities(),
        transcribe=lambda audio_path, language: ASRResult("你好"),
    )
    vad = types.SimpleNamespace(detect=lambda p: vad_segments)
    return ASRPipeline(asr_engine=asr, vad_engine=vad,
                       punc_engine=None, speaker_engine=speaker)


# ─── DiarizationResult ───

def _result_two_speakers():
    windows = [(0.0, 1.5), (0.75, 2.25), (1.5, 3.0), (8.0, 9.5), (8.75, 10.25), (9.5, 11.0)]
    labels = np.array([0, 0, 0, 1, 1, 1])
    embs = np.stack([_unit(0)] * 3 + [_unit(1)] * 3)
    return DiarizationResult(windows, labels, embs)


def test_label_for_overlap_weighted_voting():
    diar = _result_two_speakers()
    assert diar.label_for(0.2, 2.8) == "A"
    assert diar.label_for(8.1, 10.9) == "B"


def test_label_for_cross_cluster_segment_majority():
    # 段横跨两簇：与 B 簇重叠更长 → 投给 B
    diar = _result_two_speakers()
    assert diar.label_for(2.0, 11.0) == "B"


def test_label_for_no_overlap_returns_none():
    diar = _result_two_speakers()
    assert diar.label_for(4.0, 7.0) is None


def test_labels_in_order_and_clusters():
    diar = _result_two_speakers()
    assert diar.labels_in_order == ["A", "B"]
    clusters = diar.clusters
    assert [c["label"] for c in clusters] == ["A", "B"]
    # dur_sec 为窗区间并集（滑窗重叠不重复计）：A=(0,3.0)，B=(8.0,11.0)
    assert clusters[0]["dur_sec"] == pytest.approx(3.0)
    assert clusters[1]["dur_sec"] == pytest.approx(3.0)
    for c in clusters:
        assert np.isclose(np.linalg.norm(c["centroid"]), 1.0, atol=1e-5)


def test_empty_result():
    diar = DiarizationResult([], [], [])
    assert diar.labels_in_order == []
    assert diar.clusters == []
    assert diar.label_for(0.0, 1.0) is None


# ─── _run_diarization ───

def test_run_diarization_windows_from_raw_vad(fake_wav_io):
    fake = FakeSpeakerEngine()
    pipe = _make_pipe(fake, [])
    diar = pipe._run_diarization("x.wav", [(0, 3000), (8000, 11000)])
    # 每段 3s → 各 3 窗；ms→s 换算正确
    assert fake.calls[0][0] == (0.0, 1.5)
    assert fake.calls[0][3] == (8.0, 9.5)
    assert len(fake.calls[0]) == 6
    assert diar.labels_in_order == ["A", "B"]


def test_run_diarization_dilution(fake_wav_io, monkeypatch):
    monkeypatch.setattr("app.config.SPEAKER_MAX_WINDOWS", 4)
    fake = FakeSpeakerEngine()
    pipe = _make_pipe(fake, [])
    pipe._run_diarization("x.wav", [(0, 3000), (8000, 11000)])
    # 6 窗 > 上限 4 → 每 2 取 1 → 3 窗
    assert len(fake.calls[0]) == 3


def test_run_diarization_no_windows():
    pipe = _make_pipe(FakeSpeakerEngine(), [])
    diar = pipe._run_diarization("x.wav", [(1000, 1000)])  # 零长段 → 0 窗
    assert diar.labels_in_order == []


# ─── run() 三态 ───

@pytest.fixture
def run_env(monkeypatch, tmp_path, fake_wav_io):
    monkeypatch.setattr("app.pipeline.asr_pipeline.convert_to_wav", lambda a, b: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.get_audio_duration", lambda p: 12.0)
    monkeypatch.setattr("app.pipeline.asr_pipeline.UPLOADS_DIR", str(tmp_path / "up"))
    monkeypatch.setattr("app.pipeline.asr_pipeline.AUDIO_CHUNKS_DIR", str(tmp_path / "chunks"))


def test_run_speaker_enabled(run_env, tmp_path):
    pipe = _make_pipe(FakeSpeakerEngine(), [(0, 3000), (8000, 11000)])
    progress: list[float] = []
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", progress_callback=progress.append)
    assert result["speakers"] == ["A", "B"]
    assert [seg["speaker"] for seg in result["segments"]] == ["A", "B"]
    assert 0.90 in progress and 0.95 in progress


def test_run_speaker_failure_degrades(run_env, tmp_path):
    pipe = _make_pipe(BoomSpeakerEngine(), [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    # 失败只丢标签：转写完整、无 speaker 字段、无 speakers 顶层键
    assert result["full_text"] == "你好你好"
    assert "speakers" not in result
    assert all("speaker" not in seg for seg in result["segments"])


def test_run_speaker_disabled_no_fields(run_env, tmp_path):
    pipe = _make_pipe(None, [(0, 3000), (8000, 11000)])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert "speakers" not in result
    assert all("speaker" not in seg for seg in result["segments"])


def test_run_no_vad_with_speaker_enabled(run_env, tmp_path):
    pipe = _make_pipe(FakeSpeakerEngine(), [])
    result = pipe.run(str(tmp_path / "a.mp3"), "t1")
    assert result["segments"] == []
    assert result["speakers"] == []
