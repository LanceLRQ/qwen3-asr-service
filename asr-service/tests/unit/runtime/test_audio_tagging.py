"""共享窗级打标助手测试（tag_windows / scene_timeline / tag_wav，fake tagger）。"""
import numpy as np

from app.engines.audio_tagger import TagResult
from app.runtime import audio_tagging


class FakeTagger:
    def __init__(self, label="Singing"):
        self.label = label

    def predict_window(self, wav, sr, topk=5):
        return TagResult(top=[(self.label, 0.9)], scores={self.label: 0.9})


def test_tag_windows_count_and_fields():
    wav = np.ones(16000 * 2, dtype=np.float32) * 0.1   # 2s
    w = audio_tagging.tag_windows(FakeTagger(), wav, 16000, 960, 5)
    assert len(w) == 3            # 32000 / 15360 → 3 窗（含末尾残窗）
    s, e, top, scores, dbfs = w[0]
    assert top[0][0] == "Singing" and "Singing" in scores
    assert e > s and dbfs > -50


def test_scene_timeline_run_length_merge():
    wav = np.ones(16000 * 3, dtype=np.float32) * 0.1
    w = audio_tagging.tag_windows(FakeTagger("Singing"), wav, 16000, 960, 5)
    tl = audio_tagging.scene_timeline(w)
    assert len(tl) == 1 and tl[0]["label"] == "singing"
    assert tl[0]["start_ms"] == 0


def test_tag_wav_structure_and_events():
    wav = np.ones(16000 * 2, dtype=np.float32) * 0.1
    out = audio_tagging.tag_wav(FakeTagger(), wav, 16000, interval_ms=960, topk=5,
                                scene_enable=True)
    assert any(e["label"] == "Singing" for e in out["audio_events"])
    assert out["scene_timeline"] and out["scene_timeline"][0]["label"] == "singing"


def test_tag_wav_scene_disabled_omits_timeline():
    wav = np.ones(16000, dtype=np.float32) * 0.1
    out = audio_tagging.tag_wav(FakeTagger(), wav, 16000, interval_ms=960, topk=5,
                                scene_enable=False)
    assert out.get("scene_timeline") is None
    assert "audio_events" in out


def test_tag_wav_empty_input():
    out = audio_tagging.tag_wav(FakeTagger(), np.zeros(0, dtype=np.float32), 16000,
                                interval_ms=960, topk=5, scene_enable=True)
    assert out["audio_events"] == []
