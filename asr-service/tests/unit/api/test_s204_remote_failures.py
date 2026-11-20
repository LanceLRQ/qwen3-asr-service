from pathlib import Path
from threading import Event
from unittest.mock import Mock

import httpx
import pytest

from app.api.compat import dashscope_routes, openai_routes
from app.api.compat.errors import register_compat_exception_handlers
from app.engines.remote_asr_engine import RemoteASREngine
from app.pipeline import asr_pipeline


@pytest.fixture
def remote_service(tmp_path, monkeypatch, make_client, make_wav, tm_factory):
    def create(upstream):
        transports = []
        init_transport = httpx.Client._init_transport

        def transport(client, **kwargs):
            if kwargs["transport"] is not None:
                return init_transport(client, **kwargs)
            remote_transport = httpx.MockTransport(upstream)
            remote_transport.close = Mock(wraps=remote_transport.close)
            transports.append(remote_transport)
            return remote_transport

        monkeypatch.setattr(httpx.Client, "_init_transport", transport)
        monkeypatch.setattr(openai_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))
        workspace = tmp_path / "chunks"
        workspace.mkdir()
        monkeypatch.setattr(asr_pipeline.cfg, "MAX_SEGMENT_DURATION", 1)
        engine = RemoteASREngine("http://upstream/v1", "qwen3-asr", "secret-key", 2)
        vad = Mock()
        vad.detect.return_value = [(100, 1000), (2000, 3000)]
        pipe = asr_pipeline.ASRPipeline(engine, vad, work_dir=workspace)
        manager = tm_factory(start=True, processor=lambda task: pipe.run(
            task["file_path"], cancelled=lambda: manager.is_cancelled(task["task_id"])))
        client = make_client(task_manager=manager)
        source = Path(make_wav(4))
        files = {"file": ("clip.wav", source.read_bytes(), "audio/wav")}
        return manager, client, source, files, transports

    return create


@pytest.mark.parametrize("cause", ["connection", "timeout", 401, 429, 503, "invalid"])
@pytest.mark.parametrize("endpoint", ["native", "openai", "dashscope"])
def test_upstream_failure_envelopes(cause, endpoint, tmp_path, monkeypatch,
                                    remote_service):
    errors = {
        "connection": "连接", "timeout": "超时", 401: "401", 429: "429",
        503: "503", "invalid": "格式",
    }
    requests = []

    def upstream(request):
        requests.append(request)
        assert request.method == "POST"
        if cause == "connection":
            raise httpx.ConnectError("private-detail", request=request)
        if cause == "timeout":
            raise httpx.ReadTimeout("private-detail", request=request)
        if cause == "invalid":
            return httpx.Response(200, json={"private-detail": True})
        return httpx.Response(cause, text="private-detail")

    manager, client, source, files, transports = remote_service(upstream)
    if endpoint == "dashscope":
        async def download(url, **kwargs):
            path = tmp_path / "download.wav"
            path.write_bytes(source.read_bytes())
            return str(path)

        monkeypatch.setattr(dashscope_routes, "_task_manager", manager)
        monkeypatch.setattr(dashscope_routes, "fetch_to_local", download)
        monkeypatch.setattr(dashscope_routes, "_registry", {})
        client.app.include_router(dashscope_routes.build_dashscope_router())
        base = "/compat/dashscope/api/v1"
        response = client.post(base + "/services/audio/asr/transcription",
                               headers={"X-DashScope-Async": "enable"},
                               json={"model": "test", "input": {"file_urls": ["https://example.org/a.wav"]}})
        response.raise_for_status()
        tid = response.json()["output"]["task_id"]
        inner = manager.list_tasks()[0]["task_id"]
        assert manager.wait_done(inner, 5)["status"] == "failed"
        parent = client.get(f"{base}/tasks/{tid}").json()["output"]
        assert parent["task_status"] == "FAILED"
        result, = parent["results"]
        assert result["code"] == "InternalError" and result["transcription_url"] is None
        assert not (tmp_path / "download.wav").exists()
        message = result["message"]
    elif endpoint == "openai":
        monkeypatch.setattr(openai_routes, "_task_manager", manager)
        register_compat_exception_handlers(client.app)
        client.app.include_router(openai_routes.build_openai_router())
        response = client.post("/compat/openai/v1/audio/transcriptions", files=files,
                               data={"model": "whisper-1"})
        assert response.status_code == 500
        error = response.json()["error"]
        assert error["code"] == "internal_error"
        message = error["message"]
    else:
        response = client.post("/v2/asr", files=files)
        response.raise_for_status()
        tid = response.json()["task_id"]
        assert manager.wait_done(tid, 5)
        result = client.get(f"/v2/tasks/{tid}").json()
        assert result["status"] == "failed" and result["result"] is None
        message = result["error"]
    assert errors[cause] in message
    assert "secret-key" not in message and "private-detail" not in message
    transport, = transports
    transport.close.assert_called_once()
    assert len(requests) == 1
    assert source.is_file()
    assert not list((tmp_path / "chunks").iterdir())
    if endpoint != "dashscope":
        assert not list((tmp_path / "uploads").iterdir())

@pytest.mark.parametrize("late_failure", [False, True])
def test_cancel_during_remote_post(remote_service, tmp_path, late_failure):
    started, release = Event(), Event()
    calls = []

    def upstream(request):
        calls.append(request)
        assert request.extensions["timeout"]["read"] == 2
        started.set()
        assert release.wait(5)
        if late_failure:
            raise httpx.ReadTimeout("late timeout", request=request)
        return httpx.Response(200, json={"text": "language English<asr_text>late"})

    manager, client, source, files, transports = remote_service(upstream)
    response = client.post("/v2/asr", files=files)
    response.raise_for_status()
    tid = response.json()["task_id"]
    try:
        assert started.wait(5)
        client.delete(f"/v2/tasks/{tid}").raise_for_status()
    finally:
        release.set()
    task = manager.wait_done(tid, 5)
    assert task["status"] == "cancelled"
    assert not (task["result"] or {}).get("segments")
    assert len(calls) == 1
    transport, = transports
    transport.close.assert_called_once()
    assert source.is_file()
    assert not list((tmp_path / "chunks").iterdir())
    assert not list((tmp_path / "uploads").iterdir())
