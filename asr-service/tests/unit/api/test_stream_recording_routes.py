from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_client(manager, monkeypatch, api_key="sk-secret"):
    import app.config as cfg
    from app.api.stream_recording_routes import (
        build_stream_recordings_router,
        init_stream_recording_routes,
    )

    monkeypatch.setattr(cfg, "API_KEY", api_key)
    init_stream_recording_routes(manager)
    app = FastAPI()
    app.include_router(build_stream_recordings_router())
    return TestClient(app)


def test_download_and_delete_stream_recording(tmp_path, monkeypatch):
    from app.runtime.stream_recording import StreamRecordingManager

    manager = StreamRecordingManager(
        enabled=True,
        directory=str(tmp_path / "recordings"),
        retention_hours=72,
    )
    recorder = manager.start(wav_name="meeting", sample_rate=16000)
    recorder.write(b"\x00\x00")
    recorder.close()

    client = _make_client(manager, monkeypatch)
    headers = {"Authorization": "Bearer sk-secret"}

    resp = client.get(
        f"/v2/stream-recordings/{recorder.info['recording_id']}",
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/wav")
    assert resp.content.startswith(b"RIFF")

    deleted = client.delete(
        f"/v2/stream-recordings/{recorder.info['recording_id']}",
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json() == {
        "recording_id": recorder.info["recording_id"],
        "status": "deleted",
        "deleted": True,
    }

    missing = client.get(
        f"/v2/stream-recordings/{recorder.info['recording_id']}",
        headers=headers,
    )
    assert missing.status_code == 404


def test_stream_recording_routes_require_api_key(tmp_path, monkeypatch):
    from app.runtime.stream_recording import StreamRecordingManager

    manager = StreamRecordingManager(
        enabled=True,
        directory=str(tmp_path / "recordings"),
        retention_hours=72,
    )
    client = _make_client(manager, monkeypatch)

    assert client.get("/v2/stream-recordings/not-found").status_code == 401

    no_key_client = _make_client(manager, monkeypatch, api_key="")
    assert no_key_client.get("/v2/stream-recordings/not-found").status_code == 503
