from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.forced_alignment import ForcedAlignment, resolve_words


@pytest.mark.parametrize(("token", "start", "end", "valid"), [
    ("hello", 1.0, 1.5, True),
    ("other", 1.0, 1.5, False),
    ("hello", 0.9, 1.5, False),
    ("hello", 1.5, 1.0, False),
    ("hello", 1.0, float("nan"), False),
    ("hello", 1.0, 2.1, False),
])
def test_verified_backend_words_take_priority(token, start, end, valid):
    words = [{"text": token, "start": start, "end": end}]
    aligner = Mock()
    aligner.align.return_value = [{"text": "hello", "start": 0.1, "end": 0.8}]
    result = resolve_words("hello!", words, 1, 2, aligner, "chunk.wav", "English")
    if valid:
        assert result is words
        aligner.align.assert_not_called()
    else:
        assert result == [{"text": "hello", "start": 1.1, "end": 1.8}]
        aligner.align.assert_called_once_with("chunk.wav", "hello!", "English")


def test_unsupported_language_and_empty_text_skip_inference():
    model = Mock()
    model.get_supported_languages.return_value = ["english"]
    aligner = ForcedAlignment(model)
    for text, lang in [("你好", "zh"), ("", "en"), ("Hello", None)]:
        assert resolve_words(text, None, 0, 1, aligner, "chunk.wav", lang) is None
    model.align.assert_not_called()


def test_inference_error_preserves_text_and_drops_unusable_words():
    model = SimpleNamespace(align=Mock(side_effect=RuntimeError("failed")))
    assert resolve_words("你好", None, 0, 1, model, "chunk.wav", "Chinese") is None


def test_oversized_chunk_does_not_reach_independent_aligner():
    aligner = Mock()
    assert resolve_words("long", None, 10, 191, aligner, "long.wav", "en") is None
    aligner.align.assert_not_called()
