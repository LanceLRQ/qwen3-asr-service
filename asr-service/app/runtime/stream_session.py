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
from app.utils.language import to_engine_language
from app.utils.result_parser import extract_text, extract_words
from app.engines.streaming_vad_engine import StreamingVADEngine
from app.runtime.speaker_cluster import OnlineSpeakerClusterer
from app.runtime.noise_gate import NoiseFloorTracker, rms_dbfs, should_gate
from app.runtime import scene_mapper
from app.runtime.scene_mapper import SceneSmoother
from app.utils.validation import (
    coerce_num_in_range, parse_bool,
    SPK_THRESHOLD_RANGE, SPK_MIN_SEG_RANGE, SPK_MAX_RANGE,
    MAX_SEGMENT_SEC_RANGE, MAX_END_SILENCE_RANGE,
    SPK_ID_THRESHOLD_RANGE, SPK_ID_MARGIN_RANGE,
    ENERGY_FLOOR_RANGE, SNR_MIN_RANGE,
)
import app.config as cfg

logger = logging.getLogger(__name__)

_TARGET_SR = 16000
_MIN_AUDIO_FS = 8000          # 客户端 audio_fs 允许下限
_MAX_AUDIO_FS = 96000         # 客户端 audio_fs 允许上限
_IDLE_KEEP_MS = 5000          # 无活动语音段时缓冲保留余量（覆盖 VAD 事件回溯）


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
                 snr_min_db=6.0, tagger=None, scene_enable=True, scene_enter_sec=2.0,
                 scene_exit_sec=2.0, scene_silence_dbfs=-50.0, scene_vocal_priority=True,
                 scene_singing_min=0.10, scene_singing_bias=0.0, scene_weights=None,
                 tag_interval_ms=960, tag_topk=5, scene_map=None):
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

        # 音频标注 / 场景（None tagger 或 scene_enable=False → 流式不产 scene）
        self._tagger = tagger
        self._scene_enable = scene_enable and tagger is not None
        self._scene_enter_sec = scene_enter_sec
        self._scene_exit_sec = scene_exit_sec
        self._scene_silence_dbfs = scene_silence_dbfs
        self._scene_vocal_priority = scene_vocal_priority
        self._scene_singing_min = scene_singing_min
        self._scene_singing_bias = scene_singing_bias
        self._scene_weights = scene_weights
        self._scene_map = scene_map              # 自定义场景映射（None = 内置默认）
        self._tag_topk = tag_topk
        self._scene_step = max(1, int(_TARGET_SR * max(1, tag_interval_ms) / 1000))
        self._scene_chunks: list[np.ndarray] = []   # 独立于 buffer（buffer 会被 final/idle 裁剪）
        self._scene_samples = 0
        self._scene_smoother = None              # configure() 时重建（会话域）

        # 会话级可覆盖参数（默认=服务端 cfg；configure() 经 _apply_session_override 覆盖）
        self._spk_threshold = cfg.SPEAKER_THRESHOLD
        self._spk_min_seg_ms = cfg.SPEAKER_MIN_SEG_MS
        self._spk_max = cfg.SPEAKER_MAX
        self._spk_id_threshold = cfg.SPEAKER_ID_THRESHOLD
        self._spk_id_margin = cfg.SPEAKER_ID_MARGIN
        self._max_end_silence_ms = cfg.VAD_MAX_SILENCE   # 始终显式传 VAD，避免跨会话继承
        self._with_punc = True                   # 降级开关：仅能关（不能开启未加载模型）
        self._with_words = True
        self._with_diarize = True
        self._warnings = []                      # 因功能未启用被忽略的参数（软提示）

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
            # 归一成引擎规范名（zh→Chinese / 带地区子标签亦可）；未识别→None 交自动检测，
            # 避免非法 hint 击穿到引擎抛 Unsupported language
            self.language = to_engine_language(cfg_msg.get("language"))
        self.wav_name = cfg_msg.get("wav_name", self.sid)
        self.vad_cache = self._svad.new_cache()
        self.seg_id = 0
        self.seg_start_ms = None
        self.buffer = AudioBuffer(sr=_TARGET_SR)
        self._frame_count = 0
        self._apply_session_override(cfg_msg)
        self._apply_noise_override(cfg_msg)
        if self._speaker is not None and self._with_diarize:
            self._spk_cluster = OnlineSpeakerClusterer(
                threshold=self._spk_threshold,
                max_speakers=self._spk_max,
                min_seg_ms=self._spk_min_seg_ms,
            )
        else:
            self._spk_cluster = None
        self._identify = bool(cfg_msg.get("identify_speakers", False))
        self._spk_name_cache = {}
        self._noise_tracker = NoiseFloorTracker() if self._noise_filter else None
        self._scene_chunks = []
        self._scene_samples = 0
        self._scene_smoother = (
            SceneSmoother(self._scene_enter_sec, self._scene_exit_sec)
            if self._scene_enable else None)
        self._warnings = self._collect_ignored_params(cfg_msg)
        logger.info(f"[stream] 会话配置 sid={self.sid[:8]} audio_fs={self.audio_fs} "
                    f"language={self.language} wav={self.wav_name} "
                    f"覆盖={'有' if self._warnings else '无'}忽略项")
        return self._warnings

    def _apply_noise_override(self, cfg_msg: dict):
        """客户端 start 消息可选覆盖方案1 阈值（缺省=服务端默认），服务端范围钳制。

        越界/类型错误抛 ValueError → ws_routes 回 invalid_config（体例同 audio_fs）。
        仅影响本会话；vad_speech_noise_thres 受 FunASR 构造期限制不可按会话调，仍为全局。
        """
        self._noise_filter = parse_bool(
            cfg_msg.get("noise_filter"), self._noise_filter, "noise_filter")
        ef = cfg_msg.get("energy_floor_dbfs")
        if ef is not None:
            self._energy_floor_dbfs = coerce_num_in_range(
                ef, ENERGY_FLOOR_RANGE, "energy_floor_dbfs")
        sn = cfg_msg.get("snr_min_db")
        if sn is not None:
            self._snr_min_db = coerce_num_in_range(sn, SNR_MIN_RANGE, "snr_min_db")

    def _apply_session_override(self, cfg_msg: dict):
        """客户端 start 可选覆盖会话级参数（缺省=服务端默认），服务端范围钳制。

        越界/类型错误抛 ValueError → ws_routes 回 invalid_config（体例同 audio_fs）；
        参数合法但功能未启用的情形不在此报错，由 _collect_ignored_params 收集为软提示。
        说话人三参（threshold/min_seg/max）仅作用于在线归簇——离线用谱聚类，不在此列。
        """
        st = cfg_msg.get("speaker_threshold")
        if st is not None:
            self._spk_threshold = coerce_num_in_range(st, SPK_THRESHOLD_RANGE, "speaker_threshold")
        ms = cfg_msg.get("speaker_min_seg_ms")
        if ms is not None:
            self._spk_min_seg_ms = coerce_num_in_range(
                ms, SPK_MIN_SEG_RANGE, "speaker_min_seg_ms", cast=int)
        sx = cfg_msg.get("speaker_max")
        if sx is not None:
            self._spk_max = coerce_num_in_range(sx, SPK_MAX_RANGE, "speaker_max", cast=int)
        it = cfg_msg.get("speaker_id_threshold")
        if it is not None:
            self._spk_id_threshold = coerce_num_in_range(
                it, SPK_ID_THRESHOLD_RANGE, "speaker_id_threshold")
        im = cfg_msg.get("speaker_id_margin")
        if im is not None:
            self._spk_id_margin = coerce_num_in_range(im, SPK_ID_MARGIN_RANGE, "speaker_id_margin")
        es = cfg_msg.get("max_end_silence_ms")
        if es is not None:
            self._max_end_silence_ms = coerce_num_in_range(
                es, MAX_END_SILENCE_RANGE, "max_end_silence_ms", cast=int)
        sg = cfg_msg.get("max_segment_sec")
        if sg is not None:
            self._max_segment_sec = coerce_num_in_range(
                sg, MAX_SEGMENT_SEC_RANGE, "max_segment_sec", cast=int)
        self._with_punc = parse_bool(cfg_msg.get("with_punc"), self._with_punc, "with_punc")
        self._with_words = parse_bool(cfg_msg.get("with_words"), self._with_words, "with_words")
        self._with_diarize = parse_bool(cfg_msg.get("diarize"), self._with_diarize, "diarize")

    def _collect_ignored_params(self, cfg_msg: dict) -> list[str]:
        """合法但因服务端未启用对应功能而无法生效的参数（软提示，不报错）。"""
        ignored = []
        spk_ok = self._speaker is not None
        svc_ok = self._speaker_service is not None
        for k in ("speaker_threshold", "speaker_min_seg_ms", "speaker_max"):
            if cfg_msg.get(k) is not None and not spk_ok:
                ignored.append(k)
        if cfg_msg.get("diarize") is True and not spk_ok:
            ignored.append("diarize")
        # 声纹识别真正能跑的前提：声纹库 + 说话人引擎 + diarize 同时就位（diarize 关时
        # _spk_cluster 为 None，identify/id 阈值全部失效）——与离线管线判定保持一致
        spk_id_ready = svc_ok and spk_ok and self._with_diarize
        for k in ("speaker_id_threshold", "speaker_id_margin"):
            if cfg_msg.get(k) is not None and not spk_id_ready:
                ignored.append(k)
        if cfg_msg.get("identify_speakers") is True and not spk_id_ready:
            ignored.append("identify_speakers")
        if cfg_msg.get("with_words") is True and not self._enable_words:
            ignored.append("with_words")
        if cfg_msg.get("with_punc") is True and self._punc is None:
            ignored.append("with_punc")
        return ignored

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
        events = await self._in_thread(
            self._svad.process_chunk, arr, self.vad_cache, False, self._max_end_silence_ms)
        if events:
            logger.debug(f"[stream] frame#{self._frame_count} 收到{arr.size}样本 VAD事件={events}")
        elif self._frame_count % 16 == 0:      # 约每 2s 一次心跳，监控缓冲是否无界增长
            logger.debug(f"[stream] frame#{self._frame_count} 心跳 "
                         f"buffer={self.buffer.end_ms - self.buffer.base_ms}ms "
                         f"end_ms={self.buffer.end_ms} seg_start={self.seg_start_ms}")
        for ev in events:
            async for msg in self._on_event(ev):
                yield msg

        # 场景标注（独立于 VAD 断句的连续状态信号；buffer 会被 final/idle 裁剪故单独累积）
        if self._scene_smoother is not None:
            async for msg in self._maybe_emit_scene(arr):
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
                self._svad.process_chunk, np.zeros(0, dtype=np.float32), self.vad_cache, True,
                self._max_end_silence_ms,
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
        if self._punc is not None and self._with_punc and text.strip():
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
        if self._enable_words and self._with_words:
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
            [{"label": spk, "centroid": centroid}],
            id_threshold=self._spk_id_threshold, id_margin=self._spk_id_margin)
        name = mapping[0]["name"] if mapping else None
        self._spk_name_cache[spk] = {"name": name, "count": count, "ver": ver}
        return name

    async def _maybe_emit_scene(self, arr):
        """累积音频满一个推理窗即打标 → 迟滞平滑 → 状态切换时产出 scene 信封。

        独立累积（不依赖 buffer——后者随 final/idle 裁剪）；推理在线程池内执行
        （tagger 自带 _infer_lock 串行化），失败仅告警跳过，不影响转写主链路。
        """
        self._scene_chunks.append(arr)
        self._scene_samples += arr.size
        if self._scene_samples < self._scene_step:
            return
        block = np.concatenate(self._scene_chunks)
        window = block[:self._scene_step]
        rest = block[self._scene_step:]
        self._scene_chunks = [rest] if rest.size else []
        self._scene_samples = int(rest.size)

        ts_ms = self.buffer.end_ms
        try:
            tr = await self._in_thread(
                self._tagger.predict_window, window, _TARGET_SR, self._tag_topk)
            scene, conf = scene_mapper.classify_window(
                tr.scores, rms_dbfs(window), scene_map=self._scene_map,
                silence_dbfs=self._scene_silence_dbfs,
                vocal_priority=self._scene_vocal_priority,
                singing_min=self._scene_singing_min, singing_bias=self._scene_singing_bias,
                weights=self._scene_weights)
        except Exception as e:
            logger.warning(f"[stream] 场景打标失败，跳过: {e}")
            return
        changed = self._scene_smoother.update(scene, conf, ts_ms)
        if changed is not None:
            logger.info(f"[stream] scene → {changed} since={self._scene_smoother.since_ms} "
                        f"conf={self._scene_smoother.last_conf:.2f}")
            yield {
                "type": "scene",
                "label": changed,
                "confidence": round(self._scene_smoother.last_conf, 4),
                "since": int(self._scene_smoother.since_ms),
                "scores": scene_mapper.bucket_scores(tr.scores, self._scene_map),
            }


class VadOfflineBackend:
    """路线 B 活动后端：在线 VAD 断句 + 内存离线 Qwen ASR 解码。实现 StreamBackend 接口。"""

    mode = "standard"
    backend = "vad-offline"

    def __init__(self, asr, vad, punc=None, *, speaker=None, speaker_service=None,
                 max_sessions=4, asr_concurrency=1, max_segment_sec=30, vad_chunk_ms=200,
                 noise_filter=False, energy_floor_dbfs=-50.0, snr_min_db=6.0, tagger=None,
                 scene_enable=True, scene_enter_sec=2.0, scene_exit_sec=2.0,
                 scene_silence_dbfs=-50.0, scene_vocal_priority=True,
                 scene_singing_min=0.10, scene_singing_bias=0.0, scene_weights=None,
                 tag_interval_ms=960, tag_topk=5, scene_map=None):
        self._svad = StreamingVADEngine(vad, chunk_ms=vad_chunk_ms)
        self._asr = asr
        self._punc = punc
        self._speaker = speaker
        self._speaker_service = speaker_service
        self._tagger = tagger
        self._scene_enable = scene_enable and tagger is not None
        self._scene_enter_sec = scene_enter_sec
        self._scene_exit_sec = scene_exit_sec
        self._scene_silence_dbfs = scene_silence_dbfs
        self._scene_vocal_priority = scene_vocal_priority
        self._scene_singing_min = scene_singing_min
        self._scene_singing_bias = scene_singing_bias
        self._scene_weights = scene_weights
        self._tag_interval_ms = tag_interval_ms
        self._tag_topk = tag_topk
        self._scene_map = scene_map
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
            "speaker_tunable": speaker is not None,   # speaker_threshold/min_seg/max + id_threshold/margin
            "endpoint_tunable": True,                 # max_end_silence_ms（断句尾静音）/ max_segment_sec
            "output_toggles": True,                   # with_punc / with_words / diarize 可按会话关闭
            "scene": self._scene_enable,              # 派生场景信封（scene 消息，迟滞平滑）
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
            snr_min_db=self._snr_min_db, tagger=self._tagger,
            scene_enable=self._scene_enable, scene_enter_sec=self._scene_enter_sec,
            scene_exit_sec=self._scene_exit_sec, scene_silence_dbfs=self._scene_silence_dbfs,
            scene_vocal_priority=self._scene_vocal_priority,
            scene_singing_min=self._scene_singing_min, scene_singing_bias=self._scene_singing_bias,
            scene_weights=self._scene_weights,
            tag_interval_ms=self._tag_interval_ms, tag_topk=self._tag_topk,
            scene_map=self._scene_map,
        )

    def release(self, session):
        try:
            if session is not None:
                session.buffer = None
                session.vad_cache = None
                session._spk_cluster = None    # 会话域语义：质心状态随会话释放
                session._spk_name_cache = {}   # 声纹簇缓存同步清空
                session._noise_tracker = None  # 噪声底估计随会话释放
                session._scene_smoother = None # 场景平滑状态随会话释放
                session._scene_chunks = []
        finally:
            with self._count_lock:
                self._active = max(0, self._active - 1)

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
