from unittest.mock import Mock
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_main_serve_mode import _mock_standard_engines, isolated_create_app


@pytest.mark.parametrize(("device", "align"), [("cuda", False), ("cpu", False),
                                               ("cpu", True), ("cuda", "failure")])
def test_remote_assembly_and_transcription(isolated_create_app, monkeypatch, tmp_path, caplog, device, align):
    import app.main as main

    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr(main, "QwenASREngine", lambda **kw: pytest.fail("local ASR"))
    monkeypatch.setattr("app.engines.openvino_asr_engine.OpenVINOASREngine",
                        lambda **kw: pytest.fail("local ASR"))
    monkeypatch.setattr(main, "resolve_device", lambda *a, **kw: device)
    for path in ("app.engines.qwen_asr_engine.ensure_model",
                 "app.utils.openvino_model_downloader.ensure_openvino_model"):
        monkeypatch.setattr(path, lambda *a, **kw: pytest.fail("local ASR download"))
    loads = []
    def load_alignment(where):
        loads.append(where)
        if align == "failure":
            raise RuntimeError("failed")
        return object()
    monkeypatch.setattr("app.engines.forced_alignment.ForcedAlignment.load", load_alignment)
    engine = {}
    def capture_pipeline(**kwargs):
        engine.update(kwargs)
        return Mock(spec=["run"])

    monkeypatch.setattr(main, "ASRPipeline", capture_pipeline)
    requests = []

    def upstream(request):
        requests.append(request)
        assert set(request.extensions["timeout"].values()) == {3.0}
        assert request.headers["authorization"] == "Bearer outbound-secret"
        if request.method == "GET":
            assert request.url.path == "/v1/models"
            return httpx.Response(200, json={"data": [{"id": "qwen3-asr"}]})
        assert request.method == "POST"
        assert request.url.path == "/v1/audio/transcriptions"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="model"\r\n\r\nqwen3-asr' in request.content
        assert b'name="language"\r\n\r\nen' in request.content
        assert b'name="file"' in request.content and b"test-audio" in request.content
        return httpx.Response(200, json={"text": "language English<asr_text>Hello.", "usage": {"seconds": 8}})

    monkeypatch.setattr(httpx.Client, "_init_transport",
                        lambda *a, **kw: kw.get("transport") or httpx.MockTransport(upstream))
    args = main.parse_args(["--no-config", "--serve-mode", "remote", "--remote-base-url",
                            "http://upstream/v1/", "--remote-model", "qwen3-asr",
                            "--remote-api-key", "outbound-secret", "--remote-timeout", "3",
                            "--enable-stream", "--web", "--no-punc", "--enable-openai-api", "--enable-dashscope-api"]
                           + (["--remote-enable-align"] if align else []))
    monkeypatch.setattr(main.cfg, "ENABLE_WEB", False)
    monkeypatch.setattr(main, "setup_logger", lambda: None)
    monkeypatch.setattr(main.cfg, "ENABLE_OPENAI_API", False)
    with caplog.at_level(logging.INFO):
        app = main.create_app(args)
    client = TestClient(app)
    health = client.get("/v2/health").json()
    assert health["mode"] == health["asr_backend"] == "remote"
    assert health["align_enabled"] is (align is True)
    assert bool(engine["aligner"]) == (align is True)
    assert loads == (["cuda:0" if device == "cuda" else "cpu"] if align else [])
    capabilities = client.get("/v1/capabilities").json()
    assert capabilities["mode"] == "remote" and capabilities["offline_api"]
    assert capabilities["stream"]["enabled"] is True
    assert capabilities["stream"]["backend"] == "vad-offline"
    assert capabilities["stream"]["partial_results"] is False
    assert capabilities["stream"]["word_timestamps"] is False
    assert capabilities["compat"]["openai"] and capabilities["compat"]["realtime"]
    assert client.get("/compat/openai/v1/models").json()["data"][0]["id"] == "qwen3-asr"
    paths = {route.path for route in app.routes}
    assert "/v2/asr/stream" in paths
    assert "/compat/openai/v1/realtime" in paths
    assert "/compat/dashscope/api-ws/v1/inference" in paths
    assert client.get("/web-ui", follow_redirects=True).status_code == 200
    wav = tmp_path / "short.wav"
    wav.write_bytes(b"test-audio")
    asr = engine["asr_engine"]
    assert asr.is_remote
    result = asr.transcribe(str(wav), language="English")
    assert (result.language, result.text, result.words) == ("English", "Hello.", None)
    assert len(requests) == 2
    assert "remote_api_key" in caplog.text
    assert "outbound-secret" not in caplog.text


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_remote_requires_finite_positive_timeout(timeout):
    from app.engines.remote_asr_engine import RemoteASREngine

    with pytest.raises(ValueError, match="remote_timeout"):
        RemoteASREngine("http://upstream/v1", "qwen3-asr", "", timeout)


@pytest.mark.parametrize(("language", "hint"), [
    ("Filipino", "tl"), ("fil", "tl"), ("tl", "tl"), ("Cantonese", None),
    ("yue", None), ("zh-CN", "zh"), ("en", "en"), ("unknown", None),
])
def test_multipart_language_hint(language, hint):
    from app.utils.language import to_iso_language

    assert to_iso_language(language) == hint


def test_remote_credential_precedence(monkeypatch, tmp_path):
    from app.main import parse_args

    monkeypatch.setenv("ASR_REMOTE_API_KEY", "from-env")
    assert parse_args(["--no-config"]).remote_api_key == "from-env"
    config = tmp_path / "config.yaml"
    config.write_text("remote_api_key: from-file\n")
    assert parse_args(["--config", str(config)]).remote_api_key == "from-file"
    assert parse_args(["--config", str(config), "--remote-api-key", "from-cli"]).remote_api_key == "from-cli"


def test_invalid_upstream_response_has_public_error(monkeypatch, tmp_path):
    from app.engines.remote_asr_engine import RemoteASREngine, RemoteASRError

    def invalid_response(request):
        return httpx.Response(200, text="private upstream details")

    monkeypatch.setattr(httpx.Client, "_init_transport",
                        lambda *a, **kw: httpx.MockTransport(invalid_response))
    chunk = tmp_path / "chunk.wav"
    chunk.touch()
    engine = RemoteASREngine("http://upstream/v1", "qwen3-asr", "secret", 1)
    with pytest.raises(RemoteASRError, match="格式") as error:
        engine.transcribe(str(chunk))
    assert "private" not in str(error.value) and "secret" not in str(error.value)


def test_remote_sentence_wav(monkeypatch):
    import io
    import wave
    import numpy as np
    from app.engines.remote_asr_engine import RemoteASREngine

    engine = RemoteASREngine("http://upstream/v1", "qwen", "", 1)

    def request(method, path, *, data, files):
        assert (method, path) == ("POST", "/audio/transcriptions")
        assert data == {"model": "qwen", "language": "en"}
        with wave.open(io.BytesIO(files["file"][1].read())) as wav:
            assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
            assert wav.readframes(2) == b"\x00\xc0\x00\x40"
        return {"text": "language English<asr_text>Hello"}

    monkeypatch.setattr(engine, "_request", request)
    result = engine.transcribe_array(np.array([-.5, .5]), 16000, "English", "2")
    assert (result.chunk_id, result.language, result.text) == ("2", "English", "Hello")


@pytest.mark.parametrize(("enabled", "compat"), [(True, True), (True, False), (False, True)])
def test_remote_stream_origin_assembly(isolated_create_app, monkeypatch, tmp_path, enabled, compat):
    from app.runtime import remote_stream_session
    import app.main as main
    from app.api import ws_routes
    from app.api.compat import ws_bridge

    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr("app.engines.remote_asr_engine.RemoteASREngine.load", lambda self: None)
    factory = Mock(wraps=remote_stream_session.RemoteStreamBackend)
    monkeypatch.setattr(remote_stream_session, "RemoteStreamBackend", factory)
    monkeypatch.setattr(main.cfg, "STREAM_MAX_SEGMENT_SEC", 7)
    config = tmp_path / "remote.yaml"
    config.write_text("serve_mode: remote\nremote_base_url: http://offline/v1\n"
                      "remote_stream_url: http://demo:8000\n")
    assert main.parse_args(["--config", str(config)]).remote_stream_url == "http://demo:8000"
    args = main.parse_args(["--config", str(config), "--remote-stream-url", "http://demo:8080",
                            "--remote-timeout", "3", "--max-stream-sessions", "8",
                            "--enable-stream" if enabled else "--no-stream"]
                           + (["--enable-openai-api", "--enable-dashscope-api"] if compat else []))
    with TestClient(main.create_app(args)) as client:
        caps = client.get("/v2/capabilities").json()
        assert client.get("/v1/health").json()["capabilities"] == caps
        assert caps["mode"] == "remote" and caps["offline_api"]
        stream = caps["stream"]
        assert stream["enabled"] is enabled
        assert stream["partial_results"] is enabled
        assert stream["backend"] == ("remote-qwen-streaming" if enabled else None)
        assert not any(stream[key] for key in ("word_timestamps", "speaker_labels", "scene"))
        assert caps["compat"]["realtime_partial"] is (enabled and compat)
        if enabled:
            factory.assert_called_once_with(
                "http://demo:8080", 3, max_sessions=1, max_utterance_sec=7)
            backend = ws_routes._backend
            assert backend.mode == "remote"
            assert stream["max_sessions"] == 1
            if compat:
                assert ws_bridge._backend is backend
        else:
            factory.assert_not_called()
