"""语言码归一（中立工具层，api 兼容层与 runtime 原生端点共用）。

放在 app/utils 而非 api/compat，避免 app/runtime 反向依赖 app/api：原生实时/离线
端点同样需要把上游 language 归一成引擎规范名，否则非法 hint 会击穿到引擎抛
`Unsupported language: ...`（引擎内部 validate_language 会把 zh 归成 Zh 再校验，必挂）。
"""
from __future__ import annotations

# 上游 ISO-639-1 → Qwen3-ASR 规范名
_ISO_TO_QWEN = {
    "zh": "Chinese", "en": "English", "yue": "Cantonese", "ar": "Arabic",
    "de": "German", "fr": "French", "es": "Spanish", "pt": "Portuguese",
    "id": "Indonesian", "it": "Italian", "ko": "Korean", "ru": "Russian",
    "th": "Thai", "vi": "Vietnamese", "ja": "Japanese", "tr": "Turkish",
    "hi": "Hindi", "ms": "Malay", "nl": "Dutch", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "pl": "Polish", "cs": "Czech",
    "fil": "Filipino", "tl": "Filipino", "fa": "Persian", "el": "Greek",
    "ro": "Romanian", "hu": "Hungarian", "mk": "Macedonian",
}
_QWEN_CANONICAL = {v.lower(): v for v in _ISO_TO_QWEN.values()}


def to_engine_language(code: str | None) -> str | None:
    """上游 language（ISO-639-1 码 / 规范名 / 带地区子标签）→ Qwen3-ASR 规范名。

    None/空/未识别 → None（交引擎自动检测），避免非法 hint 击穿到引擎抛错。
    """
    if code is None:
        return None
    s = str(code).strip()
    if not s:
        return None
    key = s.lower()
    if key in _QWEN_CANONICAL:                          # 已是规范名（Chinese/english…）
        return _QWEN_CANONICAL[key]
    if key in _ISO_TO_QWEN:                             # 纯 ISO 码（zh/en…）
        return _ISO_TO_QWEN[key]
    primary = key.replace("_", "-").split("-", 1)[0]    # zh-CN / en_US → 取主标签
    return _ISO_TO_QWEN.get(primary)
