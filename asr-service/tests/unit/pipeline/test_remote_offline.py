from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.asr_result import ASRResult, BackendCapabilities
from app.api.compat import openai_routes
from app.pipeline import asr_pipeline as pipeline


@pytest.fixture
def remote_pipe(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline.cfg, "MAX_SEGMENT_DURATION", 1)
    remote = SimpleNamespace(
        is_remote=True, capabilities=BackendCapabilities(),
        batch_transcribe=Mock(side_effect=AssertionError("remote batch invoked")),
        transcribe=Mock(side_effect=[
            ASRResult("第一句。", language=None),
            ASRResult("第二句。", language="Chinese"),
        ]),
    )
    vad = SimpleNamespace(detect=lambda path: [(500, 1500), (2500, 3500)])
    pipe = pipeline.ASRPipeline(remote, vad, speaker_engine=object())
    diar = SimpleNamespace(labels_in_order=["A", "B"],
                           label_for=lambda start, end: "A" if start < 2 else "B")
    monkeypatch.setattr(pipe, "_run_diarization", lambda path, spans: diar)
    return pipe


@pytest.mark.parametrize("compat", [False, True], ids=["native", "openai"])
def test_remote_http_entries(compat, remote_pipe, make_client, tm_factory, make_wav,
                             tmp_path, monkeypatch):
    def process(task):
        return remote_pipe.run(task["file_path"], language=task.get("language"),
                               options=task.get("options"))

    tm = tm_factory(start=True, processor=process)
    client = make_client(task_manager=tm)
    files = {"file": ("input.wav", Path(make_wav(4)).read_bytes(), "audio/wav")}
    if compat:
        monkeypatch.setattr(openai_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
        openai_routes.init_openai_routes(task_manager=tm)
        client.app.include_router(openai_routes.build_openai_router())
        response = client.post("/compat/openai/v1/audio/transcriptions", files=files,
                               data={"model": "whisper-1", "response_format": "verbose_json",
                                     "timestamp_granularities[]": "word"})
        response.raise_for_status()
        result = response.json()
        assert result["text"] == "第一句。第二句。"
        assert "words" not in result
    else:
        response = client.post("/v2/asr", files=files, data={"with_words": "true"})
        response.raise_for_status()
        tid = response.json()["task_id"]
        assert tm.wait_done(tid, 5)["status"] == "completed"
        result = client.get(f"/v2/tasks/{tid}").json()["result"]
        assert result["full_text"] == "第一句。第二句。"
        assert result["speakers"] == ["A", "B"]
        assert [s["speaker"] for s in result["segments"]] == ["A", "B"]
        assert result["warnings"] == ["with_words"]
    assert result["language"] == "Chinese"
    assert [(s["start"], s["end"]) for s in result["segments"]] == [(0.5, 1.5), (2.5, 3.5)]
    assert all("words" not in s for s in result["segments"])
    remote_pipe.asr.batch_transcribe.assert_not_called()


def test_remote_failure_stops_at_failed_chunk(remote_pipe, make_wav):
    remote_pipe.asr.transcribe.side_effect = TimeoutError("upstream")
    with pytest.raises(TimeoutError, match="upstream"):
        remote_pipe.run(make_wav(4))
    remote_pipe.asr.transcribe.assert_called_once()
    remote_pipe.asr.batch_transcribe.assert_not_called()


def test_remote_cancel_during_request(remote_pipe, make_wav):
    result = remote_pipe.run(make_wav(4),
                             cancelled=lambda: remote_pipe.asr.transcribe.called)
    remote_pipe.asr.transcribe.assert_called_once()
    assert result["segments"] == []


def test_remote_aligns_final_text_on_each_chunk(remote_pipe, make_wav):
    import soundfile as sf

    seen = []

    def align(path, text, language):
        assert sf.info(path).duration == 1
        seen.append((text, language))
        return [{"text": text.rstrip("。"), "start": 0.1, "end": 0.8}]

    remote_pipe.aligner = SimpleNamespace(align=align)
    remote_pipe.punc = SimpleNamespace(restore=lambda s: s.replace("句", "段"))
    result = remote_pipe.run(make_wav(4), language="Chinese")
    assert result["align_enabled"]
    assert seen == [("第一段。", "Chinese"), ("第二段。", "Chinese")]
    assert [s["words"][0]["start"] for s in result["segments"]] == [0.6, 2.6]
    assert "warnings" not in result


@pytest.mark.parametrize("requested", [False, True])
def test_remote_aligner_failure_and_request_opt_out(remote_pipe, make_wav, requested):
    align = Mock(side_effect=ValueError("unsupported language"))
    remote_pipe.aligner = SimpleNamespace(align=align)
    result = remote_pipe.run(make_wav(4), options={"with_words": requested})
    assert result["full_text"] == "第一句。第二句。"
    assert result["align_enabled"]
    assert all("words" not in seg for seg in result["segments"])
    assert result.get("warnings", []) == (["with_words"] if requested else [])
    assert align.call_count == (2 if requested else 0)


@pytest.mark.parametrize("left,right,expected", [
    ("at noon", "please wait", "at noon please wait"),
    ("到中午", "请稍候", "到中午请稍候"),
    ("At noon.", "Please wait.", "At noon.Please wait."),
    ("at noon ", "please wait", "at noon please wait"),
])
@pytest.mark.parametrize("pause_ms", [100, 1000])
def test_word_boundary_between_vad_chunks(remote_pipe, make_wav, left, right, expected, pause_ms):
    remote_pipe.asr.transcribe.side_effect = [ASRResult(left), ASRResult(right)]
    remote_pipe.vad.detect = lambda _: [(0, 1000), (1000 + pause_ms, 2000 + pause_ms)]
    result = remote_pipe.run(make_wav(4), options={"diarize": False})
    assert result["full_text"] == expected
    if pause_ms == 100 and not left.endswith("."):
        assert [s["text"] for s in result["segments"]] == [expected]


def test_punctuated_chunk_trailing_space_uses_final_segments(remote_pipe, make_wav):
    remote_pipe.asr.transcribe.side_effect = [ASRResult("yellow gag. "), ASRResult("The train.")]
    result = remote_pipe.run(make_wav(4), options={"diarize": False})
    assert [seg["text"] for seg in result["segments"]] == ["yellow gag.", "The train."]
    assert result["full_text"] == "yellow gag.The train."


def test_only_unpunctuated_boundary_adds_space(remote_pipe, make_wav):
    remote_pipe.vad.detect = lambda _: [(0, 500), (1500, 2000), (3000, 3500)]
    remote_pipe.asr.transcribe.side_effect = [
        ASRResult("yellow gag. "), ASRResult("The train"), ASRResult("leaves soon"),
    ]
    result = remote_pipe.run(make_wav(4), options={"diarize": False})
    assert result["full_text"] == "yellow gag.The train leaves soon"
