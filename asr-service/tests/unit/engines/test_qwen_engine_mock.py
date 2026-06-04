"""app/engines/qwen_asr_engine.py 测试（mock self._model / from_pretrained，不加载真实模型）。

行为依源码确认（qwen_asr_engine.py:10/24/70/91/119/129）。
"""
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.engines.qwen_asr_engine import QwenASREngine


def test_transcribe_requires_loaded():
    with pytest.raises(RuntimeError):
        QwenASREngine().transcribe("x.wav")


def test_batch_transcribe_requires_loaded():
    with pytest.raises(RuntimeError):
        QwenASREngine().batch_transcribe(["x.wav"])


def test_align_enabled_property():
    assert QwenASREngine(enable_align=True).align_enabled is True
    assert QwenASREngine(enable_align=False).align_enabled is False


def test_is_loaded_property():
    eng = QwenASREngine()
    assert eng.is_loaded is False
    eng._model = MagicMock()
    assert eng.is_loaded is True


def test_transcribe_passes_params_with_align():
    eng = QwenASREngine(enable_align=True)
    eng._model = MagicMock()
    eng._model.transcribe.return_value = ["r"]
    out = eng.transcribe("x.wav", language="zh")
    assert out == ["r"]
    eng._model.transcribe.assert_called_once_with(
        audio="x.wav", language="zh", return_time_stamps=True,
    )


def test_transcribe_align_false_sets_return_time_stamps_false():
    eng = QwenASREngine(enable_align=False)
    eng._model = MagicMock()
    eng._model.transcribe.return_value = []
    eng.transcribe("x.wav")
    eng._model.transcribe.assert_called_once_with(
        audio="x.wav", language=None, return_time_stamps=False,
    )


def test_batch_transcribe_empty_returns_empty():
    eng = QwenASREngine()
    eng._model = MagicMock()
    assert eng.batch_transcribe([]) == []
    eng._model.transcribe.assert_not_called()


def test_batch_transcribe_passes_params():
    eng = QwenASREngine(enable_align=True)
    eng._model = MagicMock()
    eng._model.transcribe.return_value = ["a", "b"]
    out = eng.batch_transcribe(["1.wav", "2.wav"], language="en")
    assert out == ["a", "b"]
    eng._model.transcribe.assert_called_once_with(
        audio=["1.wav", "2.wav"], language="en", return_time_stamps=True,
    )


# ─── T05: transcribe_array（内存数组解码）───

def test_transcribe_array_requires_loaded():
    with pytest.raises(RuntimeError):
        QwenASREngine().transcribe_array(np.zeros(10, dtype=np.float32))


def test_transcribe_array_passes_tuple_and_align():
    eng = QwenASREngine(enable_align=True)
    eng._model = MagicMock()
    eng._model.transcribe.return_value = ["r"]
    audio = np.zeros(8, dtype=np.float32)

    out = eng.transcribe_array(audio, sr=16000, language="zh")

    assert out == ["r"]
    kwargs = eng._model.transcribe.call_args.kwargs
    # audio 以 (ndarray, sr) 元组传入
    assert kwargs["audio"][0] is audio
    assert kwargs["audio"][1] == 16000
    assert kwargs["language"] == "zh"
    assert kwargs["return_time_stamps"] is True


def test_transcribe_array_align_false():
    eng = QwenASREngine(enable_align=False)
    eng._model = MagicMock()
    eng._model.transcribe.return_value = []
    eng.transcribe_array(np.zeros(4, dtype=np.float32), sr=8000)
    kwargs = eng._model.transcribe.call_args.kwargs
    assert kwargs["audio"][1] == 8000
    assert kwargs["return_time_stamps"] is False


def test_inference_serialized_across_threads():
    # 推理锁串行化并发调用（Qwen3ASRModel.rope_deltas 实例状态非线程安全），
    # 覆盖离线 transcribe 与流式 transcribe_array 混合并发
    eng = QwenASREngine(enable_align=False)
    eng._model = MagicMock()
    active, max_active = 0, 0
    stat_lock = threading.Lock()

    def fake_transcribe(**kwargs):
        nonlocal active, max_active
        with stat_lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with stat_lock:
            active -= 1
        return []

    eng._model.transcribe.side_effect = fake_transcribe
    threads = [
        threading.Thread(target=eng.transcribe, args=("x.wav",)),
        threading.Thread(target=eng.transcribe_array, args=(np.zeros(8, dtype=np.float32),)),
        threading.Thread(target=eng.batch_transcribe, args=(["a.wav", "b.wav"],)),
        threading.Thread(target=eng.transcribe_array, args=(np.zeros(8, dtype=np.float32),)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert max_active == 1                  # 任意时刻仅一个线程在推理
    assert eng._model.transcribe.call_count == 4


def test_load_assembles_kwargs_and_sets_model(mocker):
    # mock 下载与模型构造，验证装配逻辑（不触网、不加载真实权重）
    mocker.patch("app.engines.qwen_asr_engine.ensure_model")
    import qwen_asr
    sentinel = object()
    fp = mocker.patch.object(qwen_asr.Qwen3ASRModel, "from_pretrained", return_value=sentinel)

    eng = QwenASREngine(model_size="0.6b", device="cpu", enable_align=False)
    eng.load()

    assert eng._model is sentinel
    assert eng.is_loaded is True
    kwargs = fp.call_args.kwargs
    assert kwargs["device_map"] == "cpu"
    from app.config import MODEL_LOCAL_MAP
    assert kwargs["pretrained_model_name_or_path"] == MODEL_LOCAL_MAP["asr_0.6b"]
    # enable_align=False 不应注入 forced_aligner
    assert "forced_aligner" not in kwargs
