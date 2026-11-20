"""Collect one serial S2 run against the isolated ASR service and its upstream."""
import hashlib
import json
import sys
import time
from pathlib import Path

import requests

baseline = Path(__file__).resolve().parents[1] / "s1-standard-gpu"
manifest = json.loads((baseline / "samples.json").read_text())
audio_dir = Path(sys.argv[1])
service = "http://127.0.0.1:18765"
upstream = "http://127.0.0.1:18764"
http = requests.Session()
http.trust_env = False


def upstream_metrics():
    response = http.get(upstream + "/metrics", timeout=10)
    response.raise_for_status()
    keys = {
        "vllm:e2e_request_latency_seconds_count": "calls",
        "vllm:e2e_request_latency_seconds_sum": "engine_e2e_s",
        "vllm:request_queue_time_seconds_sum": "queue_s",
        "vllm:request_inference_time_seconds_sum": "inference_s",
    }
    values = {"http_handler_s": 0.0, "uploaded_bytes": 0.0}
    for line in response.text.splitlines():
        key = line.partition("{")[0]
        if 'handler="/v1/audio/transcriptions"' in line:
            if key == "http_request_duration_seconds_sum":
                values["http_handler_s"] += float(line.rsplit(" ", 1)[1])
            elif key == "http_request_size_bytes_sum":
                values["uploaded_bytes"] += float(line.rsplit(" ", 1)[1])
        if key in keys:
            values[keys[key]] = float(line.rsplit(" ", 1)[1])
    assert set(keys.values()) <= values.keys(), values
    return values


result = {}
for key in ("health", "capabilities"):
    response = http.get(service + "/v2/" + key, timeout=10)
    response.raise_for_status()
    result[key] = response.json()
assert result["health"]["mode"] == "remote"
response = http.get(service + "/compat/openai/v1/models", timeout=10)
response.raise_for_status()
result["models"] = response.json()
assert result["models"]["data"][0]["id"] == "qwen3-asr", result["models"]
result["tasks"] = {}
for name, sample in manifest.items():
    wav = (audio_dir / (name + ".wav")).read_bytes()
    assert hashlib.sha256(wav).hexdigest() == sample["sha256"]
    before = upstream_metrics()
    start = time.monotonic()
    response = http.post(service + "/v2/asr", files={"file": (name + ".wav", wav)},
                         data={"language": "en", "with_words": "true"}, timeout=60)
    response.raise_for_status()
    task_id = response.json()["task_id"]
    submitted = time.monotonic() - start
    observed = []
    while time.monotonic() - start < 300:
        response = http.get(service + "/v2/tasks/" + task_id, timeout=10)
        response.raise_for_status()
        task = response.json()
        elapsed = time.monotonic() - start
        if not observed or task["status"] != observed[-1]["status"]:
            observed.append({"status": task["status"], "elapsed_s": elapsed})
        if task["status"] == "completed":
            break
        if task["status"] not in ("pending", "processing"):
            raise RuntimeError(task)
        time.sleep(0.05)
    else:
        raise TimeoutError(task_id)
    after = upstream_metrics()
    result["tasks"][name] = {
        "sha256": sample["sha256"], "submit_round_trip_s": submitted,
        "observed": observed, "terminal": task,
        "upstream": {k: after[k] - before[k] for k in before},
    }
with (audio_dir / "short.wav").open("rb") as audio:
    response = http.post(service + "/compat/openai/v1/audio/transcriptions",
                         files={"file": ("short.wav", audio)},
                         data={"model": "whisper-1", "language": "en"}, timeout=60)
response.raise_for_status()
result["sync"] = response.json()
print(json.dumps(result, ensure_ascii=False, indent=2))
