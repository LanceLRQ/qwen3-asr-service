from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.engines.asr_result import ASRResult, BackendCapabilities
from app.api.compat import openai_routes
from app.pipeline import asr_pipeline as pipeline


@pytest.fixture
def remote_pipe(monkeypatch, tmp_path):
    monkeypatch.setattr(pipeline, "UPLOADS_DIR", str(tmp_path / "wav"))
    monkeypatch.setattr(pipeline, "AUDIO_CHUNKS_DIR", str(tmp_path / "chunks"))
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
        return remote_pipe.run(task["file_path"], task["task_id"], task.get("language"),
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
        remote_pipe.run(make_wav(4), "failure")
    remote_pipe.asr.transcribe.assert_called_once()
    remote_pipe.asr.batch_transcribe.assert_not_called()


def test_remote_cancel_during_request(remote_pipe, make_wav):
    result = remote_pipe.run(make_wav(4), "cancel",
                             cancelled=lambda: remote_pipe.asr.transcribe.called)
    remote_pipe.asr.transcribe.assert_called_once()
    assert result["segments"] == []
