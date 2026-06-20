import os
import time
import wave


def test_stream_recording_manager_writes_wav_and_deletes(tmp_path):
    from app.runtime.stream_recording import StreamRecordingManager

    manager = StreamRecordingManager(
        enabled=True,
        directory=str(tmp_path / "recordings"),
        retention_hours=72,
    )

    recorder = manager.start(wav_name="../mic input", sample_rate=8000)
    assert recorder is not None
    assert recorder.info["recording_id"]
    assert recorder.info["wav_name"] == "mic input.wav"

    recorder.write(b"\x01\x00\x02\x00")
    recorder.close()

    path = manager.path_for(recorder.info["recording_id"])
    assert path is not None
    with wave.open(str(path), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 8000
        assert wf.getnframes() == 2

    assert manager.delete(recorder.info["recording_id"]) is True
    assert manager.delete(recorder.info["recording_id"]) is False


def test_stream_recording_manager_cleanup_uses_hours(tmp_path):
    from app.runtime.stream_recording import StreamRecordingManager

    manager = StreamRecordingManager(
        enabled=True,
        directory=str(tmp_path / "recordings"),
        retention_hours=1,
    )
    recorder = manager.start(wav_name="old", sample_rate=16000)
    recorder.close()
    path = manager.path_for(recorder.info["recording_id"])
    old_ts = time.time() - 7200
    os.utime(path, (old_ts, old_ts))

    assert manager.cleanup_expired() == 1
    assert manager.path_for(recorder.info["recording_id"]) is None


def test_stream_recording_manager_disabled_is_noop(tmp_path):
    from app.runtime.stream_recording import StreamRecordingManager

    manager = StreamRecordingManager(
        enabled=False,
        directory=str(tmp_path / "recordings"),
        retention_hours=72,
    )

    assert manager.start(wav_name="x", sample_rate=16000) is None
    assert manager.cleanup_expired() == 0
