"""识别一个输入片段的结果；词时间均为相对片段起点的秒数。"""
from dataclasses import dataclass


@dataclass(frozen=True)
class WordTime:
    text: str
    start: float
    end: float


@dataclass(frozen=True)
class ASRResult:
    text: str
    language: str | None = None
    words: tuple[WordTime, ...] | None = None
    chunk_id: str | None = None


@dataclass(frozen=True)
class BackendCapabilities:
    offline: bool = True
    batch: bool = False
    word_timestamps: bool = False
    realtime_incremental: bool = False


def from_qwen(items, chunk_id: str | None) -> ASRResult:
    """转换一个输入的 Qwen 结果；无识别内容时仍保留片段身份。"""
    words = []
    texts = []
    language = None
    for item in items:
        texts.append(item.text)
        if not language and item.language and item.language != "None":
            language = item.language
        if item.time_stamps:
            words.extend(WordTime(w.text, w.start_time, w.end_time) for w in item.time_stamps.items)
    return ASRResult("".join(texts), language, tuple(words) or None, chunk_id)


def from_qwen_text(raw: str, chunk_id: str | None) -> ASRResult:
    """解析 OpenVINO 和远端共享的 Qwen 解码文本格式。"""
    prefix, marker, body = raw.partition("<asr_text>")
    if marker:
        language = prefix.removeprefix("language ").strip() or None
        return ASRResult(body.strip(), None if language == "None" else language, chunk_id=chunk_id)
    return ASRResult(raw.strip(), chunk_id=chunk_id)
