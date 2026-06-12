"""离线能量 VAD（vLLM 模式说话人分离/声纹库的语音区间来源，无 funasr）。

按帧 RMS 能量门限聚合连续语音帧为 [(start_ms, end_ms)]，返回契约对齐 standard 的
VADEngine.detect（FSMN-VAD）——供 SpeakerEmbeddingEngine 滑窗与 SpeakerService 登记/
识别样本切分复用同一下游代码。

依赖中性：仅 soundfile + numpy + noise_gate.rms_dbfs，不引 torch/funasr。分段质量弱于
FSMN（纯能量、无声学模型），仅作 vLLM 镜像下说话人能力的语音区间近似来源。
"""
import logging

import numpy as np
import soundfile as sf

from app.runtime.noise_gate import rms_dbfs

logger = logging.getLogger(__name__)


class EnergyVAD:
    """能量端点离线 VAD：逐帧 RMS 与门限比较，连续语音帧聚合成段。

    返回 list[(start_ms, end_ms)]（整型毫秒），与 VADEngine.detect 同形，使说话人滑窗
    与声纹登记/识别下游零改动复用。短于 min_speech_ms 的段过滤，尾静音累计达
    end_silence_ms 判段结束（防一句话内的短停顿割裂）。
    """

    BACKEND = "energy"
    SAMPLE_RATE = 16000

    def __init__(self, *, energy_floor_dbfs: float = -45.0, frame_ms: int = 30,
                 min_speech_ms: int = 200, end_silence_ms: int = 300):
        self._floor = energy_floor_dbfs
        self._frame_ms = frame_ms
        self._min_speech_ms = min_speech_ms
        self._end_sil_ms = end_silence_ms

    def detect(self, wav_path: str) -> list[tuple[int, int]]:
        """读取 wav（16k 单声道，离线侧 convert_to_wav 已保证）→ 能量分段。"""
        wav, sr = sf.read(wav_path, dtype="float32")
        if wav.ndim > 1:                       # 兜底：多声道取均值（理论上已单声道）
            wav = wav.mean(axis=1)
        return self.detect_array(wav, sr)

    def detect_array(self, wav: np.ndarray, sr: int = SAMPLE_RATE) -> list[tuple[int, int]]:
        frame = int(sr * self._frame_ms / 1000)
        if frame <= 0 or wav.size == 0:
            return []
        spans: list[tuple[int, int]] = []
        in_speech = False
        seg_start_ms = 0
        speech_end_ms = 0                      # 段内最后一个语音帧的结束时刻
        silence_ms = 0
        for i in range(0, wav.size, frame):
            chunk = wav[i:i + frame]
            t0 = int(i * 1000 / sr)
            dur = int(chunk.size * 1000 / sr)
            if rms_dbfs(chunk) >= self._floor:
                if not in_speech:
                    in_speech = True
                    seg_start_ms = t0
                silence_ms = 0
                speech_end_ms = t0 + dur
            elif in_speech:
                silence_ms += dur
                if silence_ms >= self._end_sil_ms:
                    spans.append((seg_start_ms, speech_end_ms))
                    in_speech = False
        if in_speech:
            spans.append((seg_start_ms, speech_end_ms))
        return [(s, e) for s, e in spans if e - s >= self._min_speech_ms]
