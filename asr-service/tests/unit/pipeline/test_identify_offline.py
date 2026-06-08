"""离线声纹识别联动测试（fake speaker 引擎 + fake SpeakerService，不触模型/库）。

覆盖：identify_speakers 联动字段（speaker_name / speakers 升级为映射表）、
未命中保留匿名、模块关闭/分离失败字段不出现、segment 级不带 speaker_id、
clusters 衔接面字段（label/centroid/dur_sec）。
"""
import types

import numpy as np
import pytest

from app.pipeline.asr_pipeline import ASRPipeline

DIM = 192


def _unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


class FakeSpeakerEngine:
    """t<5s 的窗 → 说话人 0，之后 → 说话人 1。"""

    def embed_windows(self, wav, windows):
        return np.stack([_unit(0 if st < 5.0 else 1) for st, _ in windows])


class BoomSpeakerEngine:
    def embed_windows(self, wav, windows):
        raise RuntimeError("boom")


class FakeSpeakerService:
    """A 簇命中张三，B 簇自动登记说话人_01；记录收到的 clusters。"""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def map_and_enroll_clusters(self, clusters, *, id_threshold=None, id_margin=None):
        self.calls.append(clusters)
        out = []
        for c in clusters:
            if c["label"] == "A":
                out.append({"label": "A", "speaker_id": "a" * 32,
                            "name": "张三", "score": 0.62})
            elif c["label"] == "B":
                out.append({"label": "B", "speaker_id": "b" * 32,
                            "name": "说话人_01", "score": None, "auto_enrolled": True})
            else:
                out.append({"label": c["label"], "speaker_id": None,
                            "name": None, "score": None})
        return out


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


def _make_pipe(speaker_engine, speaker_service):
    asr = types.SimpleNamespace(align_enabled=False,
                                transcribe=lambda audio_path, language: "你好")
    vad = types.SimpleNamespace(detect=lambda p: [(0, 3000), (8000, 11000)])
    return ASRPipeline(asr_engine=asr, vad_engine=vad, punc_engine=None,
                       speaker_engine=speaker_engine, speaker_service=speaker_service)


def test_identify_attaches_names_and_mapping(run_env, tmp_path):
    service = FakeSpeakerService()
    pipe = _make_pipe(FakeSpeakerEngine(), service)
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=True)

    assert [seg["speaker"] for seg in result["segments"]] == ["A", "B"]
    assert [seg["speaker_name"] for seg in result["segments"]] == ["张三", "说话人_01"]
    # segment 级不带 speaker_id（id 仅在顶层 speakers 映射表）
    assert all("speaker_id" not in seg for seg in result["segments"])
    speakers = result["speakers"]
    assert speakers[0] == {"label": "A", "speaker_id": "a" * 32,
                           "name": "张三", "score": 0.62}
    assert speakers[1]["auto_enrolled"] is True


def test_identify_clusters_interface_fields(run_env, tmp_path):
    """S3→V3 衔接面：service 收到 [{"label","centroid","dur_sec"}]。"""
    service = FakeSpeakerService()
    pipe = _make_pipe(FakeSpeakerEngine(), service)
    pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=True)
    clusters = service.calls[0]
    assert [c["label"] for c in clusters] == ["A", "B"]
    for c in clusters:
        assert c["centroid"].shape == (DIM,)
        assert c["dur_sec"] == pytest.approx(3.0)   # 每段 3s 窗并集


def test_identify_miss_keeps_anonymous(run_env, tmp_path):
    class AllMissService:
        def map_and_enroll_clusters(self, clusters, *, id_threshold=None, id_margin=None):
            return [{"label": c["label"], "speaker_id": None, "name": None,
                     "score": None} for c in clusters]

    pipe = _make_pipe(FakeSpeakerEngine(), AllMissService())
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=True)
    assert all("speaker_name" not in seg for seg in result["segments"])
    assert [seg["speaker"] for seg in result["segments"]] == ["A", "B"]   # 匿名保留
    assert result["speakers"][0]["speaker_id"] is None


def test_identify_flag_off_keeps_s3_shape(run_env, tmp_path):
    service = FakeSpeakerService()
    pipe = _make_pipe(FakeSpeakerEngine(), service)
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=False)
    assert result["speakers"] == ["A", "B"]            # 纯标签列表（S3 形态）
    assert all("speaker_name" not in seg for seg in result["segments"])
    assert service.calls == []


def test_identify_without_service_keeps_s3_shape(run_env, tmp_path):
    pipe = _make_pipe(FakeSpeakerEngine(), None)
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=True)
    assert result["speakers"] == ["A", "B"]
    assert all("speaker_name" not in seg for seg in result["segments"])


def test_identify_skipped_when_diarization_fails(run_env, tmp_path):
    service = FakeSpeakerService()
    pipe = _make_pipe(BoomSpeakerEngine(), service)
    result = pipe.run(str(tmp_path / "a.mp3"), "t1", identify_speakers=True)
    assert "speakers" not in result                    # 分离失败 → 降级一致
    assert service.calls == []                         # 不调用声纹服务
