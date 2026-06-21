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


def classify_window(scores: dict[str, float], dbfs: float | None,
                    scene_map: dict[str, list[str]] | None = None,
                    silence_dbfs: float = -50.0,
                    min_score: float = SCENE_MIN_SCORE) -> tuple[str, float]:
    """单窗 → (scene_label, confidence)。

    优先级：能量低于 silence_dbfs → silence；否则取得分最高的桶（桶得分=成员概率最大值）；
    最高桶得分 < min_score → other。
    """
    if dbfs is not None and dbfs < silence_dbfs:
        return SCENE_SILENCE, 1.0
    smap = scene_map or DEFAULT_SCENE_MAP
    best_bucket, best_score = SCENE_OTHER, 0.0
    for bucket, members in smap.items():
        s = max((scores.get(m, 0.0) for m in members), default=0.0)
        if s > best_score:
            best_bucket, best_score = bucket, s
    if best_score < min_score:
        return SCENE_OTHER, float(best_score)
    return best_bucket, float(best_score)


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
                  scene_map: dict[str, list[str]] | None = None) -> dict[str, float]:
    """各内容桶的代表分（成员概率最大值），供流式 SceneMsg.scores 下发（小而有用）。"""
    smap = scene_map or DEFAULT_SCENE_MAP
    return {b: round(max((scores.get(m, 0.0) for m in members), default=0.0), 4)
            for b, members in smap.items()}


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
