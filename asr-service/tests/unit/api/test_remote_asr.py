import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from .test_main_serve_mode import _mock_standard_engines, isolated_create_app


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_remote_assembly_and_transcription(isolated_create_app, monkeypatch, tmp_path, caplog, device):
    import app.main as main

    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr(main, "QwenASREngine", lambda **kw: pytest.fail("local ASR"))
    monkeypatch.setattr("app.engines.openvino_asr_engine.OpenVINOASREngine",
                        lambda **kw: pytest.fail("local ASR"))
    monkeypatch.setattr(main, "resolve_device", lambda *a, **kw: device)
    for path in ("app.engines.qwen_asr_engine.ensure_model",
                 "app.utils.openvino_model_downloader.ensure_openvino_model"):
        monkeypatch.setattr(path, lambda *a, **kw: pytest.fail("local ASR download"))
    engine = {}
    monkeypatch.setattr(main, "ASRPipeline", lambda **kw: engine.update(kw))
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
                            "--enable-stream", "--web", "--no-punc", "--enable-openai-api"])
    monkeypatch.setattr(main.cfg, "ENABLE_WEB", False)
    monkeypatch.setattr(main, "setup_logger", lambda: None)
    monkeypatch.setattr(main.cfg, "ENABLE_OPENAI_API", False)
    with caplog.at_level(logging.INFO):
        app = main.create_app(args)
    client = TestClient(app)
    health = client.get("/v2/health").json()
    assert health["mode"] == health["asr_backend"] == "remote"
    assert health["align_enabled"] is False
    capabilities = client.get("/v1/capabilities").json()
    assert capabilities["mode"] == "remote" and capabilities["offline_api"]
    assert capabilities["stream"]["enabled"] is False
    assert capabilities["compat"]["openai"] and not capabilities["compat"]["realtime"]
    paths = {route.path for route in app.routes}
    assert "/v2/asr/stream" not in paths
    assert "/compat/openai/v1/realtime" not in paths
    assert client.get("/web-ui", follow_redirects=True).status_code == 200
    wav = tmp_path / "short.wav"
    wav.write_bytes(b"test-audio")
    asr = engine["asr_engine"]
    assert asr.is_remote
    result, = asr.transcribe(str(wav), language="English")
    assert (result.language, result.text, result.time_stamps) == ("English", "Hello.", None)
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
