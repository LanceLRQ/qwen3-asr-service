"""实时会话与"活动后端"抽象（路线 B：在线 VAD 断句 + 内存离线解码）。

StreamBackend 接口约定（VadOfflineBackend / 未来 VllmStreamBackend 各自实现）：
    属性: .mode / .backend / .capabilities
    async acquire() -> bool          # 并发超额返回 False
    create_session(sid) -> StreamSession
    release(session)                 # 释放信号量/缓冲

StreamSession 产出类型化信封事件 dict（{"type": "final", ...}）。
所有阻塞推理（VAD / ASR / 标点）经线程池下沉，不阻塞事件循环。
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.utils.audio_resampler import pcm_bytes_to_array, resample_to_16k
from app.engines.streaming_vad_engine import StreamingVADEngine

logger = logging.getLogger(__name__)

_TARGET_SR = 16000


# ─── 结果解析（借鉴 asr_pipeline._extract_text/_extract_words，结构一致）───

def _extract_text(results) -> str:
    if not results:
        return ""
    if isinstance(results, str):
        return results
    if isinstance(results, list):
        texts = []
        for item in results:
            if hasattr(item, "text"):
                texts.append(item.text)
            elif isinstance(item, dict):
                texts.append(item.get("text", ""))
            elif isinstance(item, str):
                texts.append(item)
        return "".join(texts)
    if hasattr(results, "text"):
        return results.text
    return str(results)


def _extract_words(results, offset_sec: float):
    if not results or not isinstance(results, list):
        return None
    words = []
    for item in results:
        ts = getattr(item, "time_stamps", None)
        if ts is None:
            continue
        for w in getattr(ts, "items", []):
            words.append({
                "text": w.text,
                "start": round(w.start_time + offset_sec, 3),
                "end": round(w.end_time + offset_sec, 3),
            })
    return words if words else None


class AudioBuffer:
    """累积 16kHz float32 单声道样本，按绝对毫秒切片，并可释放已消费部分。"""

    def __init__(self, sr: int = _TARGET_SR):
        self.sr = sr
        self._buf = np.zeros(0, dtype=np.float32)
        self._base_ms = 0     # _buf[0] 对应的绝对时间(ms)

    def append(self, arr: np.ndarray):
        if arr is not None and arr.size:
            self._buf = np.concatenate([self._buf, np.asarray(arr, dtype=np.float32)])

    def _ms_to_idx(self, ms: int) -> int:
        idx = int((ms - self._base_ms) * self.sr / 1000)
        return max(0, min(idx, len(self._buf)))

    @property
    def base_ms(self) -> int:
        return self._base_ms

    @property
    def end_ms(self) -> int:
        return self._base_ms + int(len(self._buf) * 1000 / self.sr)

    def slice_ms(self, start_ms, end_ms) -> np.ndarray:
        if start_ms is None:
            start_ms = self._base_ms
        s = self._ms_to_idx(int(start_ms))
        e = self._ms_to_idx(int(end_ms))
        return self._buf[s:e]

    def drop_until_ms(self, ms: int):
        idx = self._ms_to_idx(int(ms))
        if idx > 0:
            self._buf = self._buf[idx:]
            self._base_ms += int(idx * 1000 / self.sr)


class StreamSession:
    """单个 WS 会话：缓冲音频 → 在线 VAD 断句 → 内存离线解码 → 标点 → 产出 final 信封。"""

    def __init__(self, sid, svad: StreamingVADEngine, asr, punc, executor, asr_sem,
                 *, language=None, max_segment_sec=30, enable_words=False):
        self.sid = sid
        self._svad = svad
        self._asr = asr
        self._punc = punc
        self._executor = executor
        self._asr_sem = asr_sem
        self._max_segment_sec = max_segment_sec
        self._enable_words = enable_words

        self.audio_fs = _TARGET_SR
        self.language = language
        self.wav_name = sid
        self.vad_cache = None
        self.seg_id = 0
        self.seg_start_ms = None
        self.buffer = None
        self._frame_count = 0

    def configure(self, cfg_msg: dict):
        cfg_msg = cfg_msg or {}
        self.audio_fs = int(cfg_msg.get("audio_fs", _TARGET_SR))
        if cfg_msg.get("language") is not None:
            self.language = cfg_msg.get("language")
        self.wav_name = cfg_msg.get("wav_name", self.sid)
        self.vad_cache = self._svad.new_cache()
        self.seg_id = 0
        self.seg_start_ms = None
        self.buffer = AudioBuffer(sr=_TARGET_SR)
        self._frame_count = 0
        logger.info(f"[stream] 会话配置 sid={self.sid[:8]} audio_fs={self.audio_fs} "
                    f"language={self.language} wav={self.wav_name}")

    async def _in_thread(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    async def feed_audio(self, pcm_bytes):
        """喂入一块二进制 PCM16，异步产出 final 信封（按句）。"""
        if self.buffer is None:
            self.configure({})
        arr = pcm_bytes_to_array(pcm_bytes)
        if self.audio_fs != _TARGET_SR and arr.size:
            arr = await self._in_thread(resample_to_16k, arr, self.audio_fs)
        self.buffer.append(arr)

        self._frame_count += 1
        events = await self._in_thread(self._svad.process_chunk, arr, self.vad_cache, False)
        if events:
            logger.debug(f"[stream] frame#{self._frame_count} 收到{arr.size}样本 VAD事件={events}")
        elif self._frame_count % 16 == 0:      # 约每 2s 一次心跳，监控缓冲是否无界增长
            logger.debug(f"[stream] frame#{self._frame_count} 心跳 "
                         f"buffer={self.buffer.end_ms - self.buffer.base_ms}ms "
                         f"end_ms={self.buffer.end_ms} seg_start={self.seg_start_ms}")
        for ev in events:
            async for msg in self._on_event(ev):
                yield msg

        # 长无停顿句兜底切分，避免缓冲无限增长
        if self.seg_start_ms is not None and \
                self.buffer.end_ms - self.seg_start_ms >= self._max_segment_sec * 1000:
            end_ms = self.buffer.end_ms
            logger.info(f"[stream] 长句兜底切分 seg_start={self.seg_start_ms} end={end_ms}")
            async for msg in self._emit_final(self.seg_start_ms, end_ms):
                yield msg
            self.seg_start_ms = end_ms
            self.buffer.drop_until_ms(end_ms)

    async def flush(self):
        """收到 {type:"stop"} 时冲刷末句。"""
        if self.buffer is None:
            return
        logger.debug(f"[stream] flush sid={self.sid[:8]} 总帧数={self._frame_count} "
                     f"seg_start={self.seg_start_ms}")
        events = await self._in_thread(
            self._svad.process_chunk, np.zeros(0, dtype=np.float32), self.vad_cache, True
        )
        for ev in events:
            async for msg in self._on_event(ev):
                yield msg
        # 仍有未闭合句（收到 start 未收到 end）→ 冲刷剩余缓冲
        if self.seg_start_ms is not None:
            async for msg in self._emit_final(self.seg_start_ms, self.buffer.end_ms):
                yield msg
            self.seg_start_ms = None

    async def _on_event(self, ev):
        t = ev["type"]
        if t == "start":
            self.seg_start_ms = ev["start"]
        elif t in ("end", "complete"):
            start_ms = ev["start"] if t == "complete" else self.seg_start_ms
            if start_ms is None:
                start_ms = self.buffer.base_ms
            async for msg in self._emit_final(start_ms, ev["end"]):
                yield msg
            self.seg_start_ms = None
            self.buffer.drop_until_ms(ev["end"])

    async def _emit_final(self, start_ms, end_ms):
        seg = self.buffer.slice_ms(start_ms, end_ms)
        if seg is None or seg.size == 0:
            logger.debug(f"[stream] 跳过空段 start={start_ms} end={end_ms}")
            return
        t0 = time.monotonic()
        async with self._asr_sem:                      # 串行化 GPU/ASR
            res = await self._in_thread(self._asr.transcribe_array, seg, _TARGET_SR, self.language)
        decode_ms = (time.monotonic() - t0) * 1000
        text = _extract_text(res)
        if self._punc is not None and text.strip():
            try:
                text = await self._in_thread(self._punc.restore, text)
            except Exception as e:
                logger.warning(f"标点恢复失败，使用原始文本: {e}")
        logger.info(f"[stream] final#{self.seg_id} 段[{int(start_ms)},{int(end_ms)}]"
                    f"={int(end_ms - start_ms)}ms 样本={seg.size} 解码={decode_ms:.0f}ms 文本长度={len(text)}")
        msg = {
            "type": "final",
            "seg_id": self.seg_id,
            "text": text,
            "start": int(start_ms),
            "end": int(end_ms),
        }
        if self._enable_words:
            words = _extract_words(res, int(start_ms) / 1000.0)
            if words:
                msg["words"] = words
        self.seg_id += 1
        yield msg


class VadOfflineBackend:
    """路线 B 活动后端：在线 VAD 断句 + 内存离线 Qwen ASR 解码。实现 StreamBackend 接口。"""

    mode = "standard"
    backend = "vad-offline"

    def __init__(self, asr, vad, punc=None, *, max_sessions=4, asr_concurrency=1,
                 max_segment_sec=30, vad_chunk_ms=200):
        self._svad = StreamingVADEngine(vad, chunk_ms=vad_chunk_ms)
        self._asr = asr
        self._punc = punc
        self._max_sessions = max_sessions
        self._max_segment_sec = max_segment_sec
        self._enable_words = bool(getattr(asr, "align_enabled", False))
        self._asr_sem = asyncio.Semaphore(asr_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, asr_concurrency + 2), thread_name_prefix="stream-asr"
        )
        self._active = 0
        self._count_lock = asyncio.Lock()
        self.capabilities = {
            "partial_results": False,
            "word_timestamps": self._enable_words,
            "languages_auto": True,
        }

    async def acquire(self) -> bool:
        async with self._count_lock:
            if self._active >= self._max_sessions:
                return False
            self._active += 1
            return True

    def create_session(self, sid) -> StreamSession:
        return StreamSession(
            sid, self._svad, self._asr, self._punc, self._executor, self._asr_sem,
            max_segment_sec=self._max_segment_sec, enable_words=self._enable_words,
        )

    def release(self, session):
        try:
            if session is not None:
                session.buffer = None
                session.vad_cache = None
        finally:
            self._active = max(0, self._active - 1)

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
