"""派生场景映射测试：5 桶分类、能量静音、other 兜底、投票、事件段聚合。"""
import pytest

from app.runtime import scene_mapper as sm


# ─── classify_window ───

def test_silence_by_energy_floor():
    # 能量低 且 无内容信号 → silence
    label, conf = sm.classify_window({"Dog": 0.3}, dbfs=-60.0, silence_dbfs=-50.0)
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


def test_low_energy_with_content_not_silenced():
    # 内容感知静音门：能量极低但有明确语音/演唱信号时不判 silence
    #（修复短促/轻声台词被打标窗 RMS 稀释到底噪而误判静音）
    label, _ = sm.classify_window({"Singing": 0.95}, dbfs=-80.0, silence_dbfs=-50.0)
    assert label == "singing"
    label, _ = sm.classify_window({"Speech": 0.7}, dbfs=-80.0, silence_dbfs=-50.0)
    assert label == "speech"


def test_singing_overrides_music():
    # 演唱优先：music 占优但演唱桶得分达阈值 → 改判 singing
    label, _ = sm.classify_window({"Music": 0.6, "Singing": 0.2}, dbfs=-20.0, singing_min=0.1)
    assert label == "singing"


def test_music_kept_when_singing_below_threshold():
    # 纯器乐：演唱桶得分低于阈值 → 保持 music
    label, _ = sm.classify_window({"Music": 0.6, "Singing": 0.05}, dbfs=-20.0, singing_min=0.1)
    assert label == "music"


def test_singing_override_disabled_by_high_threshold():
    # singing_min 调高 → 更偏 music（可配置验证）
    label, _ = sm.classify_window({"Music": 0.6, "Singing": 0.2}, dbfs=-20.0, singing_min=0.5)
    assert label == "music"


# ─── 人声优先（vocal_priority）───

def test_vocal_priority_speech_over_loud_music():
    # 主播开 BGM 说话：music 分更高，但人声优先 → speech（不再被背景音乐压成 music）
    label, _ = sm.classify_window({"Speech": 0.5, "Music": 0.8}, dbfs=-20.0, vocal_priority=True)
    assert label == "speech"


def test_vocal_priority_singing_over_loud_music():
    # 主播演唱 + BGM：music 分更高，人声优先 + 命中演唱 → singing
    label, _ = sm.classify_window(
        {"Singing": 0.3, "Music": 0.8, "Speech": 0.2}, dbfs=-20.0, vocal_priority=True, singing_min=0.1)
    assert label == "singing"


def test_vocal_priority_off_falls_back_to_music():
    # 关闭人声优先（music 预设）：纯 argmax → 响度更高的 music 占主导
    label, _ = sm.classify_window({"Speech": 0.5, "Music": 0.8}, dbfs=-20.0, vocal_priority=False)
    assert label == "music"


def test_singing_bias_helps_acappella():
    # 清唱：speech 略高于 singing；singing_bias 抬高演唱使其胜出
    base = {"Speech": 0.4, "Singing": 0.35}
    assert sm.classify_window(base, dbfs=-20.0, vocal_priority=True, singing_bias=0.0)[0] == "speech"
    assert sm.classify_window(base, dbfs=-20.0, vocal_priority=True, singing_bias=0.1)[0] == "singing"


def test_pure_instrumental_stays_music_under_vocal_priority():
    # 纯器乐（无人声）：人声优先下仍落 music
    label, _ = sm.classify_window({"Music": 0.7, "Musical instrument": 0.5}, dbfs=-20.0, vocal_priority=True)
    assert label == "music"


# ─── 预设 ───

def test_presets_have_expected_shape():
    for name in ("balanced", "live", "music"):
        p = sm.resolve_preset(name)
        assert set(p) == {"vocal_priority", "singing_min", "singing_bias"}
    assert sm.resolve_preset("live")["singing_bias"] > 0          # 直播带清唱偏置
    assert sm.resolve_preset("music")["vocal_priority"] is False  # 音乐优先关人声优先
    assert sm.resolve_preset("nonexistent") == sm.resolve_preset("balanced")  # 未知名回退默认


# ─── 每桶权重 weights ───

def test_weights_demote_music():
    # music 原本占优；weights 压低 music 后，人声优先下说话胜出更稳，且 scene_scores 反映权重
    sc = {"Speech": 0.4, "Music": 0.6}
    assert sm.classify_window(sc, dbfs=-20.0, vocal_priority=False)[0] == "music"
    assert sm.classify_window(sc, dbfs=-20.0, vocal_priority=False, weights={"music": 0.5})[0] == "speech"


def test_bucket_scores_applies_weights():
    bs = sm.bucket_scores({"Music": 0.8}, weights={"music": 0.5})
    assert bs["music"] == pytest.approx(0.4)


# ─── mean_bucket_scores（重叠加权 + 桶权重）───

def test_mean_bucket_scores_plain():
    out = sm.mean_bucket_scores([{"Speech": 0.6}, {"Speech": 0.2}])
    assert out["speech"] == pytest.approx(0.4)


def test_mean_bucket_scores_window_weights_demote_straddle():
    # 两窗：说话窗(重叠权重大) + 跨界 music 窗(重叠权重小) → 加权后 speech 远高于等权
    scores = [{"Speech": 0.8, "Music": 0.1}, {"Speech": 0.1, "Music": 0.9}]
    plain = sm.mean_bucket_scores(scores)
    weighted = sm.mean_bucket_scores(scores, window_weights=[900, 100])
    assert weighted["speech"] > plain["speech"]
    assert weighted["music"] < plain["music"]
    assert weighted["speech"] > weighted["music"]


def test_mean_bucket_scores_bucket_weights():
    out = sm.mean_bucket_scores([{"Music": 0.8}], weights={"music": 0.5})
    assert out["music"] == pytest.approx(0.4)


def test_classify_buckets_direct():
    assert sm.classify_buckets({"speech": 0.5, "music": 0.7}, -20.0)[0] == "speech"
    assert sm.classify_buckets({"speech": 0.0, "music": 0.7}, -20.0)[0] == "music"


# ─── refine_scene_with_text（文本感知歌声修正）───

def test_refine_music_with_lyrics_to_singing():
    # 判成 music 但有歌词 + 说话分不强 → 演唱（带伴奏歌声）
    bs = {"speech": 0.07, "singing": 0.04, "music": 0.44}
    assert sm.refine_scene_with_text("music", bs, has_text=True, speech_min=0.30) == "singing"


def test_refine_weak_speech_with_lyrics_to_singing():
    # 判成 speech 但说话分低 + 有伴奏 + 有歌词 → 演唱
    bs = {"speech": 0.18, "singing": 0.04, "music": 0.30}
    assert sm.refine_scene_with_text("speech", bs, has_text=True, speech_min=0.30) == "singing"


def test_refine_keeps_strong_speech():
    # 说话分高（说话+BGM）→ 仍 speech，不误判演唱
    bs = {"speech": 0.52, "singing": 0.05, "music": 0.50}
    assert sm.refine_scene_with_text("speech", bs, has_text=True, speech_min=0.30) == "speech"


def test_refine_no_text_unchanged():
    # 无歌词（纯器乐）→ 原样保留 music
    bs = {"speech": 0.05, "singing": 0.04, "music": 0.7}
    assert sm.refine_scene_with_text("music", bs, has_text=False) == "music"


def test_refine_keeps_existing_singing_and_silence():
    bs = {"speech": 0.1, "singing": 0.5, "music": 0.3}
    assert sm.refine_scene_with_text("singing", bs, has_text=True) == "singing"
    assert sm.refine_scene_with_text("silence", bs, has_text=True) == "silence"


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
