"""app/pipeline/sentence_segmenter.py 单元测试。

覆盖 evolution.md §二.4 的分句规则：标点强/弱切、停顿强/弱切、说话人切换强切、
处理块边界标点软化、max_segment 上限二次切、英文句点保护（小数/.env/缩写）、
词级与无词级两条定时路径、concat 不变量。
"""
import pytest

from app.pipeline.sentence_segmenter import (
    segment_sentences,
    _is_sentence_end_at,
    _is_english_period_end,
)


def _w(text, s, e):
    return {"text": text, "start": s, "end": e}


# ─── 标点切句 ─────────────────────────────────────────────────────────

def test_internal_sentence_punct_splits():
    chunk = {"start": 0.0, "end": 1.0, "text": "你好。再见。",
             "words": [_w("你", 0.0, 0.2), _w("好", 0.2, 0.4),
                       _w("再", 0.5, 0.7), _w("见", 0.7, 0.9)]}
    segs = segment_sentences([chunk])
    assert [s["text"] for s in segs] == ["你好。", "再见。"]
    assert segs[0]["start"] == 0.0 and segs[0]["end"] == 0.4
    assert segs[1]["start"] == 0.5 and segs[1]["end"] == 0.9
    assert "".join(s["text"] for s in segs) == chunk["text"]   # concat 不变量


def test_comma_not_split_by_default():
    chunk = {"start": 0.0, "end": 1.0, "text": "你好，世界。",
             "words": [_w("你", 0.0, 0.2), _w("好", 0.2, 0.4),
                       _w("世", 0.5, 0.7), _w("界", 0.7, 0.9)]}
    segs = segment_sentences([chunk])
    assert [s["text"] for s in segs] == ["你好，世界。"]   # 逗号默认不断句


# ─── 停顿切句 ─────────────────────────────────────────────────────────

def test_long_gap_between_chunks_splits_without_punct():
    chunks = [{"start": 0.0, "end": 2.0, "text": "前半"},
              {"start": 3.0, "end": 5.0, "text": "后半"}]   # gap 1.0s >= 0.8
    segs = segment_sentences(chunks)
    assert [s["text"] for s in segs] == ["前半", "后半"]
    assert segs[0]["start"] == 0.0 and segs[1]["start"] == 3.0


def test_long_word_gap_inside_chunk_splits():
    chunk = {"start": 0.0, "end": 5.0, "text": "前半后半",
             "words": [_w("前", 0.0, 1.0), _w("半", 1.0, 2.0),
                       _w("后", 3.0, 4.0), _w("半", 4.0, 5.0)]}  # 内部 1.0s 间隙
    segs = segment_sentences([chunk])
    assert [s["text"] for s in segs] == ["前半", "后半"]


def test_short_gap_does_not_split():
    chunks = [{"start": 0.0, "end": 2.0, "text": "前半"},
              {"start": 2.1, "end": 4.0, "text": "后半"}]   # gap 0.1s < 0.8，无标点
    segs = segment_sentences(chunks)
    assert [s["text"] for s in segs] == ["前半后半"]


# ─── 说话人切换 ───────────────────────────────────────────────────────

def test_speaker_change_splits_and_keeps_labels():
    chunks = [{"start": 0.0, "end": 3.0, "text": "甲说的话", "speaker": "A"},
              {"start": 3.0, "end": 6.0, "text": "乙说的话", "speaker": "B"}]  # 紧邻但换人
    segs = segment_sentences(chunks)
    assert [s["text"] for s in segs] == ["甲说的话", "乙说的话"]
    assert [s["speaker"] for s in segs] == ["A", "B"]


def test_no_speaker_no_field():
    segs = segment_sentences([{"start": 0.0, "end": 1.0, "text": "无人"}])
    assert "speaker" not in segs[0]


# ─── 处理块边界标点软化 ───────────────────────────────────────────────

def test_chunk_boundary_punct_softened_when_adjacent_same_speaker():
    # 块末"。"但下一块紧邻、同说话人 → 视为模型按块产生的伪标点，不切
    chunks = [{"start": 0.0, "end": 5.0, "text": "这句被处理块切断了前半。", "speaker": "A"},
              {"start": 5.0, "end": 8.0, "text": "其实后半还在继续。", "speaker": "A"}]
    segs = segment_sentences(chunks)
    assert len(segs) == 1
    assert segs[0]["text"] == "这句被处理块切断了前半。其实后半还在继续。"


def test_chunk_boundary_punct_kept_when_real_pause():
    # 块末"。"且块间有停顿 → 真句末，切开
    chunks = [{"start": 0.0, "end": 5.0, "text": "第一句话。", "speaker": "A"},
              {"start": 6.0, "end": 9.0, "text": "第二句话。", "speaker": "A"}]  # gap 1.0s
    segs = segment_sentences(chunks)
    assert [s["text"] for s in segs] == ["第一句话。", "第二句话。"]


# ─── max_segment 上限（仅显式给定时）───────────────────────────────────

def test_max_segment_subsplit_at_commas():
    chunk = {"start": 0.0, "end": 6.2, "text": "甲，乙，丙。",
             "words": [_w("甲", 0.0, 2.0), _w("乙", 2.0, 4.0), _w("丙", 4.0, 6.2)]}
    assert segment_sentences([chunk]) == [
        {"start": 0.0, "end": 6.2, "text": "甲，乙，丙。",
         "words": chunk["words"]}]                                   # 默认不切
    segs = segment_sentences([chunk], max_segment=5)
    assert [s["text"] for s in segs] == ["甲，", "乙，", "丙。"]       # 超 5s → 逗号细切


def test_max_segment_time_slice_when_no_punct():
    words = [_w(str(i), 0.3 * i, 0.3 * (i + 1)) for i in range(10)]
    chunk = {"start": 0.0, "end": 3.0, "text": "0123456789", "words": words}
    segs = segment_sentences([chunk], max_segment=1.0)
    assert len(segs) >= 2
    assert all((s["end"] - s["start"]) <= 1.0 + 0.05 for s in segs)
    assert "".join(s["text"] for s in segs) == "0123456789"


# ─── 英文句点 ─────────────────────────────────────────────────────────

def test_english_period_splits():
    chunk = {"start": 0.0, "end": 4.0, "text": "Hello world. Open the file now."}
    segs = segment_sentences([chunk])
    assert [s["text"] for s in segs] == ["Hello world.", " Open the file now."]


def test_english_period_no_space_before_capital():
    assert _is_sentence_end_at("back.In", 4) is True       # back.In（无空格）


def test_english_period_protects_decimal_and_dotfile_and_abbrev():
    assert _is_english_period_end("3.14", 1) is False       # 小数
    assert _is_english_period_end(".env", 0) is False       # 点开头 token
    assert _is_english_period_end("e.g. yes", 1) is False   # 单字母缩写


# ─── 无词级时间戳：比例估时 + 内部标点仍切 ────────────────────────────

def test_no_words_proportional_timing_and_internal_split():
    chunk = {"start": 10.0, "end": 14.0, "text": "上半句。下半句。"}
    segs = segment_sentences([chunk])
    assert [s["text"] for s in segs] == ["上半句。", "下半句。"]
    assert segs[0]["start"] == pytest.approx(10.0)
    assert segs[1]["end"] == pytest.approx(14.0)
    assert segs[0]["end"] == pytest.approx(segs[1]["start"], abs=1e-6)
    assert "words" not in segs[0]


# ─── 失败标记 / 空输入 ────────────────────────────────────────────────

def test_failure_mark_isolated():
    chunks = [{"start": 0.0, "end": 1.0, "text": "正常一句。"},
              {"start": 1.0, "end": 2.0, "text": "[识别失败]"},
              {"start": 2.0, "end": 3.0, "text": "又一句。"}]
    segs = segment_sentences(chunks)
    assert [s["text"] for s in segs] == ["正常一句。", "[识别失败]", "又一句。"]


def test_empty_and_blank_input():
    assert segment_sentences([]) == []
    assert segment_sentences([{"start": 0, "end": 1, "text": "   "}]) == []
