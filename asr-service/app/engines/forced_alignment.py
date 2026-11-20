"""离线块级对齐以及词时间与最终文本的一致性检查。"""
import logging
import threading
import unicodedata

from app.utils.language import to_engine_language

logger = logging.getLogger(__name__)
# 与 qwen_asr.inference.utils.MAX_FORCE_ALIGN_INPUT_SECONDS 一致。
MAX_ALIGNMENT_SECONDS = 180.0


class ForcedAlignment:
    def __init__(self, model, infer_lock=None):
        self.model = model
        self.infer_lock = infer_lock or threading.Lock()
        self.languages = model.get_supported_languages()

    @classmethod
    def load(cls, device):
        import torch
        from qwen_asr import Qwen3ForcedAligner
        from app import config as cfg
        from app.utils.model_manager import ensure_model

        source = cfg.MODEL_SOURCE if cfg.MODEL_SOURCE in cfg.MODEL_REPO_MAP else "modelscope"
        path = cfg.MODEL_LOCAL_MAP["aligner"]
        ensure_model(cfg.MODEL_REPO_MAP[source]["aligner"], path)
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        return cls(Qwen3ForcedAligner.from_pretrained(path, dtype=dtype, device_map=device))

    def align(self, audio, text, language):
        language = to_engine_language(language)
        if not language or (self.languages and language.lower() not in self.languages):
            raise ValueError(f"对齐器不支持语种 {language}")
        with self.infer_lock:
            result, = self.model.align(audio=audio, text=text, language=language)
        return [{"text": w.text, "start": w.start_time, "end": w.end_time} for w in result.items]


def valid_words(text, words, start, end):
    """词须依次覆盖最终文本；允许模型略去词间空白和标点。"""
    cursor = 0
    previous = round(start, 3)
    for word in words or ():
        token = word["text"]
        if not token or not previous <= word["start"] <= word["end"] <= round(end, 3):
            return False
        index = text.find(token, cursor)
        if index < 0 or not _separators(text[cursor:index]):
            return False
        cursor = index + len(token)
        previous = word["end"]
    return bool(words) and _separators(text[cursor:])


def _separators(text):
    return all(c.isspace() or unicodedata.category(c).startswith("P") for c in text)


def resolve_words(text, words, start, end, aligner, audio, language):
    if valid_words(text, words, start, end):
        return words
    if aligner is not None and text.strip() and 0 < end - start <= MAX_ALIGNMENT_SECONDS:
        try:
            local = aligner.align(audio, text, language)
            shifted = [dict(w, start=round(w["start"] + start, 3),
                            end=round(w["end"] + start, 3)) for w in local]
            if valid_words(text, shifted, start, end):
                return shifted
        except Exception as exc:
            logger.warning("片段对齐失败，保留转写文本: %s", exc)
    return None
