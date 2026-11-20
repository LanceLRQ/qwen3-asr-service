"""Probe the loopback qwen-asr-serve instance with the S1 short WAV."""
import base64
import hashlib
import json
import os
import sys
from pathlib import Path

import requests

wav = Path(sys.argv[1]).read_bytes()
session = requests.Session()
session.trust_env = False
base = "http://127.0.0.1:18764"
headers = {"Authorization": "Bearer " + os.environ["ASR_PROBE_KEY"]}
audio = "data:audio/wav;base64," + base64.b64encode(wav).decode()
chat = {"model": "qwen3-asr", "messages": [{"role": "user", "content": [
    {"type": "audio_url", "audio_url": {"url": audio}}]}], "temperature": 0, "max_tokens": 128}
results = {"wav_sha256": hashlib.sha256(wav).hexdigest()}


def record(name, method, path, **kwargs):
    response = session.request(method, base + path, timeout=120, **kwargs)
    results[name] = {"status": response.status_code, "body": response.json()}
    if name == "missing_file":
        message = results[name]["body"]["error"]["message"]
        results[name]["body"]["error"]["message"] = message.split('  File "')[0].rstrip()


record("models", "GET", "/v1/models", headers=headers)
record("missing_key", "POST", "/v1/chat/completions", json=chat)
for name, overrides in (("chat", {}), ("language_field", {"language": "en"}),
                        ("unknown_model", {"model": "missing-model"}),
                        ("context_limit", {"max_tokens": 4097})):
    record(name, "POST", "/v1/chat/completions", headers=headers, json=chat | overrides)
record("multipart", "POST", "/v1/audio/transcriptions", headers=headers,
       files={"file": ("short.wav", wav, "audio/wav")},
       data={"model": "qwen3-asr", "language": "en"})
for name, fields in (("auto", {}), ("other_language", {"language": "fr"}),
                     ("invalid_language", {"language": "zz"}),
                     ("word_times", {"response_format": "verbose_json",
                                     "timestamp_granularities[]": "word"})):
    record(name, "POST", "/v1/audio/transcriptions", headers=headers,
           files={"file": ("short.wav", wav, "audio/wav")}, data={"model": "qwen3-asr"} | fields)
record("over_25_mib", "POST", "/v1/audio/transcriptions", headers=headers,
       files={"file": ("size-test.wav", wav.ljust(25 * 1024**2 + 1, b"\0"), "audio/wav")},
       data={"model": "qwen3-asr"})
record("missing_file", "POST", "/v1/audio/transcriptions", headers=headers,
       data={"model": "qwen3-asr"})
response = session.get(base + "/v1/realtime", headers=headers | {
    "Connection": "Upgrade", "Upgrade": "websocket", "Sec-WebSocket-Version": "13",
    "Sec-WebSocket-Key": base64.b64encode(os.urandom(16)).decode()},
    timeout=10, stream=True)
results["realtime_handshake"] = {"status": response.status_code}
response.close()
print(json.dumps(results, indent=2, ensure_ascii=False))
