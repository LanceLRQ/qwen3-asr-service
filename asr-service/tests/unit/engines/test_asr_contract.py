from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.qwen_asr_engine import QwenASREngine
from app.engines.remote_asr_engine import RemoteASREngine


@pytest.mark.parametrize("remote", [False, True])
@pytest.mark.parametrize("text,language", [("", None), ("hello", None), ("hello", "English")])
def test_file_result(tmp_path, remote, text, language):
    path = str(tmp_path / "chunk.wav")
    (tmp_path / "chunk.wav").touch()
    if remote:
        engine = RemoteASREngine("http://upstream", "model", None, 1)
        prefix = f"language {language}<asr_text>" if language else ""
        engine._request = Mock(return_value={"text": prefix + text})
    else:
        engine = QwenASREngine(enable_align=False)
        engine._model = Mock()
        engine._model.transcribe.return_value = [
            SimpleNamespace(text=text, language=language, time_stamps=None)
        ] if text else []
    result = engine.transcribe(path)
    assert (result.text, result.language, result.words, result.chunk_id) == (text, language, None, path)
    assert engine.capabilities.batch is (not remote)


@pytest.mark.parametrize("raw,language,text", [
    ("language English<asr_text> Hi ", "English", "Hi"),
    ("language None<asr_text>Hello", None, "Hello"),
    (" 一句 ", None, "一句"),
])
def test_openvino_array_preserves_detected_language(raw, language, text):
    import numpy as np
    from app.engines.openvino_asr_engine import OpenVINOASREngine

    engine = OpenVINOASREngine()
    engine._processor = Mock()
    engine._processor.prepare.return_value = (None, None)
    engine._infer = Mock(return_value=raw)
    result = engine.transcribe_array(np.zeros(16), chunk_id="segment")
    assert (result.text, result.language, result.chunk_id) == (text, language, "segment")


@pytest.mark.parametrize("remote", [False, True])
def test_two_chunk_coordinates(remote, tmp_path):
    from app.pipeline.asr_pipeline import ASRPipeline

    chunks = []
    for i, offset in enumerate((0.5, 3.0)):
        path = tmp_path / f"{i}.wav"
        path.touch()
        chunks.append({"path": str(path), "offset_sec": offset, "duration_sec": 1.0})
    if remote:
        engine = RemoteASREngine("http://upstream", "model", None, 1)
        engine._request = Mock(side_effect=[{"text": "one"}, {"text": "two"}])
        transcribe = ASRPipeline(engine, None)._transcribe_sequential
    else:
        engine = QwenASREngine(enable_align=True)
        engine._model = Mock()
        items = []
        for text in ("one", "two"):
            word = SimpleNamespace(text=text, start_time=0.1, end_time=0.8)
            items.append(SimpleNamespace(text=text, language="", time_stamps=SimpleNamespace(items=[word])))
        engine._model.transcribe.return_value = items
        transcribe = ASRPipeline(engine, None)._transcribe_batched
    segments = transcribe(chunks, 2, None, None, None)
    assert [(s["text"], s["start"], s["end"]) for s in segments] == [
        ("one", 0.5, 1.5), ("two", 3.0, 4.0),
    ]
    if remote:
        assert all("words" not in s for s in segments)
    else:
        assert [s["words"] for s in segments] == [
            [{"text": "one", "start": 0.6, "end": 1.3}],
            [{"text": "two", "start": 3.1, "end": 3.8}],
        ]


def test_vllm_offline_aligned_result():
    from app.engines.asr_result import ASRResult, WordTime
    from app.engines.vllm_asr_engine import VLLMASREngine

    engine = VLLMASREngine(enable_align=True)
    engine._model = Mock()
    word = SimpleNamespace(text="hi", start_time=0.25, end_time=0.5)
    engine._model.transcribe.return_value = [
        SimpleNamespace(text="hi", language="English", time_stamps=SimpleNamespace(items=[word]))
    ]
    result = engine.transcribe("input.wav", with_words=True)
    assert result == ASRResult("hi", "English", (WordTime("hi", 0.25, 0.5),), "input.wav")
    assert engine.capabilities.realtime_incremental and not engine.capabilities.batch
