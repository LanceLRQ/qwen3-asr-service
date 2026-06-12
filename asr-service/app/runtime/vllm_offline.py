"""vLLM 模式离线转写处理器（Phase 1 + Phase 2 说话人）。

供 TaskManager 的 process_fn 调用：上传音频经 ffmpeg 转 16k → 一次性 vLLM 批量
transcribe → 标点优先分段 → （可选）说话人分离/识别叠加 → 组装成与 standard /v2/asr
同形的 result（segments / full_text / words / speaker / speakers / warnings）。

设计要点（见 docs/plan/features/20260612_vllm_offline_asr/）：
- 不依赖 funasr：分段用模型原生标点 + 词级时间戳定位；说话人用 CAM++ + scipy/sklearn 聚类。
- 顶层不 import vllm/qwen_asr/torch（仅经传入的 engine/speaker_engine 间接调用），依赖中性，
  standard venv 可单测（mock 引擎）。切块也经 engine.split_chunks（qwen_asr 在引擎侧惰性 import）。
- 长音频按静音边界逐块转写：转写阶段(0.1→0.85)逐块报进度 + 块间查取消 + 压峰值显存；
  短音频（≤VLLM_OFFLINE_CHUNK_SEC）单块直转，行为不变。
- 说话人滑窗来源 = 传入的离线能量 VAD（无 funasr VAD），镜像 standard 的 vad_segments 路径。
"""
import logging
import os
import re

from app import config as cfg
from app.pipeline.audio_preprocessor import convert_to_wav, get_audio_duration
from app.utils.result_parser import extract_text, extract_words

logger = logging.getLogger(__name__)


def run_vllm_offline(engine, task, *, progress_callback=None, cancelled=None,
                     speaker_engine=None, speaker_service=None, energy_vad=None) -> dict:
    """执行一次离线转写，返回与 standard ASRPipeline.run 同形的 result dict。

    speaker_engine/speaker_service/energy_vad 为 Phase 2 说话人能力（均可缺省=未启用）：
    speaker_engine 在 → diarize；再有 speaker_service 且 identify_speakers → 声纹识别/自动登记。
    """
    task_id = task["task_id"]
    file_path = task["file_path"]
    language = task.get("language")
    opts = task.get("options") or {}
    identify_speakers = task.get("identify_speakers", False)

    with_words = opts.get("with_words", True)
    max_segment = opts.get("max_segment")        # 秒；None → cfg.MAX_SEGMENT_DURATION
    diarize = opts.get("diarize", True)
    id_threshold = opts.get("speaker_id_threshold")
    id_margin = opts.get("speaker_id_margin")

    speaker_enabled = speaker_engine is not None
    # 声纹识别真正能跑的前提：声纹库 + 说话人引擎 + diarize 同时就位（对齐 asr_pipeline）
    spk_id_ready = speaker_service is not None and speaker_enabled and diarize
    warnings = _collect_warnings(engine, opts, identify_speakers,
                                 speaker_enabled=speaker_enabled, spk_id_ready=spk_id_ready)

    wav_path = None
    try:
        if progress_callback:
            progress_callback(0.05)
        os.makedirs(cfg.UPLOADS_DIR, exist_ok=True)
        wav_path = os.path.join(cfg.UPLOADS_DIR, f"{task_id}.wav")
        convert_to_wav(file_path, wav_path)

        duration = get_audio_duration(wav_path)
        if duration < cfg.MIN_AUDIO_DURATION:
            raise ValueError(f"音频过短（{duration:.1f}s），最短要求 {cfg.MIN_AUDIO_DURATION}s")
        if duration > cfg.MAX_AUDIO_DURATION:
            raise ValueError(f"音频过长（{duration:.0f}s），最大支持 {cfg.MAX_AUDIO_DURATION}s")

        # 取消：转写前检查（worker 据 cancel_event 定终态）
        if cancelled and cancelled():
            return _result([], "", language, engine, warnings)

        if progress_callback:
            progress_callback(0.1)
        want_words = with_words and engine.align_enabled
        # 长音频按静音切块逐块转写：转写阶段 0.1→0.85 逐块报进度 + 块间查取消 + 压峰值显存
        transcribed = _transcribe_progressive(
            engine, wav_path, duration, language, want_words, progress_callback, cancelled)
        if transcribed is None:                       # 转写途中取消
            return _result([], "", language, engine, warnings)
        full_text, words = transcribed
        segments = _segment(full_text, words, duration, max_segment)

        # 说话人分离/识别（可选；容错——失败只丢标签，不破坏转写）
        speakers = None
        if speaker_enabled and diarize and segments and not (cancelled and cancelled()):
            speakers = _diarize_and_identify(
                speaker_engine, speaker_service, energy_vad, wav_path, segments, duration,
                identify_speakers=identify_speakers, id_threshold=id_threshold, id_margin=id_margin,
                progress_callback=progress_callback)

        if progress_callback:
            progress_callback(1.0)
        return _result(segments, full_text, language, engine, warnings, speakers=speakers)
    finally:
        _cleanup(file_path, wav_path)


_PROGRESS_TRANSCRIBE_LO = 0.1
_PROGRESS_TRANSCRIBE_HI = 0.85


def _transcribe_progressive(engine, wav_path, duration, language, want_words,
                            progress_callback, cancelled):
    """长音频按静音切块逐块转写，转写阶段 0.1→0.85 逐块报进度、块间查取消。

    返回 (full_text, words) ；途中取消返回 None。短音频（≤VLLM_OFFLINE_CHUNK_SEC）整段
    单块直转（行为不变）。切块经 engine.split_chunks（qwen_asr 同款静音切块，块拼接=原音频）
    →与整段转写质量一致，且每次只对齐一块、峰值显存随块走。词时间戳按块 offset 归到绝对时间。
    """
    import soundfile as sf
    chunk_sec = float(cfg.VLLM_OFFLINE_CHUNK_SEC)
    wav, sr = sf.read(wav_path, dtype="float32")
    if wav.ndim > 1:                                  # 兜底：多声道取均值（阶段0已单声道）
        wav = wav.mean(axis=1)

    if duration <= chunk_sec:
        chunks = [(wav, 0.0)]
    else:
        chunks = engine.split_chunks(wav, sr, chunk_sec) or [(wav, 0.0)]

    total = len(chunks)
    span = _PROGRESS_TRANSCRIBE_HI - _PROGRESS_TRANSCRIBE_LO
    texts, words = [], []
    for i, (cwav, offset) in enumerate(chunks):
        if cancelled and cancelled():
            return None
        results = engine.transcribe((cwav, sr), language=language, with_words=want_words)
        texts.append(extract_text(results))
        if want_words:
            w = extract_words(results, float(offset))
            if w:
                words.extend(w)
        if progress_callback:
            progress_callback(round(_PROGRESS_TRANSCRIBE_LO + span * (i + 1) / total, 3))
    return "".join(texts).strip(), (words or None)


def _collect_warnings(engine, opts: dict, identify_speakers: bool, *,
                      speaker_enabled: bool = False, spk_id_ready: bool = False) -> list:
    """请求了但本模式不支持/无法生效的项 → 软提示（随 result 返回，不报错）。

    speaker_enabled=False（未挂说话人引擎）时 diarize/identify 全部软提示；挂载后仅在
    识别前提缺失（无声纹库 / diarize 关）时对 identify/id 阈值软提示——对齐 asr_pipeline。
    """
    w = []
    if opts.get("with_punc") is False:
        w.append("with_punc")            # vLLM 标点由模型原生提供，无法单独关闭
    if opts.get("with_words") is True and not engine.align_enabled:
        w.append("with_words")           # 对齐器未加载
    if opts.get("diarize") is True and not speaker_enabled:
        w.append("diarize")              # 未挂说话人引擎（--enable-speaker 未开/加载失败）
    if identify_speakers and not spk_id_ready:
        w.append("identify_speakers")
    if (opts.get("speaker_id_threshold") is not None
            or opts.get("speaker_id_margin") is not None) and not spk_id_ready:
        w.append("speaker_id_threshold/margin")
    return w


def _diarize_and_identify(speaker_engine, speaker_service, energy_vad, wav_path, segments,
                          duration, *, identify_speakers, id_threshold, id_margin,
                          progress_callback=None):
    """说话人分离（+可选声纹识别/自动登记），就地给 segments 叠加 speaker/speaker_name。

    返回 speakers（labels_in_order 列表，或识别后带 speaker_id/name 的簇映射），无法分离
    返回 None。永不抛错——容错对齐 standard：说话人失败不影响转写主链路。
    镜像 asr_pipeline._run_diarization + 4.5/4.6 段，滑窗来源换成离线能量 VAD（无 funasr）。
    """
    import soundfile as sf
    from app.engines.speaker_embedding_engine import make_windows
    from app.runtime.speaker_cluster import DiarizationResult, cluster_offline

    if progress_callback:
        progress_callback(0.9)
    speakers = None
    diar = None
    try:
        # 语音区间：离线能量 VAD（无 funasr）；缺省/无段时退化为整段
        vad_segments = energy_vad.detect(wav_path) if energy_vad is not None else []
        if not vad_segments:
            vad_segments = [(0, int(duration * 1000))]
        windows = []
        for st_ms, ed_ms in vad_segments:
            windows.extend(make_windows(st_ms / 1000.0, ed_ms / 1000.0))
        if not windows:
            return None
        if len(windows) > cfg.SPEAKER_MAX_WINDOWS:        # 抽稀防谱聚类 N² 亲和阵内存
            k = -(-len(windows) // cfg.SPEAKER_MAX_WINDOWS)
            windows = windows[::k]
        wav, _sr = sf.read(wav_path, dtype="float32")     # 阶段 0 已保证 16k 单声道
        embeddings = speaker_engine.embed_windows(wav, windows)
        labels = cluster_offline(embeddings, max_speakers=cfg.SPEAKER_MAX)
        diar = DiarizationResult(windows, labels, embeddings)
        for seg in segments:
            label = diar.label_for(seg["start"], seg["end"])
            if label is not None:
                seg["speaker"] = label
        speakers = diar.labels_in_order
        logger.info(f"[vllm-offline] 说话人分离完成: {len(speakers)} 人 {speakers}")
    except Exception as e:
        logger.warning(f"说话人分离失败，跳过: {e}")

    # 声纹识别 + 自动登记（可选）：speakers 升级为带 speaker_id/name 的映射表；
    # map_and_enroll_clusters 永不抛错（失败退回匿名）
    if identify_speakers and speaker_service is not None and diar is not None:
        try:
            mapping = speaker_service.map_and_enroll_clusters(
                diar.clusters, id_threshold=id_threshold, id_margin=id_margin)
            name_of = {m["label"]: m for m in mapping}
            for seg in segments:
                m = name_of.get(seg.get("speaker"))
                if m and m.get("name"):
                    seg["speaker_name"] = m["name"]
            speakers = mapping
            named = sum(1 for m in mapping if m.get("name"))
            logger.info(f"[vllm-offline] 声纹识别完成: {named}/{len(mapping)} 簇有名")
        except Exception as e:
            logger.warning(f"声纹识别失败，跳过: {e}")

    if progress_callback:
        progress_callback(0.95)
    return speakers


_SENTENCE_PUNCT = r"[。！？；!?;]"      # 句末标点（中英）→ 主切点
_CLAUSE_PUNCT = r"[，,、]"              # 子句标点 → 超长句二次切
_DURATION_CLAMP_FACTOR = 2.0           # 段时长超 max_seg×此值视为对齐器跨块损坏 → 钳制


def _segment(full_text: str, words, duration: float, max_segment) -> list:
    """标点优先分段：用 full_text 原生句末标点切句（超长句按逗号细切），词时间戳仅用于
    定位 start/end（min/max + 钳制，免疫对齐器伪间隙/时间戳回退）。

    段文本取自 full_text 切片（非词拼接）→ 保留模型原生标点、concat(segments)==full_text。
    无词级时间戳则整文单段；无句末标点（罕见短句/非中文）退化为词间隙分段。
    """
    if not full_text:
        return []
    if not words:
        return [{"start": 0.0, "end": round(float(duration), 3), "text": full_text}]

    positions = _word_positions(full_text, words)        # 每词在 full_text 的起始下标（同序）
    max_seg = float(max_segment) if max_segment else float(cfg.MAX_SEGMENT_DURATION)

    sentence_cuts = [m.end() for m in re.finditer(_SENTENCE_PUNCT, full_text)]
    if not sentence_cuts:
        return _segment_by_word_gap(full_text, words, positions, max_seg)

    # 句级切片，超 max_seg 的句子在逗号处细切
    final = []
    for c0, c1 in _spans(0, len(full_text), sentence_cuts):
        if _span_seconds(c0, c1, positions, words) > max_seg:
            sub = [c0 + m.end() for m in re.finditer(_CLAUSE_PUNCT, full_text[c0:c1])]
            final.extend(_spans(c0, c1, sub))
        else:
            final.append((c0, c1))

    segments = []
    for c0, c1 in final:
        text = full_text[c0:c1]
        sw = [w for i, w in enumerate(words) if c0 <= positions[i] < c1]
        if not sw:                                       # 纯标点/空片段 → 文本并入前段
            if segments:
                segments[-1]["text"] += text
            continue
        start = min(w["start"] for w in sw)
        end = max(w["end"] for w in sw)                  # min/max 保证 end>=start
        if end - start > max_seg * _DURATION_CLAMP_FACTOR:
            end = start + max_seg                        # 跨块时间戳损坏 → 钳制为近似时长
        segments.append({"start": round(start, 3), "end": round(end, 3),
                         "text": text, "words": list(sw)})
    return segments or [{"start": 0.0, "end": round(float(duration), 3), "text": full_text}]


def _spans(lo: int, hi: int, cut_ends: list) -> list:
    """按升序切点（段结束位）把 [lo, hi) 切成平铺片段 [(s,e), ...]。"""
    spans, s = [], lo
    for c in cut_ends:
        if s < c <= hi:
            spans.append((s, c))
            s = c
    if s < hi:
        spans.append((s, hi))
    return spans


def _span_seconds(c0: int, c1: int, positions: list, words: list) -> float:
    sw = [w for i, w in enumerate(words) if c0 <= positions[i] < c1]
    return (max(w["end"] for w in sw) - min(w["start"] for w in sw)) if sw else 0.0


def _segment_by_word_gap(full_text: str, words: list, positions: list, max_seg: float) -> list:
    """退化路径（full_text 无句末标点，罕见）：按词间隙/回退/段长分段，段文本取 full_text 切片。"""
    gap = cfg.VLLM_SEGMENT_GAP_MS / 1000.0
    groups, cur = [], []
    for w in words:
        if cur:
            prev = cur[-1]
            if (w["start"] - prev["end"]) > gap or w["start"] < prev["end"] \
                    or (w["end"] - cur[0]["start"]) > max_seg:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    first_idx, gi = [], 0
    for g in groups:
        first_idx.append(gi)
        gi += len(g)
    segments = []
    for k, g in enumerate(groups):
        t0 = 0 if k == 0 else positions[first_idx[k]]
        t1 = positions[first_idx[k + 1]] if k + 1 < len(groups) else len(full_text)
        end = max(g[0]["start"], g[-1]["end"])
        segments.append({"start": round(g[0]["start"], 3), "end": round(end, 3),
                         "text": full_text[t0:t1], "words": list(g)})
    return segments


def _word_positions(full_text: str, words: list) -> list:
    """每词在 full_text 中的起始下标（贪心游标推进）；匹配不到时以游标兜底，不抛错。"""
    positions, cursor = [], 0
    for w in words:
        t = w.get("text", "")
        idx = full_text.find(t, cursor) if t else -1
        if idx < 0:
            idx = cursor                                 # 对齐文本与模型文本不符 → 兜底
        positions.append(idx)
        cursor = idx + len(t)
    return positions


def _result(segments, full_text, language, engine, warnings, speakers=None) -> dict:
    result = {
        "segments": segments,
        "full_text": full_text,
        "language": language,
        "align_enabled": engine.align_enabled,
        # vLLM 标点由模型原生提供（恒有，故 True）；非 CT-Transformer 且不可单独关闭，
        # with_punc=false 时进 warnings 表达"无法关闭"。与 standard 的 bool 类型对齐。
        "punc_enabled": True,
    }
    if speakers is not None:
        result["speakers"] = speakers
    if warnings:
        result["warnings"] = warnings
    return result


def _cleanup(*paths):
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError as e:
                logger.warning(f"临时文件清理失败 {p}: {e}")
