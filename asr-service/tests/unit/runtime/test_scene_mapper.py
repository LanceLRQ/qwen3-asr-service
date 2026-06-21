"""派生场景映射测试：5 桶分类、能量静音、other 兜底、投票、事件段聚合。"""
import pytest

from app.runtime import scene_mapper as sm


# ─── classify_window ───

def test_silence_by_energy_floor():
    label, conf = sm.classify_window({"Singing": 0.99}, dbfs=-60.0, silence_dbfs=-50.0)
    assert label == "silence" and conf == 1.0


def test_speech_bucket():
    label, _ = sm.classify_window({"Speech": 0.7, "Music": 0.1}, dbfs=-20.0)
    assert label == "speech"


def test_singing_bucket_uses_members():
    # "A capella" 是 singing 桶成员（CSV 单 p 写法）
    label, _ = sm.classify_window({"A capella": 0.6, "Speech": 0.1}, dbfs=-20.0)
    assert label == "singing"


def test_music_bucket():
    label, _ = sm.classify_window({"Music": 0.5}, dbfs=-20.0)
    assert label == "music"


def test_other_when_below_min_score():
    label, _ = sm.classify_window({"Speech": 0.02}, dbfs=-20.0, min_score=0.1)
    assert label == "other"


def test_other_when_nonbucket_class_dominant():
    label, _ = sm.classify_window({"Dog": 0.9}, dbfs=-20.0)
    assert label == "other"


def test_silence_overrides_high_model_score():
    # 能量门优先于模型分：极低能量即便模型给高 Singing 也判 silence
    label, _ = sm.classify_window({"Singing": 0.95}, dbfs=-80.0, silence_dbfs=-50.0)
    assert label == "silence"


# ─── vote_scene ───

def test_vote_scene_majority():
    assert sm.vote_scene([("speech", 0.5), ("speech", 0.4), ("music", 0.9)]) == "speech"


def test_vote_scene_tie_breaks_by_confidence():
    assert sm.vote_scene([("speech", 0.3), ("music", 0.9)]) == "music"


def test_vote_scene_empty_is_other():
    assert sm.vote_scene([]) == "other"


# ─── aggregate_events ───

def test_aggregate_events_merges_consecutive():
    windows = [
        (0, 960, [("Singing", 0.8)]),
        (960, 1920, [("Singing", 0.7)]),
        (1920, 2880, [("Speech", 0.9)]),
    ]
    events = sm.aggregate_events(windows, threshold=0.2, min_dur_ms=480)
    sing = next(e for e in events if e["label"] == "Singing")
    assert sing["start_ms"] == 0 and sing["end_ms"] == 1920
    assert sing["confidence"] == pytest.approx(0.8)
    assert any(e["label"] == "Speech" for e in events)


def test_aggregate_events_threshold_filters():
    assert sm.aggregate_events([(0, 960, [("Music", 0.1)])], threshold=0.2) == []


def test_aggregate_events_min_duration_filters():
    assert sm.aggregate_events([(0, 400, [("Music", 0.9)])], min_dur_ms=480) == []


def test_aggregate_events_gap_splits_into_two():
    windows = [
        (0, 960, [("Dog", 0.9)]),
        (5000, 5960, [("Dog", 0.9)]),     # 间隙 4040ms > merge_gap
    ]
    dogs = [e for e in sm.aggregate_events(windows, min_dur_ms=480, merge_gap_ms=480)
            if e["label"] == "Dog"]
    assert len(dogs) == 2


def test_aggregate_events_sorted_by_start():
    windows = [
        (0, 960, [("Music", 0.9)]),
        (960, 1920, [("Applause", 0.9)]),
    ]
    events = sm.aggregate_events(windows, min_dur_ms=480)
    assert [e["start_ms"] for e in events] == sorted(e["start_ms"] for e in events)
