"""从独立服务采集响应。参数依次为样本目录、结果文件。"""
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

import requests
import websockets

audio = Path(sys.argv[1])
base = "http://127.0.0.1:18763"
http = requests.Session()
http.trust_env = False
result = {}
for key in ("health", "capabilities"):
    response = http.get(base + "/v2/" + key, timeout=10)
    response.raise_for_status()
    result[key] = response.json()
result["web_ui_status"] = http.get(base + "/web-ui", timeout=10).status_code

def upload(route, name, fields):
    with (audio / (name + ".wav")).open("rb") as wav:
        response = http.post(base + route, files={"file": (name + ".wav", wav, "audio/wav")},
                             data=fields, timeout=240)
    response.raise_for_status()
    return response.json()

result["sync"] = upload("/compat/openai/v1/audio/transcriptions", "short",
                        {"model": "whisper-1", "language": "en"})
result["async"] = {}
for name in ("short", "dialogue", "silence", "noise", "long"):
    start = time.monotonic()
    submitted = upload("/v2/asr", name, {"language": "en"})
    changes = []
    while time.monotonic() - start < 300:
        response = http.get(base + "/v2/tasks/" + submitted["task_id"], timeout=15)
        response.raise_for_status()
        task = response.json()
        if not changes or changes[-1]["status"] != task["status"]:
            changes.append(dict(status=task["status"], seconds=time.monotonic()-start))
        if task["status"] in ("completed", "failed", "cancelled"):
            break
        time.sleep(.05)
    else:
        raise TimeoutError(name)
    result["async"][name] = dict(submitted=submitted, observed=changes, terminal=task)

async def stream():
    with wave.open(str(audio / "short.wav")) as wav:
        pcm = wav.readframes(wav.getnframes())
    async with websockets.connect(base.replace("http:", "ws:") + "/v2/asr/stream", proxy=None) as ws:
        events = [json.loads(await ws.recv())]
        await ws.send(json.dumps(dict(type="start", audio_fs=16000, language="en")))
        for offset in range(0, len(pcm), 3200):
            await ws.send(pcm[offset:offset+3200])
            await asyncio.sleep(.1)
        await ws.send('{"type":"stop"}')
        try:
            async for message in ws:
                events.append(json.loads(message))
        except websockets.exceptions.ConnectionClosedError:
            if not any(event["type"] == "session.closed" for event in events):
                raise
        return dict(events=events, close_code=ws.close_code)

result["stream"] = asyncio.run(asyncio.wait_for(stream(), 90))
Path(sys.argv[2]).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
