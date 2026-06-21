"""整段音频打标的共享窗级推理：离线管线 per-segment scene 与 /v2/audio/tag 端点共用。

把「滑窗 → predict_window」收敛到一处，避免离线管线与端点各写一遍。纯函数，
依赖注入 tagger（共形 AudioTaggerEngine），不感知具体引擎。
"""
from __future__ import annotations

import numpy as np

from app.runtime import scene_mapper
from app.runtime.noise_gate import rms_dbfs


def tag_windows(tagger, wav: np.ndarray, sr: int, interval_ms: int, topk: int) -> list[tuple]:
    """非重叠滑窗（步长 interval_ms）逐窗打标。

    返回时间升序 [(start_ms, end_ms, top, scores, dbfs)]：top 为 [(label, prob)]，
    scores 为全类 {label: prob}，dbfs 为本窗能量（场景静音判定用）。
    """
    total = len(wav)
    step = max(1, int(sr * max(1, interval_ms) / 1000))
    out: list[tuple] = []
    for off in range(0, total, step):
        clip = wav[off:off + step]
        if clip.size == 0:
            continue
        tr = tagger.predict_window(clip, sr, topk)
        out.append((int(off * 1000 / sr), int(min(off + step, total) * 1000 / sr),
                    tr.top, tr.scores, rms_dbfs(clip)))
    return out


def events_from_windows(windows: list[tuple]) -> list[dict]:
    """逐窗 top-k → onset/offset 聚合的事件段（见 scene_mapper.aggregate_events）。"""
    return scene_mapper.aggregate_events([(s, e, top) for (s, e, top, _, _) in windows])


def scene_timeline(windows: list[tuple], scene_map=None, silence_dbfs: float = -50.0) -> list[dict]:
    """逐窗 → 每窗 scene → run-length 合并成连续场景时间段 [{label, start_ms, end_ms}]。"""
    segs: list[dict] = []
    for (s, e, _top, scores, dbfs) in windows:
        label, _ = scene_mapper.classify_window(
            scores, dbfs, scene_map=scene_map, silence_dbfs=silence_dbfs)
        if segs and segs[-1]["label"] == label:
            segs[-1]["end_ms"] = e
        else:
            segs.append({"label": label, "start_ms": s, "end_ms": e})
    return segs


def tag_wav(tagger, wav: np.ndarray, sr: int, *, interval_ms: int, topk: int,
            scene_enable: bool, scene_map=None, silence_dbfs: float = -50.0) -> dict:
    """整段打标（/v2/audio/tag 端点用）：audio_events 事件段 + 可选 scene_timeline。"""
    if wav.ndim > 1:
        wav = wav.mean(axis=1)
    if len(wav) == 0:
        return {"audio_events": [], "scene_timeline": [] if scene_enable else None}
    windows = tag_windows(tagger, wav, sr, interval_ms, topk)
    result: dict = {"audio_events": events_from_windows(windows)}
    if scene_enable:
        result["scene_timeline"] = scene_timeline(windows, scene_map, silence_dbfs)
    return result
