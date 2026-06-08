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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.utils.audio_resampler import pcm_bytes_to_array, resample_to_16k
from app.utils.result_parser import extract_text, extract_words
from app.engines.streaming_vad_engine import StreamingVADEngine
from app.runtime.speaker_cluster import OnlineSpeakerClusterer
from app.runtime.noise_gate import NoiseFloorTracker, rms_dbfs, should_gate
import app.config as cfg

logger = logging.getLogger(__name__)

_TARGET_SR = 16000
_MIN_AUDIO_FS = 8000          # 客户端 audio_fs 允许下限
_MAX_AUDIO_FS = 96000         # 客户端 audio_fs 允许上限
_IDLE_KEEP_MS = 5000          # 无活动语音段时缓冲保留余量（覆盖 VAD 事件回溯）

# 远场过滤客户端覆盖的钳制范围（服务端硬边界，防滥用）
_ENERGY_FLOOR_RANGE = (-90.0, 0.0)    # dBFS（满量程参考，≤0）
_SNR_MIN_RANGE = (0.0, 40.0)          # dB；0=关闭 SNR 门


def _coerce_float_in_range(value, rng, name):
    """客户端浮点参数：类型转换 + 范围校验，越界抛 ValueError（体例同 audio_fs）。"""
    lo, hi = rng
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 非法: {value!r}")
    if not (lo <= v <= hi):
        raise ValueError(f"{name} 必须在 [{lo}, {hi}] 范围内，收到 {v}")
    return v


class AudioBuffer:
    """累积 16kHz float32 单声道样本，按绝对毫秒切片，并可释放已消费部分。

    分块存储 + 惰性合并：append 仅追加块引用（O(1)），slice/drop 前一次性合并，
    避免逐帧 np.concatenate 产生 O(n²) 拷贝。
    """

    def __init__(self, sr: int = _TARGET_SR):
        self.sr = sr
        self._chunks: list[np.ndarray] = []
        self._total = 0       # _chunks 内样本总数
        self._base_ms = 0     # 首样本对应的绝对时间(ms)

    def append(self, arr: np.ndarray):
        if arr is not None and arr.size:
            a = np.asarray(arr, dtype=np.float32)
            self._chunks.append(a)
            self._total += a.size

    def _merged(self) -> np.ndarray:
        if len(self._chunks) > 1:
            self._chunks = [np.concatenate(self._chunks)]
        return self._chunks[0] if self._chunks else np.zeros(0, dtype=np.float32)

    def _ms_to_idx(self, ms: int) -> int:
        idx = int((ms - self._base_ms) * self.sr / 1000)
        return max(0, min(idx, self._total))

    @property
    def base_ms(self) -> int:
        return self._base_ms

    @property
    def end_ms(self) -> int:
        return self._base_ms + int(self._total * 1000 / self.sr)

    def slice_ms(self, start_ms, end_ms) -> np.ndarray:
        if start_ms is None:
            start_ms = self._base_ms
        s = self._ms_to_idx(int(start_ms))
        e = self._ms_to_idx(int(end_ms))
        return self._merged()[s:e]

    def drop_until_ms(self, ms: int):
        idx = self._ms_to_idx(int(ms))
        if idx > 0:
            buf = self._merged()[idx:]
            self._chunks = [buf] if buf.size else []
            self._total = int(buf.size)
            self._base_ms += int(idx * 1000 / self.sr)


class StreamSession:
    """单个 WS 会话：缓冲音频 → 在线 VAD 断句 → 内存离线解码 → 标点 → 产出 final 信封。"""

    def __init__(self, sid, svad: StreamingVADEngine, asr, punc, executor, asr_sem,
                 *, language=None, max_segment_sec=30, enable_words=False, speaker=None,
                 speaker_service=None, noise_filter=False, energy_floor_dbfs=-50.0,
                 snr_min_db=6.0):
        self.sid = sid
        self._svad = svad
        self._asr = asr
        self._punc = punc
        self._executor = executor
        self._asr_sem = asr_sem
        self._max_segment_sec = max_segment_sec
        self._enable_words = enable_words
        self._speaker = speaker                  # None = 说话人分离关闭
        self._spk_cluster = None                 # configure() 时重建（会话域）
        self._speaker_service = speaker_service  # None = 声纹库未启用
        self._identify = False                   # start 消息 identify_speakers 开关
        self._spk_name_cache = {}                # label -> {"name", "count", "ver"}（会话级簇缓存）
        self._noise_filter = noise_filter        # 段级远场/环境音门控开关（opt-in）
        self._energy_floor_dbfs = energy_floor_dbfs
        self._snr_min_db = snr_min_db
        self._noise_tracker = None               # configure() 时重建（会话域噪声底）

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
        self.wav_name = cfg_msg.get("wav_name", self.sid)
        self.vad_cache = self._svad.new_cache()
        self.seg_id = 0
        self.seg_start_ms = None
        self.buffer = AudioBuffer(sr=_TARGET_SR)
        self._frame_count = 0
        if self._speaker is not None:
            self._spk_cluster = OnlineSpeakerClusterer(
                threshold=cfg.SPEAKER_THRESHOLD,
                max_speakers=cfg.SPEAKER_MAX,
                min_seg_ms=cfg.SPEAKER_MIN_SEG_MS,
            )
        self._identify = bool(cfg_msg.get("identify_speakers", False))
        self._spk_name_cache = {}
        self._apply_noise_override(cfg_msg)
        self._noise_tracker = NoiseFloorTracker() if self._noise_filter else None
        logger.info(f"[stream] 会话配置 sid={self.sid[:8]} audio_fs={self.audio_fs} "
                    f"language={self.language} wav={self.wav_name}")

    def _apply_noise_override(self, cfg_msg: dict):
        """客户端 start 消息可选覆盖方案1 阈值（缺省=服务端默认），服务端范围钳制。

        越界/类型错误抛 ValueError → ws_routes 回 invalid_config（体例同 audio_fs）。
        仅影响本会话；vad_speech_noise_thres 受 FunASR 构造期限制不可按会话调，仍为全局。
        """
        nf = cfg_msg.get("noise_filter")
        if nf is not None:
            if not isinstance(nf, bool):
                raise ValueError(f"noise_filter 必须为布尔值，收到 {nf!r}")
            self._noise_filter = nf
        ef = cfg_msg.get("energy_floor_dbfs")
        if ef is not None:
            self._energy_floor_dbfs = _coerce_float_in_range(
                ef, _ENERGY_FLOOR_RANGE, "energy_floor_dbfs")
        sn = cfg_msg.get("snr_min_db")
        if sn is not None:
            self._snr_min_db = _coerce_float_in_range(sn, _SNR_MIN_RANGE, "snr_min_db")

    async def _in_thread(self, fn, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, fn, *args)

    async def feed_audio(self, pcm_bytes):
        """喂入一块二进制 PCM16，异步产出 final 信封（按句）。"""
        if self.buffer is None:
            self.configure({})
        arr = pcm_bytes_to_array(pcm_bytes)
        if arr.size == 0:
            return                          # 空帧直接忽略，不喂 VAD
        if self.audio_fs != _TARGET_SR:
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

        # 无活动语音段（长静音）时裁剪缓冲，防止内存无界增长；
        # 保留 _IDLE_KEEP_MS 余量，覆盖后续 VAD start 事件的时间回溯
        if self.seg_start_ms is None:
            if self._noise_tracker is not None:       # 非语音期采集环境噪声底
                self._noise_tracker.update(arr)
            keep_from = self.buffer.end_ms - _IDLE_KEEP_MS
            if keep_from > self.buffer.base_ms:
                self.buffer.drop_until_ms(keep_from)

    async def flush(self):
        """收到 {type:"stop"} 时冲刷末句。"""
        if self.buffer is None:
            return
        logger.debug(f"[stream] flush sid={self.sid[:8]} 总帧数={self._frame_count} "
                     f"seg_start={self.seg_start_ms}")
        try:
            events = await self._in_thread(
                self._svad.process_chunk, np.zeros(0, dtype=np.float32), self.vad_cache, True
            )
        except Exception as e:
            # FunASR 对空输入 + is_final 的行为无保证；失败不阻断末句缓冲冲刷
            logger.warning(f"[stream] VAD final 冲刷失败，继续冲刷剩余缓冲: {e}")
            events = []
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
        # 远场/环境音门控：响度过低或相对底噪不够突出的段，送 ASR 前丢弃（不静默——记日志）
        if self._noise_tracker is not None:
            seg_dbfs = rms_dbfs(seg)
            floor = self._noise_tracker.floor_dbfs
            gated, reason = should_gate(
                seg_dbfs, floor,
                energy_floor_dbfs=self._energy_floor_dbfs, snr_min_db=self._snr_min_db)
            if gated:
                logger.debug(
                    f"[stream] 远场/噪声丢弃 段[{int(start_ms)},{int(end_ms)}]"
                    f"={int(end_ms - start_ms)}ms dbfs={seg_dbfs:.1f} "
                    f"floor={'—' if floor is None else f'{floor:.1f}'} 门={reason}")
                return
        t0 = time.monotonic()
        async with self._asr_sem:                      # 串行化 GPU/ASR
            res = await self._in_thread(self._asr.transcribe_array, seg, _TARGET_SR, self.language)
        decode_ms = (time.monotonic() - t0) * 1000
        text = extract_text(res)
        if self._punc is not None and text.strip():
            try:
                text = await self._in_thread(self._punc.restore, text)
            except Exception as e:
                logger.warning(f"标点恢复失败，使用原始文本: {e}")
        # 说话人判定：CPU 任务，在 _asr_sem 之外、线程池内执行（体例同标点）
        spk = None
        spk_name = None
        if self._spk_cluster is not None:
            try:
                emb = await self._in_thread(self._speaker.embed_segment, seg)
                spk = self._spk_cluster.assign(emb, int(end_ms - start_ms))
            except Exception as e:
                logger.warning(f"说话人判定失败，本段不标注: {e}")
            # 声纹识别（可选）：以"当时"质心查库，不回改历史（以最新 final 为准）
            if spk is not None and self._identify and self._speaker_service is not None:
                try:
                    spk_name = await self._in_thread(self._lookup_speaker_name, spk)
                except Exception as e:
                    logger.warning(f"声纹识别失败，本段不带真名: {e}")
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
            words = extract_words(res, int(start_ms) / 1000.0)
            if words:
                msg["words"] = words
        if spk is not None:
            msg["speaker"] = spk
        if spk_name is not None:
            msg["speaker_name"] = spk_name
        self.seg_id += 1
        yield msg

    def _lookup_speaker_name(self, spk: str) -> str | None:
        """会话级簇缓存的声纹查询（同步，线程池内执行）。

        缓存失效（满足任一即重查）：
        ① 簇质心累计段数达上次查询的 2 倍——早期 unknown 随质心稳定升级命中；
        ② 声纹库 cache_version 变化——外部登记/改名/删除即时可见。
        均不回改历史 final。
        """
        cluster = self._spk_cluster      # 本地引用：release() 并发置 None 防护
        if cluster is None:
            return None
        ver = self._speaker_service.store.cache_version
        count = max(cluster.count_of(spk), 1)
        cached = self._spk_name_cache.get(spk)
        if (cached is not None and cached["ver"] == ver
                and count < cached["count"] * 2):
            return cached["name"]
        centroid = cluster.centroid_of(spk)
        if centroid is None:
            return cached["name"] if cached else None
        mapping = self._speaker_service.map_clusters(
            [{"label": spk, "centroid": centroid}])
        name = mapping[0]["name"] if mapping else None
        self._spk_name_cache[spk] = {"name": name, "count": count, "ver": ver}
        return name


class VadOfflineBackend:
    """路线 B 活动后端：在线 VAD 断句 + 内存离线 Qwen ASR 解码。实现 StreamBackend 接口。"""

    mode = "standard"
    backend = "vad-offline"

    def __init__(self, asr, vad, punc=None, *, speaker=None, speaker_service=None,
                 max_sessions=4, asr_concurrency=1, max_segment_sec=30, vad_chunk_ms=200,
                 noise_filter=False, energy_floor_dbfs=-50.0, snr_min_db=6.0):
        self._svad = StreamingVADEngine(vad, chunk_ms=vad_chunk_ms)
        self._asr = asr
        self._punc = punc
        self._speaker = speaker
        self._speaker_service = speaker_service
        self._max_sessions = max_sessions
        self._max_segment_sec = max_segment_sec
        self._noise_filter = noise_filter
        self._energy_floor_dbfs = energy_floor_dbfs
        self._snr_min_db = snr_min_db
        self._enable_words = bool(getattr(asr, "align_enabled", False))
        # 在事件循环启动前创建：依赖 Python >=3.10 的 Semaphore 延迟绑定循环语义
        # （setup.sh 已强制 3.10/3.12；<3.10 会在此处 RuntimeError）
        self._asr_sem = asyncio.Semaphore(asr_concurrency)
        self._executor = ThreadPoolExecutor(
            max_workers=max(2, asr_concurrency + 2), thread_name_prefix="stream-asr"
        )
        self._active = 0
        # threading.Lock：acquire（异步）与 release（同步）双侧共用同一把锁，
        # 临界区仅计数读改写，不阻塞事件循环
        self._count_lock = threading.Lock()
        self.capabilities = {
            "partial_results": False,
            "word_timestamps": self._enable_words,
            "languages_auto": True,
            "speaker_labels": speaker is not None,
            "speaker_identification": speaker is not None and speaker_service is not None,
            "noise_filter_tunable": True,   # 客户端可在 start 覆盖 noise_filter/energy_floor_dbfs/snr_min_db
        }

    async def acquire(self) -> bool:
        with self._count_lock:
            if self._active >= self._max_sessions:
                return False
            self._active += 1
            return True

    def create_session(self, sid) -> StreamSession:
        return StreamSession(
            sid, self._svad, self._asr, self._punc, self._executor, self._asr_sem,
            max_segment_sec=self._max_segment_sec, enable_words=self._enable_words,
            speaker=self._speaker, speaker_service=self._speaker_service,
            noise_filter=self._noise_filter, energy_floor_dbfs=self._energy_floor_dbfs,
            snr_min_db=self._snr_min_db,
        )

    def release(self, session):
        try:
            if session is not None:
                session.buffer = None
                session.vad_cache = None
                session._spk_cluster = None    # 会话域语义：质心状态随会话释放
                session._spk_name_cache = {}   # 声纹簇缓存同步清空
                session._noise_tracker = None  # 噪声底估计随会话释放
        finally:
            with self._count_lock:
                self._active = max(0, self._active - 1)

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
