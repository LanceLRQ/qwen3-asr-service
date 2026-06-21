"""POST /v2/audio/tag 端点测试（TestClient；未启用 503 / 启用返回事件段 + 场景时间线）。"""
from unittest.mock import MagicMock

import numpy as np

from app.api import routes
from app.engines.audio_tagger import TagResult


class FakeTagger:
    def predict_window(self, wav, sr, topk=5):
        return TagResult(top=[("Singing", 0.9)], scores={"Singing": 0.9})


def test_tag_audio_503_when_disabled(make_client):
    client = make_client(task_manager=MagicMock())
    r = client.post("/v2/audio/tag", files={"file": ("a.wav", b"abcdef", "audio/wav")})
    assert r.status_code == 503


def test_tag_audio_success(make_client, monkeypatch):
    client = make_client(task_manager=MagicMock())
    monkeypatch.setattr(routes, "_tagger", FakeTagger())
    monkeypatch.setattr(routes, "_scene_map", None)
    monkeypatch.setattr("app.pipeline.audio_preprocessor.convert_to_wav", lambda a, b: None)
    monkeypatch.setattr(
        "soundfile.read",
        lambda p, dtype=None: (np.ones(16000 * 2, dtype=np.float32) * 0.1, 16000),
    )
    r = client.post("/v2/audio/tag",
                    files={"file": ("a.wav", b"abcdef", "audio/wav")},
                    data={"with_scene": "true"})
    assert r.status_code == 200
    body = r.json()
    assert any(e["label"] == "Singing" for e in body["audio_events"])
    assert body["scene_timeline"][0]["label"] == "singing"


def test_tag_audio_bad_extension(make_client, monkeypatch):
    client = make_client(task_manager=MagicMock())
    monkeypatch.setattr(routes, "_tagger", FakeTagger())
    r = client.post("/v2/audio/tag", files={"file": ("a.xyz", b"abc", "application/octet-stream")})
    assert r.status_code == 400
