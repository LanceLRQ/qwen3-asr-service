"""FSMN-VAD 在线分块封装：复用已加载的 VAD 模型权重，按块喂音频做实时断句。

每会话独立 cache（仅共享只读权重）。process_chunk 返回句边界事件列表。

FunASR 在线 VAD 真实调用与返回语义已对照已装 funasr==1.3.1 核实
（funasr/models/fsmn_vad_streaming/model.py:inference）：
    model.generate(input=chunk, cache=cache, is_final=bool, chunk_size=ms, fs=16000)
    → [{"key":..., "value": segments}]
    value 中每个区间（model.py:576 注释确认）：
        [beg, -1]  → 语音开始（start）
        [-1, end]  → 语音结束（end）
        [beg, end] → 完整语音段（complete）
        []         → 本块无事件
"""
import logging

import numpy as np

from app.engines.vad_engine import VADEngine

logger = logging.getLogger(__name__)


class StreamingVADEngine:
    """复用 VADEngine 已加载的 FSMN-VAD 权重，提供在线分块断句。"""

    def __init__(self, vad_engine: VADEngine, chunk_ms: int = 200):
        if vad_engine._model is None:
            raise RuntimeError("VAD 模型未加载，无法创建在线 VAD 封装，请先 VADEngine.load()")
        self._model = vad_engine._model      # 共享只读权重
        # 与离线 VADEngine.detect 共用同一把推理锁（AutoModel.generate 非线程安全）
        self._infer_lock = vad_engine._infer_lock
        self._chunk_ms = chunk_ms

    def new_cache(self) -> dict:
        """每会话独立 cache（FunASR 在线模式以空 dict 初始化）。"""
        return {}

    def process_chunk(self, pcm16k: np.ndarray, cache: dict, is_final: bool,
                      max_end_silence_ms: int | None = None) -> list[dict]:
        """喂入一块 16kHz 单声道音频，返回句边界事件列表。

        事件: {"type": "start"|"end"|"complete", "start": ms|None, "end": ms|None}

        max_end_silence_ms：断句尾静音（FunASR 唯一支持运行时覆盖的 VAD 参数，
        仅会话首帧空 cache 时由 init_cache 生效；后续帧忽略）。调用方应每会话固定传值
        （含默认）——init_cache 写共享 vad_opts，_infer_lock 串行化保证 cache 快照不串。
        """
        with self._infer_lock:               # 串行化多会话/跨路径的并发推理
            kwargs = {}
            if max_end_silence_ms is not None:
                kwargs["max_end_silence_time"] = int(max_end_silence_ms)
            res = self._model.generate(
                input=pcm16k,
                cache=cache,
                is_final=is_final,
                chunk_size=self._chunk_ms,
                fs=16000,
                disable_pbar=True,
                **kwargs,
            )
        return self._parse(res)

    def _parse(self, res) -> list[dict]:
        events: list[dict] = []
        if not res or len(res) == 0:
            return events
        value = res[0].get("value", []) or []
        for pair in value:
            if len(pair) != 2:
                continue
            beg, end = pair
            if beg != -1 and end == -1:
                events.append({"type": "start", "start": int(beg), "end": None})
            elif beg == -1 and end != -1:
                events.append({"type": "end", "start": None, "end": int(end)})
            elif beg != -1 and end != -1:
                events.append({"type": "complete", "start": int(beg), "end": int(end)})
            # [-1, -1] 等异常区间忽略
        return events
