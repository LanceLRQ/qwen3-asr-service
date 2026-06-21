"""音频打标引擎无关层测试：标签加载、top-k 排序、接口契约（不触模型/重依赖）。"""
import numpy as np
import pytest

from app.config import AUDIOSET_LABELS_CSV
from app.engines.audio_tagger import (
    AudioTaggerEngine,
    TagResult,
    load_labels_csv,
    topk_from_probs,
)


def test_load_real_audioset_labels():
    labels = load_labels_csv(AUDIOSET_LABELS_CSV)
    assert len(labels) == 527
    assert labels[0] == "Speech"
    assert "Singing" in labels and "Music" in labels


def test_load_labels_csv_rejects_noncontiguous(tmp_path):
    p = tmp_path / "x.csv"
    p.write_text('index,mid,display_name\n0,/m/a,"A"\n2,/m/c,"C"\n', encoding="utf-8")
    with pytest.raises(ValueError):
        load_labels_csv(str(p))


def test_topk_from_probs_orders_desc():
    probs = np.array([0.1, 0.9, 0.3, 0.7])
    top = topk_from_probs(probs, ["a", "b", "c", "d"], k=2)
    assert top == [("b", pytest.approx(0.9)), ("d", pytest.approx(0.7))]


def test_topk_from_probs_clamps_k():
    assert len(topk_from_probs(np.array([0.2, 0.8]), ["a", "b"], k=10)) == 2


class _FakeTagger:
    BACKEND = "panns"
    labels = ["Speech", "Singing"]

    def load(self):
        ...

    def predict_window(self, wav, sr, topk=5):
        return TagResult(top=[("Singing", 0.8)], scores={"Singing": 0.8})


def test_protocol_conformance():
    assert isinstance(_FakeTagger(), AudioTaggerEngine)


def test_panns_predict_window_short_clip_no_crash():
    """末尾不足整窗的短尾片喂 CNN14 不得崩溃（补零到模型最小采样后推理）。"""
    pytest.importorskip("torch")
    pytest.importorskip("torchlibrosa")
    from app.engines.panns import Cnn14
    from app.engines.panns_tagger_engine import PANNsTaggerEngine, _VARIANT_PARAMS

    eng = PANNsTaggerEngine(variant="16k", device="cpu")
    eng._model = Cnn14(classes_num=527, **_VARIANT_PARAMS["16k"]).eval()
    eng.labels = [str(i) for i in range(527)]
    for n in (1, 100, 4000):                       # 均短于 _min_samples，旧实现会 RuntimeError
        tr = eng.predict_window(np.zeros(n, dtype=np.float32), 16000, topk=5)
        assert len(tr.scores) == 527 and len(tr.top) == 5
