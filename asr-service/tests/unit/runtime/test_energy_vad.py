"""离线能量 VAD 单元测试（依赖中性：numpy + noise_gate，无 torch/funasr/soundfile）。

覆盖：语音/静音分段、空输入、全静音、短段过滤、尾静音并段。
"""
import numpy as np

from app.runtime.energy_vad import EnergyVAD


def _tone(sr, start_s, end_s, total_s, amp=0.5, freq=200):
    """构造 total_s 秒静音底，[start_s,end_s) 段填正弦人声替身。"""
    wav = np.zeros(int(sr * total_s), dtype="float32")
    i0, i1 = int(sr * start_s), int(sr * end_s)
    t = np.arange(i1 - i0) / sr
    wav[i0:i1] = (amp * np.sin(2 * np.pi * freq * t)).astype("float32")
    return wav


def test_detect_single_speech_span():
    sr = 16000
    vad = EnergyVAD(energy_floor_dbfs=-45.0, frame_ms=30, min_speech_ms=60, end_silence_ms=150)
    spans = vad.detect_array(_tone(sr, 1.0, 2.0, 3.0), sr)
    assert len(spans) == 1
    s, e = spans[0]
    assert 900 <= s <= 1100        # ~1s 起
    assert 1900 <= e <= 2100       # ~2s 止


def test_detect_two_spans_split_by_long_silence():
    sr = 16000
    wav = _tone(sr, 0.5, 1.0, 4.0) + _tone(sr, 2.5, 3.5, 4.0)
    vad = EnergyVAD(energy_floor_dbfs=-45.0, frame_ms=30, min_speech_ms=60, end_silence_ms=300)
    spans = vad.detect_array(wav, sr)
    assert len(spans) == 2
    assert spans[0][1] < spans[1][0]   # 时序不交叠


def test_detect_empty():
    assert EnergyVAD().detect_array(np.zeros(0, dtype="float32")) == []


def test_detect_all_silence():
    assert EnergyVAD().detect_array(np.zeros(16000, dtype="float32")) == []


def test_detect_filters_too_short():
    """短于 min_speech_ms 的语音段被过滤。"""
    sr = 16000
    vad = EnergyVAD(energy_floor_dbfs=-45.0, frame_ms=30, min_speech_ms=500, end_silence_ms=150)
    spans = vad.detect_array(_tone(sr, 1.0, 1.1, 3.0), sr)   # 仅 100ms 语音
    assert spans == []
