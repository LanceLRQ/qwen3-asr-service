"""app/api/compat/mappers.py DashScope 部分测试（纯函数，全分支）。

覆盖：秒→毫秒边界与四舍五入；状态映射五态；speaker 标签→整型；sentences/words；
content_duration；空段。
"""
from app.api.compat.mappers import (
    result_to_dashscope_transcript,
    sec_to_ms,
    v2status_to_dashscope,
)

RESULT = {
    "segments": [
        {"start": 0.0, "end": 3.2, "text": "你好", "speaker": "A",
         "words": [{"text": "你", "start": 0.0, "end": 0.2},
                   {"text": "好", "start": 0.2, "end": 0.4}]},
        {"start": 3.2, "end": 5.0, "text": "世界", "speaker": "B"},
    ],
    "full_text": "你好世界",
    "language": "zh",
}


# ─── sec_to_ms ───

def test_sec_to_ms_zero():
    assert sec_to_ms(0.0) == 0


def test_sec_to_ms_basic():
    assert sec_to_ms(3.2) == 3200


def test_sec_to_ms_rounding():
    assert sec_to_ms(1.9999) == 2000   # round(1999.9) → 2000（避开 .5 banker's 边界）
    assert sec_to_ms(0.123) == 123


def test_sec_to_ms_none():
    assert sec_to_ms(None) == 0


# ─── v2status_to_dashscope ───

def test_status_mapping_all():
    assert v2status_to_dashscope("pending") == "PENDING"
    assert v2status_to_dashscope("processing") == "RUNNING"
    assert v2status_to_dashscope("completed") == "SUCCEEDED"
    assert v2status_to_dashscope("failed") == "FAILED"
    assert v2status_to_dashscope("cancelled") == "FAILED"


def test_status_unknown_fallback():
    assert v2status_to_dashscope("weird") == "PENDING"
    assert v2status_to_dashscope(None) == "PENDING"


# ─── result_to_dashscope_transcript ───

def test_transcript_structure():
    doc = result_to_dashscope_transcript(RESULT, "https://x/a.wav")
    assert doc["file_url"] == "https://x/a.wav"
    assert doc["properties"]["original_sampling_rate"] == 16000
    assert doc["properties"]["original_duration_in_milliseconds"] == 5000
    tr = doc["transcripts"][0]
    assert tr["channel_id"] == 0
    assert tr["content_duration_in_milliseconds"] == 5000
    assert tr["text"] == "你好世界"
    assert len(tr["sentences"]) == 2


def test_transcript_sentence_ms_and_id():
    doc = result_to_dashscope_transcript(RESULT, "u")
    s0 = doc["transcripts"][0]["sentences"][0]
    assert s0["begin_time"] == 0 and s0["end_time"] == 3200
    assert s0["sentence_id"] == 1
    assert s0["text"] == "你好"


def test_transcript_speaker_label_to_int():
    doc = result_to_dashscope_transcript(RESULT, "u")
    sentences = doc["transcripts"][0]["sentences"]
    assert sentences[0]["speaker_id"] == 0   # A → 0
    assert sentences[1]["speaker_id"] == 1   # B → 1


def test_transcript_words_ms_and_punctuation():
    doc = result_to_dashscope_transcript(RESULT, "u")
    words = doc["transcripts"][0]["sentences"][0]["words"]
    assert words == [
        {"begin_time": 0, "end_time": 200, "text": "你", "punctuation": ""},
        {"begin_time": 200, "end_time": 400, "text": "好", "punctuation": ""},
    ]


def test_transcript_no_words_no_speaker():
    res = {"segments": [{"start": 0.0, "end": 1.0, "text": "x"}], "full_text": "x"}
    doc = result_to_dashscope_transcript(res, "u")
    s0 = doc["transcripts"][0]["sentences"][0]
    assert "words" not in s0 and "speaker_id" not in s0


def test_transcript_empty_segments():
    doc = result_to_dashscope_transcript({"segments": [], "full_text": ""}, "u")
    tr = doc["transcripts"][0]
    assert tr["sentences"] == [] and tr["content_duration_in_milliseconds"] == 0
