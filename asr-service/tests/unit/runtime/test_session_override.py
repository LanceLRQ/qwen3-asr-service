"""StreamSession 客户端按会话覆盖 + warnings 软提示（D1–D5 流式部分）。"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from app.runtime.stream_session import StreamSession


def _make_session(*, speaker=None, speaker_service=None, punc=None, enable_words=False):
    return StreamSession(
        "sid", MagicMock(), MagicMock(), punc,
        ThreadPoolExecutor(max_workers=1), asyncio.Semaphore(1),
        speaker=speaker, speaker_service=speaker_service, enable_words=enable_words,
    )


# ─── 覆盖字段生效 ───

def test_override_speaker_and_segment_fields():
    sess = _make_session(speaker=MagicMock())
    sess.configure({
        "speaker_threshold": 0.6, "speaker_min_seg_ms": 800, "speaker_max": 5,
        "speaker_id_threshold": 0.5, "speaker_id_margin": 0.2,
        "max_end_silence_ms": 500, "max_segment_sec": 20,
    })
    assert sess._spk_threshold == 0.6
    assert sess._spk_min_seg_ms == 800
    assert sess._spk_max == 5
    assert sess._spk_id_threshold == 0.5 and sess._spk_id_margin == 0.2
    assert sess._max_end_silence_ms == 500
    assert sess._max_segment_sec == 20


def test_defaults_when_absent():
    sess = _make_session(speaker=MagicMock())
    sess.configure({})
    import app.config as cfg
    assert sess._spk_threshold == cfg.SPEAKER_THRESHOLD
    assert sess._max_end_silence_ms == cfg.VAD_MAX_SILENCE
    assert sess._with_punc and sess._with_words and sess._with_diarize


def test_diarize_off_skips_cluster():
    sess = _make_session(speaker=MagicMock())
    sess.configure({"diarize": False})
    assert sess._with_diarize is False and sess._spk_cluster is None


def test_diarize_on_builds_cluster():
    sess = _make_session(speaker=MagicMock())
    sess.configure({})
    assert sess._spk_cluster is not None


# ─── 范围钳制：越界报错 ───

@pytest.mark.parametrize("msg", [
    {"speaker_threshold": 1.5},      # >0.9
    {"speaker_threshold": 0.1},      # <0.2
    {"speaker_max": 100},            # >50
    {"max_end_silence_ms": 5000},    # >2000
    {"max_segment_sec": 0},          # <1
    {"speaker_id_threshold": 1.5},   # >1.0
    {"with_punc": "yes"},            # 非布尔
])
def test_invalid_override_raises(msg):
    sess = _make_session(speaker=MagicMock())
    with pytest.raises(ValueError):
        sess.configure(msg)


# ─── warnings 软提示：合法但功能未启用 ───

def test_warns_when_feature_not_enabled():
    sess = _make_session()           # 无 speaker / service / punc，enable_words=False
    warnings = sess.configure({
        "diarize": True, "speaker_threshold": 0.6,
        "identify_speakers": True, "speaker_id_threshold": 0.5,
        "with_words": True, "with_punc": True,
    })
    assert "diarize" in warnings
    assert "speaker_threshold" in warnings
    assert "identify_speakers" in warnings
    assert "speaker_id_threshold" in warnings
    assert "with_words" in warnings
    assert "with_punc" in warnings


def test_no_warnings_when_features_available():
    sess = _make_session(speaker=MagicMock(), speaker_service=MagicMock(),
                         punc=MagicMock(), enable_words=True)
    warnings = sess.configure({
        "diarize": True, "speaker_threshold": 0.6,
        "identify_speakers": True, "with_words": True, "with_punc": True,
    })
    assert warnings == []


def test_disable_toggle_never_warns():
    # 关闭类请求（with_punc=false）即使功能未加载也不应告警
    sess = _make_session()
    warnings = sess.configure({"with_punc": False, "with_words": False, "diarize": False})
    assert warnings == []
    assert sess._with_punc is False and sess._with_words is False and sess._with_diarize is False
