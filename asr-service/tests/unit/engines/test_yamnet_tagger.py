"""YAMNet 引擎测试：predict_window 推理流（mock interpreter）、标签、契约。

不依赖 tflite-runtime/ai-edge-litert——直接注入假 interpreter 验证封装逻辑。
"""
import numpy as np
import pytest

from app.config import YAMNET_LABELS_CSV
from app.engines.audio_tagger import AudioTaggerEngine, load_labels_csv
from app.engines.yamnet_tagger_engine import YamnetTaggerEngine, _find_tflite

N = 521


class FakeInterp:
    def __init__(self, scores):
        self._scores = np.asarray(scores, dtype=np.float32)
        self.set_len = None

    def resize_tensor_input(self, idx, shape, strict=False):
        self.set_len = shape[0]

    def allocate_tensors(self):
        pass

    def set_tensor(self, idx, x):
        self.fed = x

    def invoke(self):
        pass

    def get_tensor(self, idx):
        return self._scores


def _engine(scores):
    e = YamnetTaggerEngine()
    e._interp = FakeInterp(scores)
    e._in_idx = 0
    e._score_idx = 0
    e.labels = ["Speech", "Singing", "Music"] + [f"c{i}" for i in range(N - 3)]
    return e


def test_real_yamnet_labels_load():
    labels = load_labels_csv(YAMNET_LABELS_CSV)
    assert len(labels) == N
    assert labels[0] == "Speech" and "Singing" in labels


def test_predict_window_means_frames_and_orders_topk():
    scores = np.zeros((2, N), dtype=np.float32)
    scores[:, 1] = 0.9      # Singing
    scores[:, 2] = 0.4      # Music
    e = _engine(scores)
    tr = e.predict_window(np.zeros(16000, dtype=np.float32), 16000, topk=2)
    assert tr.top[0][0] == "Singing"
    assert tr.top[0][1] == pytest.approx(0.9)
    assert tr.scores["Singing"] == pytest.approx(0.9)
    assert tr.scores["Music"] == pytest.approx(0.4)


def test_predict_window_single_frame_1d_scores():
    scores = np.zeros(N, dtype=np.float32)
    scores[0] = 0.7         # Speech
    e = _engine(scores)
    tr = e.predict_window(np.zeros(16000, dtype=np.float32), 16000, topk=1)
    assert tr.top[0][0] == "Speech"


def test_predict_window_pads_short_input():
    e = _engine(np.zeros((1, N), dtype=np.float32))
    e.predict_window(np.zeros(8000, dtype=np.float32), 16000)   # < 1s
    assert e._interp.set_len >= 16000          # 已零填到至少 1s


def test_predict_window_requires_load():
    with pytest.raises(RuntimeError):
        YamnetTaggerEngine().predict_window(np.zeros(16000, dtype=np.float32), 16000)


def test_find_tflite(tmp_path):
    assert _find_tflite(str(tmp_path)) is None
    (tmp_path / "lite-model_yamnet_tflite_1.tflite").write_bytes(b"x")
    assert _find_tflite(str(tmp_path)).endswith(".tflite")


def test_find_tflite_skips_coral_prefers_standard(tmp_path):
    (tmp_path / "coral-model_yamnet_classification_coral_1.tflite").write_bytes(b"x")
    (tmp_path / "lite-model_yamnet_classification_tflite_1.tflite").write_bytes(b"x")
    (tmp_path / "lite-model_yamnet_tflite_1.tflite").write_bytes(b"x")
    # 排除 coral（EdgeTPU 需专用硬件），优先标准全模型
    assert _find_tflite(str(tmp_path)).endswith("lite-model_yamnet_tflite_1.tflite")


def test_find_tflite_all_coral_returns_none(tmp_path):
    (tmp_path / "coral-x_edgetpu.tflite").write_bytes(b"x")
    assert _find_tflite(str(tmp_path)) is None


def test_protocol_conformance():
    assert isinstance(YamnetTaggerEngine(), AudioTaggerEngine)
