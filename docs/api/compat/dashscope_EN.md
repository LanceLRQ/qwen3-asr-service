# DashScope Compatibility API

[← Compatibility APIs Overview](../compat_EN.md) ｜ [中文](dashscope.md) | **English**

Drop-in shim for clients already built against the **Alibaba Cloud DashScope (Paraformer)** speech ecosystem. DashScope Paraformer **recorded-file recognition** (async): submit → poll → fetch second-hop result. Client `dashscope.base_http_api_url = "http://<host>:8765/compat/dashscope/api/v1"`.

> Offline endpoints need `--enable-dashscope-api` at startup; realtime additionally needs `--enable-stream`. Authentication and client pointing: see the [Compatibility APIs Overview](../compat_EN.md#2-authentication).

> ⚠️ The DashScope endpoint only accepts `file_urls` (a list of URLs downloaded server-side). For **local file upload**, use the OpenAI endpoint [`/audio/transcriptions`](openai_EN.md#transcription) (multipart) or native [`POST /v2/asr`](../v2/transcription_EN.md).

## Table of Contents

- [Submit (async)](#submit-async)
- [Poll](#poll)
- [Second-hop transcript](#second-hop-transcript)
- [Realtime `WS /inference`](#realtime)
- [Error codes](#error-codes)

---

## Submit (async)

`POST /services/audio/asr/transcription`, header `X-DashScope-Async: enable` (required; missing → 400).

```json
{ "model":"paraformer-v2",
  "input":{"file_urls":["https://example.com/a.wav"]},
  "parameters":{"language_hints":["zh"],"diarization_enabled":false} }
```

| Param | Supported | Mapping |
|------|------|------|
| `input.file_urls[]` | ✅ | Server-side download (SSRF-guarded); one subtask per URL; ≤16 per request |
| `parameters.language_hints[0]` | ✅ | → recognition language |
| `parameters.diarization_enabled` | ✅ | → speaker diarization |
| `parameters.speaker_count` | ❌ ignored | Max speakers is a service-level setting (`--speaker-max`), not per-request |
| `parameters.channel_id` | ❌ | Mono, fixed channel 0 |
| `disfluency_removal_enabled` / `special_word_filter` / `timestamp_alignment_enabled` | ❌ ignored | No corresponding capability |

> ⚠️ `file_urls` must be **reachable** by this service; private/loopback addresses are blocked by default (SSRF guard), overridable with `--compat-fetch-allow-private` (trusted intranet only).

Response: `{"output":{"task_status":"PENDING","task_id":"<id>"},"request_id":"<rid>"}`

## Poll

`POST|GET /tasks/{task_id}`:

```json
{ "output":{"task_id":"<id>","task_status":"SUCCEEDED",
    "results":[{"file_url":"https://…/a.wav",
                "transcription_url":".../tasks/<id>/transcription/0",
                "subtask_status":"SUCCEEDED"}],
    "task_metrics":{"TOTAL":1,"SUCCEEDED":1,"FAILED":0}} }
```
`task_status`: `PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED` (aggregated across multiple file_urls). The result lives at `transcription_url` (second hop).

> Behind a reverse proxy / in containers, set `--compat-external-base-url` for the `transcription_url` external base; otherwise it is derived from `X-Forwarded-Proto/Host` or the request address.
> The task registry is in-memory only (with TTL): after a restart, task_ids whose results were not yet fetched return 404 and must be resubmitted.

## Second-hop transcript

`GET /tasks/{task_id}/transcription/{idx}`, time unit **milliseconds**:

```json
{ "file_url":"https://…/a.wav",
  "transcripts":[{"channel_id":0,"content_duration_in_milliseconds":8470,"text":"full transcript",
    "sentences":[{"begin_time":0,"end_time":3200,"text":"…","sentence_id":1,"speaker_id":0,
                  "words":[{"begin_time":0,"end_time":200,"text":"hi","punctuation":""}]}]}] }
```

**Example**:
```python
import dashscope
dashscope.base_http_api_url = "http://localhost:8765/compat/dashscope/api/v1"
dashscope.api_key = "sk-xxx"
from dashscope.audio.asr import Transcription
task = Transcription.async_call(model="paraformer-v2",
        file_urls=["https://example.com/a.wav"], language_hints=["zh"])
result = Transcription.wait(task=task.output.task_id)   # SDK polls + fetches transcription_url
print(result.output)
```

## Realtime

```
WS /inference
```

Paraformer realtime (standard mode needs `--enable-stream`; under vLLM mode `--serve-mode vllm` it auto-mounts with `--enable-dashscope-api`, no `--enable-stream` needed). ws base = `ws://<host>:8765/compat/dashscope/api-ws/v1`. `header/payload` envelope:

1. Client → `run-task`: `{"header":{"action":"run-task","task_id":"<uuid>","streaming":"duplex"},"payload":{"parameters":{"format":"pcm","sample_rate":16000,"language_hints":["zh"]}}}`
2. Server → `task-started`
3. Client → binary PCM frames (~100ms each)
4. Server → per sentence `result-generated`: `payload.output.sentence` with `begin_time`/`end_time`(ms)/`text`/`sentence_end:true`/`words[]`
5. Client → `finish-task` → Server → `task-finished`
6. The same connection can issue another `run-task` (connection reuse)

> **Capabilities & limits**: depends on the serving mode — **standard** (VAD-offline) emits only whole sentences `result-generated` (`sentence_end:true`), no intermediate results (`sentence_end:false`); **vLLM** (`--serve-mode vllm`, native streaming) streams intermediate `result-generated` within a sentence (`sentence_end:false`, cumulative text, `begin_time`/`end_time`=null, no `words`), then a final `sentence_end:true`. DashScope's intermediate-result semantics are inherently cumulative and align naturally with vLLM partials — forwarded cleanly (not best-effort).

---

## Error codes

DashScope style `{"code","message","request_id"}`:

| HTTP | code | Scenario |
|------|------|------|
| 400 | `InvalidParameter` | missing X-DashScope-Async, empty/>16 file_urls |
| 401 | `InvalidApiKey` | auth failure |
| 404 | `UNKNOWN_TASK` | task_id missing/expired/lost after restart |
| subtask | `FAILED` + code | download failure/SSRF reject/transcription failure/queue busy (`Throttling`) (in `results[].subtask_status`) |

> For local file upload / HTTP streaming see [OpenAI Compatibility API](openai_EN.md); for compat-vs-native tradeoffs see the [Compatibility APIs Overview](../compat_EN.md#compat-vs-native-v2).
