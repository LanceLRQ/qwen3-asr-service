"""app/engines/streaming_vad_engine.py 测试（mock self._model.generate）。

在线 VAD value 语义已对照已装 funasr==1.3.1 核实
（fsmn_vad_streaming/model.py:576 注释：[beg,-1]/[-1,end]/[beg,end]/[]）。
"""
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.engines.vad_engine import VADEngine
from app.engines.streaming_vad_engine import StreamingVADEngine


def _sve(gen_return):
    vad = VADEngine()
    vad._model = MagicMock()
    vad._model.generate.return_value = gen_return
    return StreamingVADEngine(vad), vad


def test_requires_loaded_model():
    vad = VADEngine()  # _model 为 None
    with pytest.raises(RuntimeError):
        StreamingVADEngine(vad)


def test_new_cache_is_empty_dict():
    sve, _ = _sve([])
    assert sve.new_cache() == {}


def test_shares_infer_lock_with_offline_engine():
    # 在线封装与离线 VADEngine 共用同一把推理锁（模型非线程安全）
    sve, vad = _sve([])
    assert sve._infer_lock is vad._infer_lock


def test_parse_start():
    sve, _ = _sve([{"value": [[100, -1]]}])
    assert sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False) == \
        [{"type": "start", "start": 100, "end": None}]


def test_parse_end():
    sve, _ = _sve([{"value": [[-1, 800]]}])
    assert sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False) == \
        [{"type": "end", "start": None, "end": 800}]


def test_parse_complete():
    sve, _ = _sve([{"value": [[100, 800]]}])
    assert sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False) == \
        [{"type": "complete", "start": 100, "end": 800}]


def test_parse_empty_value():
    sve, _ = _sve([{"value": []}])
    assert sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False) == []


def test_parse_no_result():
    sve, _ = _sve([])
    assert sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False) == []


def test_parse_multiple_events_in_order():
    sve, _ = _sve([{"value": [[100, -1], [-1, 800]]}])
    evs = sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False)
    assert [e["type"] for e in evs] == ["start", "end"]


def test_process_chunk_forwards_online_kwargs():
    sve, vad = _sve([{"value": []}])
    sve.process_chunk(np.zeros(10, dtype=np.float32), {"c": 1}, True)
    kwargs = vad._model.generate.call_args.kwargs
    assert kwargs["is_final"] is True
    assert kwargs["chunk_size"] == 200
    assert kwargs["fs"] == 16000
    assert kwargs["cache"] == {"c": 1}


def test_max_end_silence_forwarded_when_given():
    sve, vad = _sve([{"value": []}])
    sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False, max_end_silence_ms=500)
    assert vad._model.generate.call_args.kwargs["max_end_silence_time"] == 500


def test_max_end_silence_absent_when_none():
    sve, vad = _sve([{"value": []}])
    sve.process_chunk(np.zeros(10, dtype=np.float32), {}, False)
    assert "max_end_silence_time" not in vad._model.generate.call_args.kwargs
