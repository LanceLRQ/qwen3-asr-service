"""标准离线管线分句集成测试：处理切块边界 ≠ 句子边界（evolution.md §二.4）。

验证：单个 ASR 处理块内的多句标点会被重组为多个句子级 segment（不再 1 块 1 段），
且默认不按 MAX_SEGMENT_DURATION 把句子拦腰切断。
"""
import types

import numpy as np
import pytest

from app.engines.asr_result import ASRResult, BackendCapabilities
from app.pipeline.asr_pipeline import ASRPipeline


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "app.pipeline.asr_pipeline.sf.read",
        lambda p, dtype=None: (np.zeros(16000 * 4, dtype=np.float32), 16000),
    )
    monkeypatch.setattr("app.pipeline.asr_pipeline.sf.write", lambda *a, **k: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.convert_to_wav", lambda a, b: None)
    monkeypatch.setattr("app.pipeline.asr_pipeline.get_audio_duration", lambda p: 4.0)
    return tmp_path


def _pipe(text):
    asr = types.SimpleNamespace(capabilities=BackendCapabilities(),
                                transcribe=lambda audio_path, language: ASRResult(text))
    vad = types.SimpleNamespace(detect=lambda p: [(0, 4000)])   # 单个处理块 0–4s
    return ASRPipeline(asr_engine=asr, vad_engine=vad, punc_engine=None)


def test_single_chunk_splits_into_sentences(run_env, tmp_path):
    pipe = _pipe("第一句。第二句。")
    result = pipe.run(str(tmp_path / "a.mp3"))
    assert [s["text"] for s in result["segments"]] == ["第一句。", "第二句。"]
    # 时间戳按字符比例落在处理块 [0,4] 内、单调
    assert result["segments"][0]["start"] == pytest.approx(0.0)
    assert result["segments"][-1]["end"] == pytest.approx(4.0)
    assert result["full_text"] == "第一句。第二句。"


def test_long_chunk_not_cut_by_duration_default(run_env, tmp_path):
    # 单句无内部句末标点：默认不应被 MAX_SEGMENT_DURATION 拦腰切断 → 仍是 1 段
    pipe = _pipe("这是一句没有句末标点的较长句子")
    result = pipe.run(str(tmp_path / "a.mp3"))
    assert len(result["segments"]) == 1
    assert result["segments"][0]["text"] == "这是一句没有句末标点的较长句子"


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("requested", [False, True])
def test_local_words_use_backend_once_after_punctuation(run_env, batch, requested):
    from unittest.mock import Mock
    from app.engines.asr_result import WordTime

    native = ASRResult("你好", "Chinese", (WordTime("你好", 0.2, 0.5),))
    engine = types.SimpleNamespace(
        capabilities=BackendCapabilities(word_timestamps=True, batch=batch),
        batch_transcribe=Mock(return_value=[native]),
        transcribe=Mock(return_value=native))
    aligner = Mock()
    pipe = ASRPipeline(engine, types.SimpleNamespace(detect=lambda _: [(1000, 3000)]),
                       types.SimpleNamespace(restore=lambda text: text + "。"),
                       aligner=aligner, work_dir=run_env)
    result = pipe.run("input.wav", options={"with_words": requested})
    assert result["align_enabled"] and "warnings" not in result
    segment, = result["segments"]
    assert segment["text"] == "你好。"
    if requested:
        assert segment["words"] == [{"text": "你好", "start": 1.2, "end": 1.5}]
    else:
        assert "words" not in segment
    method = engine.batch_transcribe if batch else engine.transcribe
    assert method.call_args.kwargs["with_words"] is requested
    aligner.align.assert_not_called()
