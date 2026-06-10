"""兼容层结果映射（纯函数，单测主战场）。

数据源：pipeline result dict（asr_pipeline.py:209）——`segments[].start/end` = **秒**(float)，
`segments[].words[].start/end` = 秒。OpenAI verbose_json 全用秒，直取；srt/vtt 由秒格式化为
时间轴。DashScope 全用**毫秒**（秒×1000）。实时（Phase 3）映射后续并入本模块。
"""
from __future__ import annotations

# v2 内部任务状态 → DashScope task_status（design §7.2）
_V2_TO_DASHSCOPE = {
    "pending": "PENDING",
    "processing": "RUNNING",
    "completed": "SUCCEEDED",
    "failed": "FAILED",
    "cancelled": "FAILED",   # message 另行注明 cancelled
}


def _fmt_timestamp(seconds: float | None, *, sep: str = ",") -> str:
    """秒 → HH:MM:SS<sep>mmm（srt 用 ','，vtt 用 '.'）；None/负值钳为 0。"""
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms_total = int(round(seconds * 1000))
    h, ms_total = divmod(ms_total, 3_600_000)
    m, ms_total = divmod(ms_total, 60_000)
    s, ms = divmod(ms_total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def _collect_words(segments: list[dict]) -> list[dict]:
    """汇总各 segment 词级时间戳到顶层 words[]（text→word，start/end 秒直取）。"""
    words = []
    for seg in segments:
        for w in seg.get("words") or []:
            words.append({"word": w.get("text", ""),
                          "start": w.get("start"), "end": w.get("end")})
    return words


def _duration(segments: list[dict]) -> float:
    """全段时长 = max(end)；空段为 0。"""
    return max((seg.get("end", 0.0) or 0.0) for seg in segments) if segments else 0.0


def _verbose_segment(idx: int, seg: dict) -> dict:
    """单段 → OpenAI verbose_json segment。本服务无来源的字段填占位（reference §3.3 注明）。"""
    return {
        "id": idx,
        "seek": 0,
        "start": seg.get("start", 0.0),
        "end": seg.get("end", 0.0),
        "text": seg.get("text", ""),
        "tokens": [],
        "temperature": 0.0,
        "avg_logprob": 0.0,
        "compression_ratio": 0.0,
        "no_speech_prob": 0.0,
    }


def result_to_openai(result: dict, *, response_format: str,
                     want_word_ts: bool, language: str | None):
    """pipeline result → OpenAI 响应。

    json/verbose_json → dict；text/srt/vtt → str（调用方用 PlainTextResponse 包装）。
    """
    segments = result.get("segments") or []
    full_text = result.get("full_text", "")

    if response_format == "json":
        return {"text": full_text}
    if response_format == "text":
        return full_text
    if response_format == "srt":
        return result_to_srt(segments)
    if response_format == "vtt":
        return result_to_vtt(segments)

    # verbose_json
    out = {
        "task": "transcribe",
        "language": language or result.get("language"),
        "duration": _duration(segments),
        "text": full_text,
        "segments": [_verbose_segment(i, seg) for i, seg in enumerate(segments)],
    }
    if want_word_ts:
        words = _collect_words(segments)
        if words:
            out["words"] = words
    return out


def result_to_openai_sse_events(result: dict) -> list[dict]:
    """result → OpenAI 转写 SSE 事件序列（按句 delta + 末尾 done）。

    stream=true 的 HTTP SSE 是**单段上传的流式返回**：本服务整段解码后分句吐字，
    delta 文本是最终结果的分块（无时间戳，不涉及增量伪造），末尾 done 携带全文。
    """
    segments = result.get("segments") or []
    full = result.get("full_text", "")
    events: list[dict] = []
    for seg in segments:
        text = seg.get("text", "")
        if text:
            events.append({"type": "transcript.text.delta", "delta": text})
    if not events and full:
        events.append({"type": "transcript.text.delta", "delta": full})
    events.append({"type": "transcript.text.done", "text": full})
    return events


def result_to_srt(segments: list[dict]) -> str:
    """segments → SRT 字幕（时间轴 HH:MM:SS,mmm，序号从 1）。"""
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        start = _fmt_timestamp(seg.get("start", 0.0), sep=",")
        end = _fmt_timestamp(seg.get("end", 0.0), sep=",")
        lines += [str(i), f"{start} --> {end}", seg.get("text", ""), ""]
    return "\n".join(lines)


def result_to_vtt(segments: list[dict]) -> str:
    """segments → WebVTT 字幕（时间轴 HH:MM:SS.mmm）。"""
    lines = ["WEBVTT", ""]
    for seg in segments:
        start = _fmt_timestamp(seg.get("start", 0.0), sep=".")
        end = _fmt_timestamp(seg.get("end", 0.0), sep=".")
        lines += [f"{start} --> {end}", seg.get("text", ""), ""]
    return "\n".join(lines)


# ─── DashScope（全用毫秒）───

def sec_to_ms(x: float | None) -> int:
    """秒 → 毫秒（四舍五入取整）；None 视为 0。"""
    return int(round((x or 0.0) * 1000))


def v2status_to_dashscope(status: str | None) -> str:
    """v2 内部任务状态 → DashScope task_status；未知状态保守映射 PENDING。"""
    return _V2_TO_DASHSCOPE.get(status or "", "PENDING")


def _speaker_to_int(label) -> int | None:
    """说话人标签（A/B/C… 或已是整数）→ DashScope 整型 speaker_id。"""
    if label is None:
        return None
    if isinstance(label, int):
        return label
    s = str(label).strip()
    if s.isdigit():
        return int(s)
    if len(s) == 1 and s.isalpha():
        return ord(s.upper()) - ord("A")   # A→0, B→1 …
    return None


def _dashscope_sentence(idx: int, seg: dict) -> dict:
    """单段 → DashScope sentence（毫秒）。speaker_id 仅 diarize 命中时有意义。"""
    sentence = {
        "begin_time": sec_to_ms(seg.get("start")),
        "end_time": sec_to_ms(seg.get("end")),
        "text": seg.get("text", ""),
        "sentence_id": idx + 1,
    }
    spk = _speaker_to_int(seg.get("speaker"))
    if spk is not None:
        sentence["speaker_id"] = spk
    words = seg.get("words") or []
    if words:
        sentence["words"] = [{
            "begin_time": sec_to_ms(w.get("start")),
            "end_time": sec_to_ms(w.get("end")),
            "text": w.get("text", ""),
            "punctuation": "",
        } for w in words]
    return sentence


def result_to_dashscope_transcript(result: dict, file_url: str) -> dict:
    """pipeline result → DashScope 转写结果文档（transcription_url 内容，毫秒）。"""
    segments = result.get("segments") or []
    dur_ms = sec_to_ms(_duration(segments))   # 单次扫描，两个时长字段复用
    return {
        "file_url": file_url,
        "properties": {
            "audio_format": "wav",
            "channels": [0],
            "original_sampling_rate": 16000,
            "original_duration_in_milliseconds": dur_ms,
        },
        "transcripts": [{
            "channel_id": 0,
            "content_duration_in_milliseconds": dur_ms,
            "text": result.get("full_text", ""),
            "sentences": [_dashscope_sentence(i, seg) for i, seg in enumerate(segments)],
        }],
    }


# ─── 实时（Phase 3 Stage A：route B 整句 final）───
#
# 单位红线：实时 final 顶层 start/end 已是**毫秒**(int，stream_session.py:387)，直取；
# final.words[].start/end 是**秒**(extract_words(res, start_ms/1000))，→毫秒需 ×1000。

def _realtime_words_ms(final: dict) -> list[dict]:
    """final.words（秒）→ DashScope words（毫秒）。"""
    return [{
        "begin_time": sec_to_ms(w.get("start")),
        "end_time": sec_to_ms(w.get("end")),
        "text": w.get("text", ""),
        "punctuation": "",
    } for w in (final.get("words") or [])]


def final_to_openai_completed(final: dict, item_id: str) -> dict:
    """实时 final → OpenAI `conversation.item.input_audio_transcription.completed`。

    OpenAI completed 仅含整句 transcript（无词级，design §11.3）。
    """
    return {
        "type": "conversation.item.input_audio_transcription.completed",
        "item_id": item_id,
        "content_index": 0,
        "transcript": final.get("text", ""),
    }


def final_to_dashscope_result(final: dict, task_id: str) -> dict:
    """实时 final → DashScope `result-generated`（sentence_end=true，整句）。"""
    sentence = {
        "begin_time": final.get("start"),   # 顶层已是毫秒，直取
        "end_time": final.get("end"),
        "text": final.get("text", ""),
        "sentence_end": True,
    }
    words = _realtime_words_ms(final)
    if words:
        sentence["words"] = words
    return {
        "header": {"task_id": task_id, "event": "result-generated", "attributes": {}},
        "payload": {"output": {"sentence": sentence}, "usage": None},
    }
