"""vLLM 模式离线转写处理器（Phase 1）。

供 TaskManager 的 process_fn 调用：上传音频经 ffmpeg 转 16k → 一次性 vLLM 批量
transcribe → 按词间隙分段 → 组装成与 standard /v2/asr 同形的 result（segments /
full_text / words / warnings）。

设计要点（见 docs/plan/features/20260612_vllm_offline_asr/）：
- 不依赖 funasr：分段用词级时间戳的「词间隙」（对齐器开时）/ 整文兜底；标点用模型原生。
- 顶层不 import vllm/qwen_asr（仅经传入的 engine 间接调用），依赖中性，standard venv 可单测。
- transcribe 为单次阻塞调用、不可中断：仅在开始前检查取消以免空耗。
"""
import logging
import os

from app import config as cfg
from app.pipeline.audio_preprocessor import convert_to_wav, get_audio_duration
from app.utils.result_parser import extract_text, extract_words

logger = logging.getLogger(__name__)


def run_vllm_offline(engine, task, *, progress_callback=None, cancelled=None) -> dict:
    """执行一次离线转写，返回与 standard ASRPipeline.run 同形的 result dict。"""
    task_id = task["task_id"]
    file_path = task["file_path"]
    language = task.get("language")
    opts = task.get("options") or {}
    identify_speakers = task.get("identify_speakers", False)

    with_words = opts.get("with_words", True)
    max_segment = opts.get("max_segment")        # 秒；None → cfg.MAX_SEGMENT_DURATION

    warnings = _collect_warnings(engine, opts, identify_speakers)

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

        # transcribe 单次阻塞、不可中断：仅开始前检查取消（worker 据 cancel_event 定终态）
        if cancelled and cancelled():
            return _result([], "", language, engine, warnings)

        if progress_callback:
            progress_callback(0.1)
        want_words = with_words and engine.align_enabled
        results = engine.transcribe(wav_path, language=language, with_words=want_words)

        if progress_callback:
            progress_callback(0.9)
        full_text = extract_text(results).strip()
        words = extract_words(results, 0.0) if want_words else None
        segments = _segment(full_text, words, duration, max_segment)

        if progress_callback:
            progress_callback(1.0)
        return _result(segments, full_text, language, engine, warnings)
    finally:
        _cleanup(file_path, wav_path)


def _collect_warnings(engine, opts: dict, identify_speakers: bool) -> list:
    """请求了但本模式不支持/无法生效的项 → 软提示（随 result 返回，不报错）。"""
    w = []
    if opts.get("with_punc") is False:
        w.append("with_punc")            # vLLM 标点由模型原生提供，无法单独关闭
    if opts.get("with_words") is True and not engine.align_enabled:
        w.append("with_words")           # 对齐器未加载
    if opts.get("diarize") is True:
        w.append("diarize")              # Phase 1 无说话人分离
    if identify_speakers:
        w.append("identify_speakers")
    if opts.get("speaker_id_threshold") is not None or opts.get("speaker_id_margin") is not None:
        w.append("speaker_id_threshold/margin")
    return w


def _segment(full_text: str, words, duration: float, max_segment) -> list:
    """按词间隙分段（对齐器开时）；无词级时间戳则整文单段、空文本则空列表。"""
    if not full_text:
        return []
    if not words:
        return [{"start": 0.0, "end": round(float(duration), 3), "text": full_text}]
    gap = cfg.VLLM_SEGMENT_GAP_MS / 1000.0
    max_seg = float(max_segment) if max_segment else float(cfg.MAX_SEGMENT_DURATION)
    segments, cur = [], []
    for w in words:
        if cur:
            too_far = (w["start"] - cur[-1]["end"]) > gap
            too_long = (w["end"] - cur[0]["start"]) > max_seg
            if too_far or too_long:
                segments.append(_make_segment(cur))
                cur = []
        cur.append(w)
    if cur:
        segments.append(_make_segment(cur))
    return segments


def _make_segment(words: list) -> dict:
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": "".join(w["text"] for w in words),
        "words": list(words),
    }


def _result(segments, full_text, language, engine, warnings) -> dict:
    result = {
        "segments": segments,
        "full_text": full_text,
        "language": language,
        "align_enabled": engine.align_enabled,
        # vLLM 标点由模型原生提供（恒有，故 True）；非 CT-Transformer 且不可单独关闭，
        # with_punc=false 时进 warnings 表达"无法关闭"。与 standard 的 bool 类型对齐。
        "punc_enabled": True,
    }
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
