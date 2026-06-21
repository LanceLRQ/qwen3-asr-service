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
