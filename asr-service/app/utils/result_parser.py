"""将片段内词时间转换为公共结果中的全局秒坐标。"""
from app.engines.asr_result import ASRResult


def extract_words(result: ASRResult, offset_sec: float) -> list[dict] | None:
    if not result.words:
        return None
    return [
        {"text": w.text, "start": round(w.start + offset_sec, 3), "end": round(w.end + offset_sec, 3)}
        for w in result.words
    ]
