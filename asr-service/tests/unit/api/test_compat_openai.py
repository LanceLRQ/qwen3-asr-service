"""app/api/compat/openai_routes.py 集成测试（TestClient + 假 TaskManager）。

不加载模型；UPLOADS_DIR 重定向到 tmp。覆盖五种 response_format、占位字段、
timestamp_granularities[] alias、超时/失败/队列满/501/models、OpenAI 风格错误信封与鉴权。
"""
import queue

import pytest

import app.config as cfg

RESULT = {
    "segments": [
        {"start": 0.0, "end": 3.2, "text": "你好",
         "words": [{"text": "你", "start": 0.0, "end": 0.2}]},
        {"start": 3.2, "end": 5.0, "text": "世界"},
    ],
    "full_text": "你好世界",
    "language": "zh",
}


class FakeTM:
    def __init__(self, *, result=None, status="completed", error=None,
                 timeout=False, full=False):
        self.result = RESULT if result is None else result
        self.status = status
        self.error = error
        self.timeout = timeout
        self.full = full
        self.submitted = None

    def submit(self, **kwargs):
        if self.full:
            raise queue.Full()
        self.submitted = kwargs
        return "tid"

    def wait_done(self, task_id, timeout):
        if self.timeout:
            return None
        return {"status": self.status, "result": self.result, "error": self.error}


@pytest.fixture
def openai_client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.compat import openai_routes
    from app.api.compat.errors import register_compat_exception_handlers

    monkeypatch.setattr(openai_routes, "UPLOADS_DIR", str(tmp_path / "uploads"))

    def _make(task_manager=None, service_info=None, api_key=""):
        monkeypatch.setattr(cfg, "API_KEY", api_key)
        app = FastAPI()
        openai_routes.init_openai_routes(
            task_manager=task_manager,
            service_info=service_info or {"model_size": "0.6b"})
        register_compat_exception_handlers(app)
        app.include_router(openai_routes.build_openai_router())
        return TestClient(app)

    return _make


def _post(client, *, data=None, fmt=None):
    payload = {"model": "whisper-1"}
    if fmt:
        payload["response_format"] = fmt
    if data:
        payload.update(data)
    return client.post(
        "/compat/openai/v1/audio/transcriptions",
        files={"file": ("a.wav", b"abcdef", "audio/wav")},
        data=payload,
    )


# ─── response_format ───

def test_json_default(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client)
    assert r.status_code == 200
    assert r.json() == {"text": "你好世界"}


def test_webm_upload_allowed(openai_client):
    tm = FakeTM()
    client = openai_client(task_manager=tm)
    r = client.post(
        "/compat/openai/v1/audio/transcriptions",
        files={"file": ("browser.webm", b"abcdef", "audio/webm")},
        data={"model": "whisper-1"},
    )
    assert r.status_code == 200
    assert tm.submitted["file_path"].endswith(".webm")
    assert tm.submitted["wav_name"] == "browser.webm"


def test_text_format(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, fmt="text")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text == "你好世界"


def test_verbose_json_placeholders(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, fmt="verbose_json")
    body = r.json()
    assert body["task"] == "transcribe" and body["language"] == "zh"
    assert body["duration"] == 5.0
    seg = body["segments"][0]
    assert seg["id"] == 0 and seg["avg_logprob"] == 0.0 and seg["tokens"] == []
    assert "words" not in body   # 未请求 word 粒度


def test_verbose_json_word_granularity_alias(openai_client):
    """timestamp_granularities[] 带 [] 的字段名经 FastAPI alias 正确解析（含多值）。"""
    client = openai_client(task_manager=FakeTM())
    r = client.post(
        "/compat/openai/v1/audio/transcriptions",
        files={"file": ("a.wav", b"abcdef", "audio/wav")},
        data={"model": "whisper-1", "response_format": "verbose_json",
              "timestamp_granularities[]": ["word", "segment"]},
    )
    assert r.status_code == 200
    assert r.json()["words"] == [{"word": "你", "start": 0.0, "end": 0.2}]


def test_word_granularity_sets_with_words_option(openai_client):
    tm = FakeTM()
    client = openai_client(task_manager=tm)
    client.post(
        "/compat/openai/v1/audio/transcriptions",
        files={"file": ("a.wav", b"abcdef", "audio/wav")},
        data={"model": "whisper-1", "timestamp_granularities[]": "word"},
    )
    assert tm.submitted["options"] == {"with_words": True}


def test_srt_format(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, fmt="srt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert "00:00:00,000 --> 00:00:03,200" in r.text


def test_vtt_format(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, fmt="vtt")
    assert "WEBVTT" in r.text and "00:00:00.000 --> 00:00:03.200" in r.text


# ─── 错误路径（OpenAI 风格信封） ───

def test_invalid_response_format_400(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, fmt="bogus")
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["code"] == "invalid_value" and err["param"] == "response_format"


def test_stream_true_returns_sse(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = _post(client, data={"stream": "true"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    body = r.text
    assert "transcript.text.delta" in body
    assert "transcript.text.done" in body
    assert "你好世界" in body          # done 携带全文（非 ASCII 不转义）


def test_timeout_504(openai_client):
    client = openai_client(task_manager=FakeTM(timeout=True))
    r = _post(client)
    assert r.status_code == 504
    assert r.json()["error"]["code"] == "timeout"


def test_failed_500(openai_client):
    client = openai_client(task_manager=FakeTM(status="failed", error="boom"))
    r = _post(client)
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "internal_error"


def test_queue_full_503_overloaded(openai_client):
    client = openai_client(task_manager=FakeTM(full=True))
    r = _post(client)
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "overloaded"


def test_translations_501(openai_client):
    client = openai_client(task_manager=FakeTM())
    r = client.post(
        "/compat/openai/v1/audio/translations",
        files={"file": ("a.wav", b"abcdef", "audio/wav")},
        data={"model": "whisper-1"},
    )
    assert r.status_code == 501
    assert r.json()["error"]["code"] == "unsupported"


# ─── models ───

def test_list_models(openai_client):
    client = openai_client(task_manager=FakeTM(), service_info={"model_size": "1.7b"})
    r = client.get("/compat/openai/v1/models")
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    assert body["data"][0]["id"] == "qwen3-asr-1.7b"


# ─── 鉴权（OpenAI 风格 401） ───

def test_auth_missing_token_401(openai_client):
    client = openai_client(task_manager=FakeTM(), api_key="sk-secret")
    r = _post(client)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_api_key"


def test_auth_valid_token_200(openai_client):
    client = openai_client(task_manager=FakeTM(), api_key="sk-secret")
    r = client.post(
        "/compat/openai/v1/audio/transcriptions",
        files={"file": ("a.wav", b"abcdef", "audio/wav")},
        data={"model": "whisper-1"},
        headers={"Authorization": "Bearer sk-secret"},
    )
    assert r.status_code == 200
