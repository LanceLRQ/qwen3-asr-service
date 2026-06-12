"""vLLM 流式活动后端（路线 A）：能量端点断句 + vLLM 原生流式解码。

实现与 stream_session.py 同一 StreamBackend / session 鸭子接口（被 ws_routes.py 消费）：
    Backend: .mode / .backend / .capabilities ；async acquire() -> bool ；
             create_session(sid) -> session ；release(session) ；shutdown()
    session: configure(dict) -> warnings:list ；feed_audio(bytes) -> async-iter[dict] ；
             flush() -> async-iter[dict]
产出信封 dict：{type:"partial",seg_id,text} / {type:"final",seg_id,text,start,end}（无 words/speaker）。

本模块不 import vLLM/qwen_asr（经 VLLMASREngine 鸭子调用），亦不 import stream_session
（其顶层依赖 funasr，vLLM 环境不含）。时间戳用累计样本计数，不需音频缓冲切片。
"""
import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from app.utils.audio_resampler import pcm_bytes_to_array, resample_to_16k
from app.runtime.noise_gate import rms_dbfs
from app.utils.validation import coerce_num_in_range

logger = logging.getLogger(__name__)

_TARGET_SR = 16000
_MIN_AUDIO_FS = 8000
_MAX_AUDIO_FS = 96000
CHUNK_SIZE_SEC_RANGE = (0.5, 5.0)      # 与 vllm_asr_engine.clamp_chunk_size_sec 对齐

# 客户端可能下发、但 vllm 模式不支持的参数 → 软提示忽略（不报错）
_UNSUPPORTED_KEYS = (
    "with_words", "diarize", "with_punc",
    "speaker_threshold", "speaker_min_seg_ms", "speaker_max",
    "speaker_id_threshold", "speaker_id_margin", "identify_speakers",
    "noise_filter", "energy_floor_dbfs", "snr_min_db",
    "max_end_silence_ms", "max_segment_sec",
)


class EnergyEndpointer:
    """按帧能量端点：能量越门限→句开始；尾静音累计≥end_silence_ms→句结束。

    每帧 = 一次 process(arr) 的整段 RMS（端点粒度 = 客户端推流块时长）。
    输入须为 float32 [-1,1)（满量程参考）；与 FSMN-VAD 等价产出 start/end 事件。
    """

    def __init__(self, *, energy_floor_dbfs=-45.0, end_silence_ms=800):
        self._floor = energy_floor_dbfs
        self._end_sil = end_silence_ms
        self.reset()

    def reset(self):
        self._in_speech = False
        self._silence_ms = 0
        self._t_ms = 0          # 会话内累计时间（ms）

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def process(self, arr, frame_ms=None) -> list:
        """返回事件：[{'type':'start','start':ms}] / [{'type':'end','end':ms}] / []。"""
        dur = int(frame_ms) if frame_ms is not None else int(arr.size * 1000 / _TARGET_SR)
        active = rms_dbfs(arr) >= self._floor
        events, t0 = [], self._t_ms
        self._t_ms += dur
        if active:
            self._silence_ms = 0
            if not self._in_speech:
                self._in_speech = True
                events.append({"type": "start", "start": t0})
        elif self._in_speech:
            self._silence_ms += dur
            if self._silence_ms >= self._end_sil:
                self._in_speech = False
                events.append({"type": "end", "end": self._t_ms})
        return events


class VllmStreamSession:
    """单个 WS 会话：能量端点断句 + vLLM 流式解码 → 句内 partial / 句尾 final。"""

    def __init__(self, sid, engine, endpointer: EnergyEndpointer, executor, infer_sem,
                 *, language=None, max_utterance_sec=20):
        self.sid = sid
        self._engine = engine
        self._endpointer = endpointer
        self._executor = executor
        self._sem = infer_sem                  # asyncio.Semaphore：限同时解码会话数
        self.language = language
        self._max_utt_samples = int(max_utterance_sec * _TARGET_SR)
        # 会话态
        self.audio_fs = _TARGET_SR
        self._chunk_size_sec = None            # None=用引擎默认；configure 可按会话覆盖
        self.state = None                      # 当前句流式状态（None=无活动句）
        self.seg_id = 0
        self._seg_start_ms = None
        self._total_ms = 0                     # 会话累计音频时长（ms）
        self._utt_samples = 0                  # 当前句已喂样本数
        self._last_partial = ""

    def configure(self, cfg_msg: dict) -> list:
        cfg_msg = cfg_msg or {}
        raw_fs = cfg_msg.get("audio_fs", _TARGET_SR)
        try:
            audio_fs = int(raw_fs)
        except (TypeError, ValueError):
            raise ValueError(f"audio_fs 非法: {raw_fs!r}")
        if not (_MIN_AUDIO_FS <= audio_fs <= _MAX_AUDIO_FS):
            raise ValueError(
                f"audio_fs 必须在 [{_MIN_AUDIO_FS}, {_MAX_AUDIO_FS}] 范围内，收到 {audio_fs}")
        self.audio_fs = audio_fs
        if cfg_msg.get("language") is not None:
            self.language = cfg_msg.get("language")
        css = cfg_msg.get("chunk_size_sec")
        if css is not None:                    # 会话级覆盖（D6），越界抛 ValueError → invalid_config
            self._chunk_size_sec = coerce_num_in_range(css, CHUNK_SIZE_SEC_RANGE, "chunk_size_sec")
        # 重置会话态
        self._endpointer.reset()
        self.state = None
        self.seg_id = 0
        self._seg_start_ms = None
        self._total_ms = 0
        self._utt_samples = 0
        self._last_partial = ""
        warnings = [k for k in _UNSUPPORTED_KEYS if cfg_msg.get(k) is not None]
        logger.info(f"[vllm-stream] 会话配置 sid={self.sid[:8]} audio_fs={self.audio_fs} "
                    f"language={self.language} chunk={self._chunk_size_sec or '默认'} "
                    f"忽略项={warnings or '无'}")
        return warnings

    async def _in_thread(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    async def _begin_segment(self, start_ms):
        self._seg_start_ms = start_ms
        self.state = await self._in_thread(self._engine.new_state, self.language, self._chunk_size_sec)
        self._utt_samples = 0
        self._last_partial = ""

    async def _finish_segment(self, end_ms):
        async with self._sem:
            text, _ = await self._in_thread(self._engine.finish, self.state)
        msg = {"type": "final", "seg_id": self.seg_id, "text": text,
               "start": int(self._seg_start_ms or 0), "end": int(end_ms)}
        self.seg_id += 1
        self.state = None
        self._seg_start_ms = None
        self._utt_samples = 0
        self._last_partial = ""
        return msg

    async def feed_audio(self, pcm_bytes):
        arr = pcm_bytes_to_array(pcm_bytes)
        if arr.size == 0:
            return
        if self.audio_fs != _TARGET_SR:
            arr = await self._in_thread(resample_to_16k, arr, self.audio_fs)
        dur_ms = int(arr.size * 1000 / _TARGET_SR)
        events = self._endpointer.process(arr, frame_ms=dur_ms)

        # 句开始：先建状态，使本帧即进入新句
        if self.state is None and any(e["type"] == "start" for e in events):
            await self._begin_segment(self._total_ms)

        # 句内：喂本帧，文本变化则发 partial
        if self.state is not None:
            async with self._sem:
                text, _ = await self._in_thread(self._engine.feed, arr, self.state)
            self._utt_samples += arr.size
            if text and text != self._last_partial:
                self._last_partial = text
                yield {"type": "partial", "seg_id": self.seg_id, "text": text}

        self._total_ms += dur_ms

        # 句尾：能量端点判停 → final
        if self.state is not None and any(e["type"] == "end" for e in events):
            yield await self._finish_segment(self._total_ms)
        # 兜底：超长无停顿句强制切分（上下文/显存边界，非性能必需——O(n²) 已被前缀缓存中和）
        elif self.state is not None and self._utt_samples >= self._max_utt_samples:
            logger.info(f"[vllm-stream] 超长句兜底切分 sid={self.sid[:8]} end={self._total_ms}ms")
            yield await self._finish_segment(self._total_ms)
            if self._endpointer.in_speech:          # 语音仍持续 → 立即开新句
                await self._begin_segment(self._total_ms)

    async def flush(self):
        """收到 stop：冲刷未闭合句出 final。"""
        if self.state is not None:
            yield await self._finish_segment(self._total_ms)


class VllmStreamBackend:
    """路线 A 活动后端：能量端点 + vLLM 原生流式。实现 StreamBackend 接口。

    与 VadOfflineBackend（stream_session.py）结构同构，差异仅 session 类型与 capabilities。
    """

    mode = "vllm"
    backend = "vllm-native"

    def __init__(self, engine, *, max_sessions=16, concurrency=1, max_utterance_sec=20,
                 energy_floor_dbfs=-45.0, end_silence_ms=800):
        self._engine = engine
        self._max_sessions = max_sessions
        self._max_utterance_sec = max_utterance_sec
        self._energy_floor_dbfs = energy_floor_dbfs
        self._end_silence_ms = end_silence_ms
        # generate 由引擎 _infer_lock 串行；此信号量限同时在飞解码的会话数（默认 1）
        self._sem = asyncio.Semaphore(concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, concurrency + 1), thread_name_prefix="vllm-asr")
        self._active = 0
        self._count_lock = threading.Lock()
        self.capabilities = {
            "partial_results": True,
            "word_timestamps": False,
            "languages_auto": True,
            "speaker_labels": False,
            "output_toggles": False,
        }

    async def acquire(self) -> bool:
        with self._count_lock:
            if self._active >= self._max_sessions:
                return False
            self._active += 1
            return True

    def create_session(self, sid) -> VllmStreamSession:
        endpointer = EnergyEndpointer(
            energy_floor_dbfs=self._energy_floor_dbfs, end_silence_ms=self._end_silence_ms)
        return VllmStreamSession(
            sid, self._engine, endpointer, self._executor, self._sem,
            max_utterance_sec=self._max_utterance_sec)

    def release(self, session):
        try:
            if session is not None:
                session.state = None       # 丢弃 vLLM 流式状态
        finally:
            with self._count_lock:
                self._active = max(0, self._active - 1)

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
