"""派生场景映射：通用 AudioSet 标签 → 高层场景桶（默认 5 桶通用集）+ 离线事件段聚合。

设计（docs/plan/features/20260620_audio_tagging/audio-tagging-design.md §3.3/§3.5）：
- scene = 持续的主导内容状态（互斥，同一时刻一个）：silence / speech / singing / music / other；
  瞬时事件（掌声/笑声/狗叫…）不进 scene，走 audio_events。
- silence 由能量门（noise_gate.rms_dbfs）判定，不耗模型；other 为兜底（无桶占主导时）。
- 映射表为默认开箱集，调用方可传入自定义 scene_map 覆盖（yaml 配置化留 Phase D）。
- 流式迟滞平滑 SceneSmoother 属 Phase B，本模块只提供无状态分类 + 离线聚合。
"""
from __future__ import annotations

SCENE_SILENCE = "silence"
SCENE_OTHER = "other"
SCENE_MUSIC = "music"
SCENE_SINGING = "singing"
SCENE_SPEECH = "speech"

# 默认 5 桶通用集：bucket -> AudioSet display_name 成员（须与 audioset_labels.csv 逐字一致）
DEFAULT_SCENE_MAP: dict[str, list[str]] = {
    "speech": [
        "Speech", "Conversation", "Narration, monologue",
        "Male speech, man speaking", "Female speech, woman speaking",
        "Child speech, kid speaking",
    ],
    "singing": [
        "Singing", "A capella", "Choir", "Chant", "Yodeling", "Rapping", "Humming",
        "Male singing", "Female singing", "Child singing", "Synthetic singing",
        "Vocal music", "Opera",
    ],
    "music": ["Music", "Musical instrument", "Background music"],
}

# 桶判定的最低主导概率：最高桶得分低于此则归 other（防把环境噪声误标成 speech）
SCENE_MIN_SCORE = 0.10
# 演唱判定阈值：详见 config.SCENE_SINGING_MIN
SCENE_SINGING_MIN = 0.10

# 场景判定预设：打包好的权重，供部署/按请求选择（WebUI 下拉）。详见 config.SCENE_PRESET。
# - vocal_priority: 人声优先——说话/演唱只要达阈值就压过背景音乐（music 降为纯器乐兜底）；
#   关闭则回退「桶间 argmax + 演唱特例」（背景音乐更易占主导，适合音乐内容分析）。
# - singing_min: 演唱判定阈值。
# - singing_bias: 清唱偏置——演唱与说话竞争时给演唱加的分（利于无伴奏清唱判为演唱）。
SCENE_PRESETS: dict[str, dict] = {
    "balanced": {"vocal_priority": True, "singing_min": 0.10, "singing_bias": 0.0},
    "live":     {"vocal_priority": True, "singing_min": 0.08, "singing_bias": 0.10},
    "music":    {"vocal_priority": False, "singing_min": 0.10, "singing_bias": 0.0},
}
SCENE_DEFAULT_PRESET = "balanced"


def resolve_preset(name: str | None) -> dict:
    """预设名 → 权重 dict（未知名回退默认预设）。"""
    return SCENE_PRESETS.get(name or SCENE_DEFAULT_PRESET, SCENE_PRESETS[SCENE_DEFAULT_PRESET])


def load_scene_map(path: str) -> dict[str, list[str]]:
    """从 yaml/json 读取自定义场景映射 {bucket: [AudioSet display_name, ...]}。

    校验为非空 dict[str, list[str]]；格式非法或读取失败抛 ValueError（调用方决定降级）。
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    try:
        import yaml
        data = yaml.safe_load(text)
    except ImportError:
        import json
        data = json.loads(text)
    if not isinstance(data, dict) or not data:
        raise ValueError(f"场景映射须为非空 dict: {path}")
    out: dict[str, list[str]] = {}
    for k, v in data.items():
        if not isinstance(v, (list, tuple)) or not all(isinstance(x, str) for x in v):
            raise ValueError(f"场景桶 '{k}' 的成员须为字符串列表: {path}")
        out[str(k)] = list(v)
    return out


def classify_window(scores: dict[str, float], dbfs: float | None,
                    scene_map: dict[str, list[str]] | None = None,
                    silence_dbfs: float = -50.0,
                    min_score: float = SCENE_MIN_SCORE,
                    vocal_priority: bool = True,
                    singing_min: float = SCENE_SINGING_MIN,
                    singing_bias: float = 0.0,
                    weights: dict[str, float] | None = None) -> tuple[str, float]:
    """单窗 → (scene_label, confidence)。

    桶得分 = 该桶成员概率的最大值 ×（可选）weights 桶权重。先判静音，再按 vocal_priority 决定内容场景：

    - 静音：能量低于 silence_dbfs **且** 无明确内容信号（最高桶得分 < min_score）才判 silence。
      不无条件以能量判静音——短促/轻声台词会被打标窗（≈1s）RMS 稀释到底噪，但语音分仍高。
    - vocal_priority=True（人声优先）：说话/演唱只要达阈值就压过背景音乐——主播开 BGM 说话→
      speech、演唱→singing，纯器乐才落 music。speech 与 singing 并存时按
      (singing + singing_bias) 与 speech 比较决出（bias 偏向演唱，利于无伴奏清唱）。
    - vocal_priority=False：桶间 argmax；若 music 占优但演唱桶 ≥ singing_min 则改判 singing
      （背景音乐更易占主导，适合音乐内容分析场景）。
    最高桶得分 < min_score → other（兜底）。
    """
    bs = bucket_scores(scores, scene_map, weights)
    return classify_buckets(bs, dbfs, silence_dbfs=silence_dbfs, min_score=min_score,
                            vocal_priority=vocal_priority, singing_min=singing_min,
                            singing_bias=singing_bias)


def classify_buckets(bucket_score: dict[str, float], dbfs: float | None, *,
                     silence_dbfs: float = -50.0, min_score: float = SCENE_MIN_SCORE,
                     vocal_priority: bool = True, singing_min: float = SCENE_SINGING_MIN,
                     singing_bias: float = 0.0) -> tuple[str, float]:
    """已算好的桶得分 → (scene_label, confidence)。判定逻辑同 classify_window。

    供段级「重叠加权平均桶分」直接定场景用（聚合后判定，规避单个跨界窗主导）。
    """
    best_bucket, best_score = SCENE_OTHER, 0.0
    for bucket, s in bucket_score.items():
        if s > best_score:
            best_bucket, best_score = bucket, s

    if dbfs is not None and dbfs < silence_dbfs and best_score < min_score:
        return SCENE_SILENCE, 1.0
    if best_score < min_score:
        return SCENE_OTHER, float(best_score)

    speech = bucket_score.get(SCENE_SPEECH, 0.0)
    sing = bucket_score.get(SCENE_SINGING, 0.0)

    if vocal_priority:
        speech_ok = speech >= min_score
        singing_ok = sing >= singing_min
        if singing_ok and (not speech_ok or sing + singing_bias >= speech):
            return SCENE_SINGING, float(sing)
        if speech_ok:
            return SCENE_SPEECH, float(speech)
        # 无人声 → 落到最高桶（纯器乐→music，或自定义非人声桶）
        return best_bucket, float(best_score)

    if best_bucket == SCENE_MUSIC and sing >= singing_min:
        return SCENE_SINGING, float(sing)
    return best_bucket, float(best_score)


def refine_scene_with_text(base_label: str, bucket_score: dict[str, float], has_text: bool, *,
                           speech_min: float = 0.30, min_score: float = SCENE_MIN_SCORE) -> str:
    """用转写文本作人声证据修正离线段场景（PANNs 对带伴奏的歌声常只给 Music、不给 Singing）。

    有歌词文本 = 一定有人声 → 该段绝非纯 music。base_label 为 speech/music 时：
    speech 分 ≥ speech_min → speech（说话占优，含说话+BGM）；否则有音乐伴奏（music ≥ min_score）
    → singing（演唱）；否则 speech（无明显伴奏的轻声说话）。
    其余情形（无文本 / 已判 silence·other·singing）原样返回。
    """
    if not has_text or base_label not in (SCENE_SPEECH, SCENE_MUSIC):
        return base_label
    if bucket_score.get(SCENE_SPEECH, 0.0) >= speech_min:
        return SCENE_SPEECH
    if bucket_score.get(SCENE_MUSIC, 0.0) >= min_score:
        return SCENE_SINGING
    return SCENE_SPEECH


def vote_scene(window_scenes: list[tuple[str, float]]) -> str:
    """对一组 (scene, confidence) 按出现次数投票，平票取置信度和较大者；空 → other。"""
    if not window_scenes:
        return SCENE_OTHER
    agg: dict[str, tuple[int, float]] = {}
    for label, conf in window_scenes:
        c, s = agg.get(label, (0, 0.0))
        agg[label] = (c + 1, s + float(conf))
    return max(agg.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]


def aggregate_events(windows: list[tuple[int, int, list[tuple[str, float]]]],
                     threshold: float = 0.20, min_dur_ms: int = 480,
                     merge_gap_ms: int = 480) -> list[dict]:
    """逐窗 top-k → 按 onset/offset 阈值聚合成「事件段」（替代逐窗全量落库）。

    windows: 时间升序 [(start_ms, end_ms, top)]，top 为 [(label, prob), ...]。
    对每个 label，把「概率≥threshold」的相邻窗连成段（容忍 merge_gap_ms 内空隙），
    段时长 < min_dur_ms 丢弃。返回按 (start_ms, label) 升序的
    [{"label","start_ms","end_ms","confidence"}]，confidence 取段内最大概率。
    """
    runs: dict[str, list] = {}    # label -> [start_ms, end_ms, max_prob]
    events: list[dict] = []

    def flush(label: str) -> None:
        r = runs.pop(label, None)
        if r and (r[1] - r[0]) >= min_dur_ms:
            events.append({"label": label, "start_ms": int(r[0]),
                           "end_ms": int(r[1]), "confidence": round(float(r[2]), 4)})

    for start_ms, end_ms, top in windows:
        for label, p in top:
            if p < threshold:
                continue
            r = runs.get(label)
            if r is None or start_ms - r[1] > merge_gap_ms:
                flush(label)
                runs[label] = [start_ms, end_ms, p]
            else:
                r[1] = end_ms
                r[2] = max(r[2], p)
    for label in list(runs.keys()):
        flush(label)
    events.sort(key=lambda e: (e["start_ms"], e["label"]))
    return events


def bucket_scores(scores: dict[str, float],
                  scene_map: dict[str, list[str]] | None = None,
                  weights: dict[str, float] | None = None) -> dict[str, float]:
    """各内容桶的代表分（成员概率最大值 ×可选桶权重），供流式 SceneMsg.scores / 判定。"""
    smap = scene_map or DEFAULT_SCENE_MAP
    w = weights or {}
    return {b: round(max((scores.get(m, 0.0) for m in members), default=0.0) * w.get(b, 1.0), 4)
            for b, members in smap.items()}


def mean_bucket_scores(scores_list: list[dict[str, float]],
                       scene_map: dict[str, list[str]] | None = None,
                       weights: dict[str, float] | None = None,
                       window_weights: list[float] | None = None) -> dict[str, float]:
    """多窗各内容桶代表分的（加权）均值（段级概率分布，供 segment.scene_scores）。

    返回 {bucket: 段内平均概率}——各桶独立置信度（不归一到 1），能如实体现
    「说话+背景音乐」两者并存（如 speech 0.6 / music 0.5）。空输入 → 空 dict。
    - weights: 每桶乘数（部署/调优用，默认全 1.0）。
    - window_weights: 各窗权重（如窗-段时间重叠时长），用于压低大部分落在段外的跨界窗。
    """
    smap = scene_map or DEFAULT_SCENE_MAP
    n = len(scores_list)
    if not n:
        return {}
    ww = window_weights if (window_weights and len(window_weights) == n) else [1.0] * n
    total = sum(ww) or 1.0
    w = weights or {}
    sums = {b: 0.0 for b in smap}
    for scores, wt in zip(scores_list, ww):
        for b, members in smap.items():
            sums[b] += max((scores.get(m, 0.0) for m in members), default=0.0) * wt
    return {b: round(sums[b] / total * w.get(b, 1.0), 4) for b in smap}


# 离开"内容场景"（回到 silence/other）的目标桶，退出用 exit_sec 阈值
_LEAVE_TARGETS = frozenset({SCENE_SILENCE, SCENE_OTHER})


class SceneSmoother:
    """迟滞/双阈值场景平滑（流式必做）：候选场景须连续保持足够时长才确认切换，消除单帧横跳。

    切入"内容场景"（speech/singing/music）需候选连续 ≥ enter_sec；
    切到 silence/other（离开内容场景）需候选连续 ≥ exit_sec。
    update(scene, conf, ts_ms) → 发生确认切换时返回新场景标签，否则 None；
    当前确认态见 .current / .since_ms / .last_conf。
    """

    def __init__(self, enter_sec: float = 2.0, exit_sec: float = 2.0):
        self.enter_ms = int(max(0.0, enter_sec) * 1000)
        self.exit_ms = int(max(0.0, exit_sec) * 1000)
        self.current: str | None = None
        self.since_ms: int | None = None
        self.last_conf: float = 0.0
        self._cand: str | None = None
        self._cand_start: int | None = None

    def update(self, scene: str, conf: float, ts_ms: int) -> str | None:
        if scene != self._cand:
            self._cand = scene
            self._cand_start = ts_ms
        if scene == self.current:
            return None
        dwell = self.exit_ms if scene in _LEAVE_TARGETS else self.enter_ms
        if ts_ms - self._cand_start >= dwell:
            self.current = scene
            self.since_ms = self._cand_start
            self.last_conf = float(conf)
            return scene
        return None
