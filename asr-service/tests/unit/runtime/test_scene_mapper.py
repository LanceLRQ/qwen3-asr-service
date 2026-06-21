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


def test_load_scene_map_yaml(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text("speech:\n  - Speech\n  - Conversation\nmusic:\n  - Music\n", encoding="utf-8")
    m = sm.load_scene_map(str(p))
    assert m == {"speech": ["Speech", "Conversation"], "music": ["Music"]}


def test_load_scene_map_rejects_non_list_member(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("speech: not_a_list\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sm.load_scene_map(str(p))


def test_load_scene_map_rejects_empty(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sm.load_scene_map(str(p))


def test_classify_window_honors_custom_map():
    # 自定义把 Dog 归入 animal 桶
    custom = {"animal": ["Dog", "Cat"]}
    label, _ = sm.classify_window({"Dog": 0.8}, dbfs=-20.0, scene_map=custom)
    assert label == "animal"


def test_bucket_scores_takes_member_max():
    bs = sm.bucket_scores({"Singing": 0.6, "Choir": 0.8, "Speech": 0.3, "Music": 0.1})
    assert bs["singing"] == pytest.approx(0.8)   # Choir 是 singing 成员
    assert bs["speech"] == pytest.approx(0.3)
    assert bs["music"] == pytest.approx(0.1)


# ─── SceneSmoother（迟滞） ───

def test_smoother_enter_dwell_required():
    sm_ = sm.SceneSmoother(enter_sec=2.0, exit_sec=2.0)
    assert sm_.update("speech", 0.8, 0) is None        # 候选起点
    assert sm_.update("speech", 0.8, 1000) is None     # 1s < 2s
    assert sm_.update("speech", 0.8, 2000) == "speech"  # 满 2s 确认
    assert sm_.current == "speech" and sm_.since_ms == 0


def test_smoother_same_as_current_returns_none():
    sm_ = sm.SceneSmoother(enter_sec=0.0)
    assert sm_.update("speech", 0.8, 0) == "speech"
    assert sm_.update("speech", 0.9, 500) is None      # 已是 current


def test_smoother_no_flicker_on_brief_candidate():
    sm_ = sm.SceneSmoother(enter_sec=2.0, exit_sec=2.0)
    sm_.update("speech", 0.8, 0)
    sm_.update("speech", 0.8, 2000)                     # 确认 speech
    assert sm_.update("music", 0.9, 2960) is None       # 偶发一帧 music 不切
    assert sm_.update("speech", 0.8, 3920) is None
    assert sm_.current == "speech"


def test_smoother_exit_to_silence_uses_exit_sec():
    sm_ = sm.SceneSmoother(enter_sec=10.0, exit_sec=1.0)
    sm_.update("speech", 0.8, 0)
    assert sm_.update("speech", 0.8, 10000) == "speech"  # 进内容场景需 enter=10s
    assert sm_.update("silence", 1.0, 10500) is None     # 候选 silence 从 10500 起，0s
    assert sm_.update("silence", 1.0, 11000) is None     # 0.5s < exit 1s
    assert sm_.update("silence", 1.0, 11500) == "silence"  # 满 exit 1s


def test_smoother_candidate_change_resets_timer():
    sm_ = sm.SceneSmoother(enter_sec=2.0)
    sm_.update("speech", 0.5, 0)
    sm_.update("music", 0.5, 1000)                      # 候选改 music，计时从 1000 起
    assert sm_.update("music", 0.5, 2500) is None       # 1.5s < 2s
    assert sm_.update("music", 0.5, 3000) == "music"    # 满 2s
