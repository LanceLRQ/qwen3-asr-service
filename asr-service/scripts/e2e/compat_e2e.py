#!/usr/bin/env python3
"""兼容接口端到端冒烟测试工具（OpenAI / DashScope drop-in）。

用真实客户端连一个**正在运行**的服务，验证 /compat/* 端点与上游 SDK/协议契约对齐——
覆盖单测 mock 覆盖不到的「SDK 实际字段/编码」。离线走官方 SDK，实时走裸 WebSocket。

前置：
  1. 安装 E2E 依赖：  venv/bin/pip install -r requirements-e2e.txt
  2. 启动带兼容接口的服务，例如：
       python -m app.main --enable-openai-api --enable-dashscope-api --enable-stream \
         --compat-fetch-allow-private --api-key sk-xxx
     （--compat-fetch-allow-private 供 DashScope 离线：本工具起本地 HTTP server 把音频
       暴露成 http://127.0.0.1:PORT/a.wav，服务需允许下载回环地址）

用法：
  python scripts/compat_e2e.py --base-url http://127.0.0.1:8765 --api-key sk-xxx
  python scripts/compat_e2e.py --only openai-transcribe-json,dashscope-offline
  python scripts/compat_e2e.py --audio /path/to/speech.wav --skip dashscope-realtime

退出码：全部通过 0，有失败 1，前置缺失（依赖/音频/服务不可达）2。
"""
import argparse
import asyncio
import base64
import functools
import http.server
import json
import os
import sys
import threading
from urllib.parse import urlparse
import uuid

# ── 路径：默认音频取仓库内现成的 16k 中文语音样本（scripts/e2e/ → asr-service）──
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_ASR_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
_DEFAULT_AUDIO = os.path.join(
    _ASR_ROOT, "models/speaker/campplus/examples/speaker1_a_cn_16k.wav")

# ── 颜色（非 TTY 时关闭）──
_TTY = sys.stdout.isatty()
def _c(code, s):
    return f"\033[{code}m{s}\033[0m" if _TTY else s
GREEN = lambda s: _c("32", s)
RED = lambda s: _c("31", s)
YELLOW = lambda s: _c("33", s)
DIM = lambda s: _c("2", s)


def bypass_proxy_for(url):
    """把目标 host 加入 NO_PROXY，避免环境 http_proxy/all_proxy 把直连请求（含 127.0.0.1）
    转给代理。SDK/httpx/websockets 都读 no_proxy；本地服务必须绕过，否则代理返回 503。"""
    host = urlparse(url).hostname
    if not host:
        return
    cur = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""
    hosts = [h for h in cur.split(",") if h.strip()]
    for h in (host, "localhost", "127.0.0.1"):
        if h not in hosts:
            hosts.append(h)
    val = ",".join(hosts)
    os.environ["NO_PROXY"] = val
    os.environ["no_proxy"] = val


class Skip(Exception):
    """前置不满足，跳过该项（非失败）。"""


# ── 结果收集 ──
class Results:
    def __init__(self):
        self.rows = []   # (name, status, detail)  status ∈ PASS/FAIL/SKIP

    def add(self, name, status, detail=""):
        self.rows.append((name, status, detail))
        tag = {"PASS": GREEN("PASS"), "FAIL": RED("FAIL"), "SKIP": YELLOW("SKIP")}[status]
        line = f"  [{tag}] {name}"
        if detail:
            line += DIM(f"  — {detail}")
        print(line)

    def run(self, name, fn):
        try:
            detail = fn()
            self.add(name, "PASS", detail or "")
        except Skip as e:
            self.add(name, "SKIP", str(e))
        except Exception as e:
            self.add(name, "FAIL", f"{type(e).__name__}: {e}")

    def summary_exit(self):
        n_pass = sum(1 for _, s, _ in self.rows if s == "PASS")
        n_fail = sum(1 for _, s, _ in self.rows if s == "FAIL")
        n_skip = sum(1 for _, s, _ in self.rows if s == "SKIP")
        print("\n" + "─" * 56)
        print(f"  合计：{GREEN(str(n_pass)+' passed')}  "
              f"{RED(str(n_fail)+' failed') if n_fail else '0 failed'}  "
              f"{YELLOW(str(n_skip)+' skipped') if n_skip else '0 skipped'}")
        return 1 if n_fail else 0


# ── 音频加载（int16 PCM + 采样率）──
def load_pcm(path):
    try:
        import soundfile as sf
    except ImportError:
        raise Skip("缺 soundfile（pip install -r requirements-test.txt）")
    import numpy as np
    data, sr = sf.read(path, dtype="int16")
    if getattr(data, "ndim", 1) > 1:
        data = data[:, 0]                 # 取单声道
    return np.ascontiguousarray(data).tobytes(), int(sr)


def _frames(pcm, sr, ms=100):
    step = int(sr * ms / 1000) * 2        # int16 → 2 bytes/sample
    for i in range(0, len(pcm), step):
        yield pcm[i:i + step]


# ── 本地 HTTP server：把音频暴露成 URL 供 DashScope 服务端下载 ──
def start_audio_server(audio_path, bind="127.0.0.1"):
    directory = os.path.dirname(os.path.abspath(audio_path))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    httpd = http.server.HTTPServer((bind, 0), handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://{bind}:{port}/{os.path.basename(audio_path)}"
    return httpd, url


# ═══════════════════ OpenAI 离线（官方 SDK）═══════════════════

def _openai_client(args):
    try:
        from openai import OpenAI
    except ImportError:
        raise Skip("缺 openai（pip install -r requirements-e2e.txt）")
    return OpenAI(base_url=f"{args.base_url}/compat/openai/v1",
                  api_key=args.api_key or "none", max_retries=0)


def check_openai_models(args):
    client = _openai_client(args)
    models = client.models.list()
    ids = [m.id for m in models.data]
    assert ids, "models 列表为空"
    assert ids[0].startswith("qwen3-asr"), f"模型 id 异常: {ids}"
    return f"models={ids}"


def check_openai_json(args):
    client = _openai_client(args)
    with open(args.audio, "rb") as f:
        r = client.audio.transcriptions.create(
            model="whisper-1", file=f, language="zh", response_format="json")
    text = getattr(r, "text", None)
    assert text is not None, "json 响应缺 text 字段"
    assert text.strip(), "转写文本为空（language 未生效 / 识别失败？）"
    return f"text[:20]={text[:20]!r}"


def check_openai_verbose(args):
    client = _openai_client(args)
    with open(args.audio, "rb") as f:
        r = client.audio.transcriptions.create(
            model="whisper-1", file=f, language="zh",
            response_format="verbose_json", timestamp_granularities=["word", "segment"])
    assert hasattr(r, "segments"), "verbose_json 缺 segments"
    assert r.duration is not None, "verbose_json 缺 duration"
    assert (r.text or "").strip(), "转写文本为空（language 未生效 / 识别失败？）"
    return f"segments={len(r.segments or [])} duration={r.duration}"


def check_openai_srt(args):
    client = _openai_client(args)
    with open(args.audio, "rb") as f:
        r = client.audio.transcriptions.create(
            model="whisper-1", file=f, language="zh", response_format="srt")
    body = r if isinstance(r, str) else getattr(r, "text", str(r))
    assert "-->" in body, "srt 未含时间轴"
    return "srt 含时间轴"


def check_openai_translate_501(args):
    import openai
    client = _openai_client(args)
    try:
        with open(args.audio, "rb") as f:
            client.audio.translations.create(model="whisper-1", file=f)
    except openai.APIStatusError as e:
        assert e.status_code == 501, f"期望 501，得 {e.status_code}"
        return "translations 正确返回 501"
    raise AssertionError("translations 未按预期拒绝")


def check_openai_stream_sse(args):
    """stream=true 走 httpx 直接打（解析 SSE delta/done）。"""
    try:
        import httpx
    except ImportError:
        raise Skip("缺 httpx")
    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    url = f"{args.base_url}/compat/openai/v1/audio/transcriptions"
    with open(args.audio, "rb") as f:
        files = {"file": (os.path.basename(args.audio), f, "audio/wav")}
        data = {"model": "whisper-1", "language": "zh", "stream": "true"}
        with httpx.Client(timeout=120) as client:
            with client.stream("POST", url, headers=headers, files=files, data=data) as resp:
                resp.raise_for_status()
                ct = resp.headers.get("content-type", "")
                assert ct.startswith("text/event-stream"), f"非 SSE: {ct}"
                events = []
                for line in resp.iter_lines():
                    line = line.strip()
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
    types = {e.get("type") for e in events}
    assert "transcript.text.done" in types, f"缺 done 事件: {types}"
    return f"事件类型={sorted(types)} 共{len(events)}条"


# ═══════════════════ DashScope 离线（官方 SDK）═══════════════════

def check_dashscope_offline(args):
    try:
        import dashscope
        from dashscope.audio.asr import Transcription
    except ImportError:
        raise Skip("缺 dashscope（pip install -r requirements-e2e.txt）")
    try:
        import httpx
    except ImportError:
        raise Skip("缺 httpx")

    dashscope.base_http_api_url = f"{args.base_url}/compat/dashscope/api/v1"
    dashscope.api_key = args.api_key or "none"

    httpd, file_url = start_audio_server(args.audio, bind=args.serve_host)
    try:
        task = Transcription.async_call(
            model="paraformer-v2", file_urls=[file_url], language_hints=["zh"])
        result = Transcription.wait(task=task.output.task_id)
        out = result.output
        if out.task_status != "SUCCEEDED":
            sub = (out.results or [{}])[0]
            hint = ""
            if sub.get("code") == "FetchForbidden":
                hint = "（服务需以 --compat-fetch-allow-private 启动以允许下载回环 URL）"
            raise AssertionError(
                f"task_status={out.task_status} sub={sub.get('subtask_status')} "
                f"code={sub.get('code')}{hint}")
        turl = out.results[0]["transcription_url"]
        headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
        doc = httpx.get(turl, headers=headers, timeout=30).json()
        text = doc["transcripts"][0]["text"]
        return f"task_status=SUCCEEDED text[:20]={text[:20]!r}"
    finally:
        httpd.shutdown()


# ═══════════════════ 实时（裸 WebSocket）═══════════════════

def _ws_url(args, path):
    base = args.base_url.replace("http://", "ws://").replace("https://", "wss://")
    url = base + path
    if args.api_key:
        url += f"?token={args.api_key}"
    return url


async def _openai_realtime(args, pcm, sr):
    import websockets
    url = _ws_url(args, "/compat/openai/v1/realtime")
    async with websockets.connect(url, max_size=None) as ws:
        created = json.loads(await ws.recv())
        assert created["type"] == "session.created", created
        await ws.send(json.dumps({"type": "session.update", "session": {"audio": {"input": {
            "format": {"type": "audio/pcm", "rate": sr},
            "transcription": {"language": "zh"}}}}}))
        updated = json.loads(await ws.recv())
        assert updated["type"] == "session.updated", updated
        for fr in _frames(pcm, sr):
            await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                      "audio": base64.b64encode(fr).decode()}))
            await asyncio.sleep(0.01)
        await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
        transcripts = []
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
                if msg.get("type") == "conversation.item.input_audio_transcription.completed":
                    transcripts.append(msg["transcript"])
                elif msg.get("type") == "error":
                    raise AssertionError(f"error 事件: {msg.get('error')}")
        except asyncio.TimeoutError:
            pass
    return transcripts


async def _dashscope_realtime(args, pcm, sr):
    import websockets
    url = _ws_url(args, "/compat/dashscope/api-ws/v1/inference")
    async with websockets.connect(url, max_size=None) as ws:
        tid = uuid.uuid4().hex[:32]
        await ws.send(json.dumps({
            "header": {"action": "run-task", "task_id": tid, "streaming": "duplex"},
            "payload": {"task_group": "audio", "task": "asr", "function": "recognition",
                        "model": "paraformer-realtime-v2",
                        "parameters": {"format": "pcm", "sample_rate": sr,
                                       "language_hints": ["zh"]}, "input": {}}}))
        started = json.loads(await ws.recv())
        assert started["header"]["event"] == "task-started", started
        for fr in _frames(pcm, sr):
            await ws.send(fr)
            await asyncio.sleep(0.01)
        await ws.send(json.dumps({"header": {"action": "finish-task", "task_id": tid},
                                  "input": {}}))
        sentences = []
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=30))
            ev = msg["header"]["event"]
            if ev == "result-generated":
                sent = msg["payload"]["output"]["sentence"]
                assert sent.get("sentence_end") is True, "非整句结果"
                sentences.append(sent["text"])
            elif ev == "task-finished":
                break
            elif ev == "task-failed":
                raise AssertionError(f"task-failed: {msg['header'].get('error_message')}")
    return sentences


def check_openai_realtime(args, pcm, sr):
    try:
        import websockets  # noqa: F401
    except ImportError:
        raise Skip("缺 websockets")
    transcripts = asyncio.run(_openai_realtime(args, pcm, sr))
    assert transcripts, "未收到任何 completed（language 未生效 / 实时链路异常？）"
    assert transcripts[0].strip(), "首句 transcript 为空"
    return f"completed×{len(transcripts)} 首句={transcripts[0][:16]!r}"


def check_dashscope_realtime(args, pcm, sr):
    try:
        import websockets  # noqa: F401
    except ImportError:
        raise Skip("缺 websockets")
    sentences = asyncio.run(_dashscope_realtime(args, pcm, sr))
    assert sentences, "未收到任何 result-generated（language 未生效 / 实时链路异常？）"
    assert sentences[0].strip(), "首句 result 为空"
    return f"result-generated×{len(sentences)} 首句={sentences[0][:16]!r}"


# ═══════════════════ 主流程 ═══════════════════

def main():
    p = argparse.ArgumentParser(
        description="兼容接口端到端冒烟测试（需服务正在运行并开启 /compat/*）")
    p.add_argument("--base-url", default="http://127.0.0.1:8765", help="服务地址")
    p.add_argument("--api-key", default="", help="Bearer api-key（服务配了才需要）")
    p.add_argument("--audio", default=_DEFAULT_AUDIO, help="测试音频（默认仓库内 16k 中文样本）")
    p.add_argument("--serve-host", default="127.0.0.1",
                   help="DashScope file_urls 本地 server 的 bind/广告地址（默认回环）")
    p.add_argument("--only", default="", help="只跑这些（逗号分隔 check 名）")
    p.add_argument("--skip", default="", help="跳过这些（逗号分隔 check 名）")
    p.add_argument("--list", action="store_true", help="列出所有 check 名后退出")
    args = p.parse_args()
    bypass_proxy_for(args.base_url)     # 直连目标，绕开环境 HTTP 代理

    # check 注册表：名 → 调用
    pcm_holder = {}

    def _audio():
        if "pcm" not in pcm_holder:
            pcm_holder["pcm"], pcm_holder["sr"] = load_pcm(args.audio)
        return pcm_holder["pcm"], pcm_holder["sr"]

    checks = {
        "openai-models": lambda: check_openai_models(args),
        "openai-transcribe-json": lambda: check_openai_json(args),
        "openai-transcribe-verbose": lambda: check_openai_verbose(args),
        "openai-transcribe-srt": lambda: check_openai_srt(args),
        "openai-translate-501": lambda: check_openai_translate_501(args),
        "openai-stream-sse": lambda: check_openai_stream_sse(args),
        "dashscope-offline": lambda: check_dashscope_offline(args),
        "openai-realtime": lambda: check_openai_realtime(args, *_audio()),
        "dashscope-realtime": lambda: check_dashscope_realtime(args, *_audio()),
    }

    if args.list:
        print("可用 check：")
        for name in checks:
            print(f"  {name}")
        return 0

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    selected = [n for n in checks if (not only or n in only) and n not in skip]

    if not os.path.isfile(args.audio):
        print(RED(f"音频不存在: {args.audio}"), file=sys.stderr)
        return 2

    print(f"目标服务：{args.base_url}   音频：{os.path.basename(args.audio)}   "
          f"鉴权：{'on' if args.api_key else 'off'}")
    print("─" * 56)

    res = Results()
    for name in selected:
        res.run(name, checks[name])
    return res.summary_exit()


if __name__ == "__main__":
    sys.exit(main())
