"""app/api/compat/dashscope_routes.py 集成测试（TestClient + 假 TaskManager + mock fetch）。

DashScope 下载+入队走 BackgroundTasks，TestClient 会在响应后执行并等待其完成，故提交返回
即可轮询。覆盖：提交校验、全链路提交→轮询→二跳、多 file_urls、下载失败/队列满隔离、
GET/POST 轮询、external base url / X-Forwarded、缺 async header、超量、鉴权、未知 task。
"""
import queue

import pytest

import app.config as cfg
from app.api.compat.fetch import FetchError

SUBMIT_URL = "/compat/dashscope/api/v1/services/audio/asr/transcription"

RESULT = {
    "segments": [
        {"start": 0.0, "end": 3.2, "text": "你好", "speaker": "A",
         "words": [{"text": "你", "start": 0.0, "end": 0.2}]},
        {"start": 3.2, "end": 5.0, "text": "世界"},
    ],
    "full_text": "你好世界",
    "language": "zh",
}


class FakeTM:
    def __init__(self, *, status="completed", result=None, full=False):
        self.status = status
        self.result = RESULT if result is None else result
        self.full = full
        self.counter = 0
        self.submitted = []

    def submit(self, **kwargs):
        if self.full:
            raise queue.Full()
        tid = f"inner-{self.counter}"
        self.counter += 1
        self.submitted.append(kwargs)
        return tid

    def get_task(self, task_id):
        return {"status": self.status, "result": self.result}


async def _ok_fetch(url, **kwargs):
    return f"/fake/{url.rsplit('/', 1)[-1]}"


@pytest.fixture
def ds_client(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.compat import dashscope_routes
    from app.api.compat.errors import register_compat_exception_handlers

    def _make(task_manager=None, api_key="", fetch=_ok_fetch):
        monkeypatch.setattr(cfg, "API_KEY", api_key)
        monkeypatch.setattr(dashscope_routes, "fetch_to_local", fetch)
        dashscope_routes._registry.clear()
        dashscope_routes.init_dashscope_routes(task_manager=task_manager)
        app = FastAPI()
        register_compat_exception_handlers(app)
        app.include_router(dashscope_routes.build_dashscope_router())
        return TestClient(app)

    return _make


def _submit(client, urls, *, params=None, async_header=True, headers=None):
    body = {"model": "paraformer-v2", "input": {"file_urls": urls}}
    if params:
        body["parameters"] = params
    h = dict(headers or {})
    if async_header:
        h["X-DashScope-Async"] = "enable"
    return client.post(SUBMIT_URL, json=body, headers=h)


# ─── 提交校验 ───

def test_submit_missing_async_header_400(ds_client):
    client = ds_client(task_manager=FakeTM())
    r = _submit(client, ["http://x/a.wav"], async_header=False)
    assert r.status_code == 400
    body = r.json()
    assert body["code"] == "InvalidParameter" and "request_id" in body


def test_submit_empty_file_urls_400(ds_client):
    client = ds_client(task_manager=FakeTM())
    r = _submit(client, [])
    assert r.status_code == 400
    assert r.json()["code"] == "InvalidParameter"


def test_submit_too_many_file_urls_400(ds_client):
    client = ds_client(task_manager=FakeTM())
    r = _submit(client, [f"http://x/{i}.wav" for i in range(17)])
    assert r.status_code == 400
    assert r.json()["code"] == "InvalidParameter"


def test_submit_returns_pending_task_id(ds_client):
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    r = _submit(client, ["http://x/a.wav"])
    assert r.status_code == 200
    out = r.json()["output"]
    assert out["task_status"] == "PENDING" and out["task_id"]
    # BackgroundTasks 已执行：fetch+submit 完成
    assert len(tm.submitted) == 1


# ─── 全链路 submit → poll → 二跳 ───

def test_full_flow_succeeded(ds_client):
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]

    poll = client.get(f"/compat/dashscope/api/v1/tasks/{tid}")
    out = poll.json()["output"]
    assert out["task_status"] == "SUCCEEDED"
    assert out["task_metrics"] == {"TOTAL": 1, "SUCCEEDED": 1, "FAILED": 0}
    url = out["results"][0]["transcription_url"]
    assert url and url.endswith(f"/tasks/{tid}/transcription/0")

    doc = client.get(f"/compat/dashscope/api/v1/tasks/{tid}/transcription/0").json()
    assert doc["transcripts"][0]["text"] == "你好世界"
    assert doc["transcripts"][0]["sentences"][0]["begin_time"] == 0
    assert doc["transcripts"][0]["sentences"][0]["end_time"] == 3200


def test_poll_via_post(ds_client):
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    poll = client.post(f"/compat/dashscope/api/v1/tasks/{tid}")
    assert poll.status_code == 200
    assert poll.json()["output"]["task_status"] == "SUCCEEDED"


def test_poll_running_status(ds_client):
    tm = FakeTM(status="processing")
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    out = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]
    assert out["task_status"] == "RUNNING"
    assert out["results"][0]["transcription_url"] is None


def test_multi_file_urls(ds_client):
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav", "http://x/b.wav"]).json()["output"]["task_id"]
    assert len(tm.submitted) == 2
    out = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]
    assert out["task_metrics"]["TOTAL"] == 2 and out["task_status"] == "SUCCEEDED"
    assert len(out["results"]) == 2


# ─── 失败隔离 ───

def test_download_failure_isolated(ds_client):
    async def fail_fetch(url, **kwargs):
        raise FetchError("FetchForbidden", "blocked private")
    tm = FakeTM()
    client = ds_client(task_manager=tm, fetch=fail_fetch)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    out = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]
    assert out["task_status"] == "FAILED"
    sub = out["results"][0]
    assert sub["subtask_status"] == "FAILED" and sub["code"] == "FetchForbidden"
    assert tm.submitted == []   # 下载失败不入队


def test_partial_failure(ds_client):
    async def selective_fetch(url, **kwargs):
        if url.endswith("bad.wav"):
            raise FetchError("FetchTooLarge", "too big")
        return f"/fake/{url.rsplit('/', 1)[-1]}"
    tm = FakeTM()
    client = ds_client(task_manager=tm, fetch=selective_fetch)
    tid = _submit(client, ["http://x/good.wav", "http://x/bad.wav"]).json()["output"]["task_id"]
    out = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]
    # 一成一败 → 聚合 SUCCEEDED（有成功且无进行中）
    assert out["task_status"] == "SUCCEEDED"
    assert out["task_metrics"] == {"TOTAL": 2, "SUCCEEDED": 1, "FAILED": 1}


def test_queue_full_throttling(ds_client):
    tm = FakeTM(full=True)
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    out = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]
    sub = out["results"][0]
    assert sub["subtask_status"] == "FAILED" and sub["code"] == "Throttling"


# ─── 参数映射 ───

def test_diarization_and_speaker_count_handling(ds_client):
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    _submit(client, ["http://x/a.wav"],
            params={"language_hints": ["en"], "diarization_enabled": True, "speaker_count": 3})
    opts = tm.submitted[0]["options"]
    assert opts == {"diarize": True}          # speaker_count 被忽略，不进 options
    assert tm.submitted[0]["language"] == "English"   # ISO 码 en 归一为 Qwen 规范名


# ─── external base url / forwarded ───

def test_external_base_url(ds_client, monkeypatch):
    monkeypatch.setattr(cfg, "COMPAT_EXTERNAL_BASE_URL", "https://ext.example.com")
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    url = client.get(f"/compat/dashscope/api/v1/tasks/{tid}").json()["output"]["results"][0]["transcription_url"]
    assert url.startswith("https://ext.example.com/compat/dashscope/api/v1/tasks/")


def test_x_forwarded_host(ds_client, monkeypatch):
    monkeypatch.setattr(cfg, "COMPAT_EXTERNAL_BASE_URL", None)
    tm = FakeTM()
    client = ds_client(task_manager=tm)
    tid = _submit(client, ["http://x/a.wav"]).json()["output"]["task_id"]
    url = client.get(f"/compat/dashscope/api/v1/tasks/{tid}",
                     headers={"X-Forwarded-Proto": "https",
                              "X-Forwarded-Host": "real.example.com"}
                     ).json()["output"]["results"][0]["transcription_url"]
    assert url.startswith("https://real.example.com/")


# ─── 未知任务 / 鉴权 ───

def test_poll_unknown_task_404(ds_client):
    client = ds_client(task_manager=FakeTM())
    r = client.get("/compat/dashscope/api/v1/tasks/nope")
    assert r.status_code == 404 and r.json()["code"] == "UNKNOWN_TASK"


def test_transcription_unknown_task_404(ds_client):
    client = ds_client(task_manager=FakeTM())
    r = client.get("/compat/dashscope/api/v1/tasks/nope/transcription/0")
    assert r.status_code == 404 and r.json()["code"] == "UNKNOWN_TASK"


def test_auth_missing_token_401(ds_client):
    client = ds_client(task_manager=FakeTM(), api_key="sk-secret")
    r = _submit(client, ["http://x/a.wav"])
    assert r.status_code == 401 and r.json()["code"] == "InvalidApiKey"


def test_auth_valid_token_ok(ds_client):
    client = ds_client(task_manager=FakeTM(), api_key="sk-secret")
    r = _submit(client, ["http://x/a.wav"], headers={"Authorization": "Bearer sk-secret"})
    assert r.status_code == 200
