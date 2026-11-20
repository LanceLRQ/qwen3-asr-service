from app.engines.asr_result import ASRResult, WordTime
from app.utils.result_parser import extract_words


def test_extract_words_with_offset():
    result = ASRResult("a", words=(WordTime("a", 0.1, 0.5),))
    assert extract_words(result, 1.0) == [{"text": "a", "start": 1.1, "end": 1.5}]
    assert result.words[0].start == 0.1


def test_extract_words_none_when_no_timestamps():
    assert extract_words(ASRResult("x"), 0.0) is None
