"""app/api/compat/mappers.py 测试（纯函数，全分支）。

覆盖：json/text/verbose_json/srt/vtt；占位字段；duration=max(end)；word 粒度开关；
空 segments；时间戳格式（秒 → HH:MM:SS,mmm / .mmm，跨分跨时、四舍五入边界）。
"""
import pytest

from app.api.compat.mappers import (
    _fmt_timestamp,
    result_to_openai,
    result_to_openai_sse_events,
    result_to_srt,
    result_to_vtt,
    to_engine_language,
)

RESULT = {
    "segments": [
        {"start": 0.0, "end": 3.2, "text": "你好",
         "words": [{"text": "你", "start": 0.0, "end": 0.2},
                   {"text": "好", "start": 0.2, "end": 0.4}]},
        {"start": 3.2, "end": 5.0, "text": "世界",
         "words": [{"text": "世界", "start": 3.2, "end": 5.0}]},
    ],
    "full_text": "你好世界",
    "language": "zh",
}


# ─── to_engine_language（上游 ISO 码 → Qwen 规范名）───

@pytest.mark.parametrize("code,expected", [
    ("zh", "Chinese"),          # 纯 ISO 码
    ("en", "English"),
    ("yue", "Cantonese"),
    ("Zh", "Chinese"),          # 大小写不敏感
    ("Chinese", "Chinese"),     # 已是规范名直通
    ("english", "English"),     # 规范名小写也认
    ("zh-CN", "Chinese"),       # 带地区子标签取主标签
    ("en_US", "English"),
    ("tl", "Filipino"),         # 别名映射
    (None, None),               # 缺省 → 自动检测
    ("", None),                 # 空串 → 自动检测
    ("   ", None),              # 纯空白 → 自动检测
    ("sw", None),               # 未支持语言 → 自动检测，不击穿引擎
    ("klingon", None),          # 非法码 → 自动检测
])
def test_to_engine_language(code, expected):
    assert to_engine_language(code) == expected


# ─── _fmt_timestamp ───

def test_fmt_timestamp_zero():
    assert _fmt_timestamp(0.0, sep=",") == "00:00:00,000"


def test_fmt_timestamp_rounding():
    assert _fmt_timestamp(3.2, sep=",") == "00:00:03,200"


def test_fmt_timestamp_cross_min_hour():
    assert _fmt_timestamp(3661.5, sep=",") == "01:01:01,500"


def test_fmt_timestamp_vtt_sep():
    assert _fmt_timestamp(3.2, sep=".") == "00:00:03.200"


def test_fmt_timestamp_none_and_negative_clamp():
    assert _fmt_timestamp(None) == "00:00:00,000"
    assert _fmt_timestamp(-1.0) == "00:00:00,000"


# ─── json / text ───

def test_json_format():
    out = result_to_openai(RESULT, response_format="json", want_word_ts=False, language="zh")
    assert out == {"text": "你好世界"}


def test_text_format_returns_str():
    out = result_to_openai(RESULT, response_format="text", want_word_ts=False, language=None)
    assert out == "你好世界"


# ─── verbose_json ───

def test_verbose_json_structure_and_placeholders():
    out = result_to_openai(RESULT, response_format="verbose_json",
                           want_word_ts=False, language="zh")
    assert out["task"] == "transcribe"
    assert out["language"] == "zh"
    assert out["duration"] == 5.0
    assert out["text"] == "你好世界"
    assert len(out["segments"]) == 2
    seg0 = out["segments"][0]
    assert seg0["id"] == 0 and seg0["seek"] == 0
    assert seg0["start"] == 0.0 and seg0["end"] == 3.2 and seg0["text"] == "你好"
    # 占位字段齐全
    assert seg0["tokens"] == [] and seg0["temperature"] == 0.0
    assert seg0["avg_logprob"] == 0.0 and seg0["compression_ratio"] == 0.0
    assert seg0["no_speech_prob"] == 0.0
    # 未请求 word 粒度 → 无顶层 words
    assert "words" not in out


def test_verbose_json_with_word_timestamps():
    out = result_to_openai(RESULT, response_format="verbose_json",
                           want_word_ts=True, language="zh")
    assert out["words"] == [
        {"word": "你", "start": 0.0, "end": 0.2},
        {"word": "好", "start": 0.2, "end": 0.4},
        {"word": "世界", "start": 3.2, "end": 5.0},
    ]


def test_verbose_json_language_fallback_to_result():
    out = result_to_openai(RESULT, response_format="verbose_json",
                           want_word_ts=False, language=None)
    assert out["language"] == "zh"   # 请求未带 language 时取 result.language


def test_verbose_json_empty_segments():
    out = result_to_openai({"segments": [], "full_text": ""},
                           response_format="verbose_json", want_word_ts=True, language="en")
    assert out["duration"] == 0.0 and out["segments"] == [] and "words" not in out


def test_word_ts_requested_but_no_words():
    res = {"segments": [{"start": 0.0, "end": 1.0, "text": "x"}], "full_text": "x"}
    out = result_to_openai(res, response_format="verbose_json", want_word_ts=True, language="zh")
    assert "words" not in out   # 无词级数据时不加空 words


# ─── srt / vtt ───

def test_srt_format():
    srt = result_to_srt(RESULT["segments"])
    lines = srt.split("\n")
    assert lines[0] == "1"
    assert lines[1] == "00:00:00,000 --> 00:00:03,200"
    assert lines[2] == "你好"
    assert lines[3] == ""
    assert lines[4] == "2"
    assert lines[5] == "00:00:03,200 --> 00:00:05,000"


def test_vtt_format():
    vtt = result_to_vtt(RESULT["segments"])
    lines = vtt.split("\n")
    assert lines[0] == "WEBVTT"
    assert lines[1] == ""
    assert lines[2] == "00:00:00.000 --> 00:00:03.200"
    assert lines[3] == "你好"


def test_srt_empty_segments():
    assert result_to_srt([]) == ""


def test_vtt_empty_segments():
    assert result_to_vtt([]) == "WEBVTT\n"


# ─── SSE 事件（stream=true）───

def test_sse_events_per_segment_delta_then_done():
    events = result_to_openai_sse_events(RESULT)
    assert events == [
        {"type": "transcript.text.delta", "delta": "你好"},
        {"type": "transcript.text.delta", "delta": "世界"},
        {"type": "transcript.text.done", "text": "你好世界"},
    ]


def test_sse_events_no_segments_falls_back_to_full_text():
    events = result_to_openai_sse_events({"segments": [], "full_text": "整段文本"})
    assert events == [
        {"type": "transcript.text.delta", "delta": "整段文本"},
        {"type": "transcript.text.done", "text": "整段文本"},
    ]


def test_sse_events_empty_result_only_done():
    events = result_to_openai_sse_events({"segments": [], "full_text": ""})
    assert events == [{"type": "transcript.text.done", "text": ""}]
