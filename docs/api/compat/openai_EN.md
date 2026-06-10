# OpenAI Compatibility API

[← Compatibility APIs Overview](../compat_EN.md) ｜ [中文](openai.md) | **English**

Drop-in shim for clients already built against the **OpenAI** speech ecosystem: point the SDK's base url at this service's `/compat/openai/v1` prefix — no business-code changes. Client base_url = `http://<host>:8765/compat/openai/v1`.

> Offline endpoints need `--enable-openai-api` at startup; realtime additionally needs `--enable-stream`. Authentication and client pointing: see the [Compatibility APIs Overview](../compat_EN.md#2-authentication).

## Table of Contents

- [Transcription `POST /audio/transcriptions`](#transcription)
- [Streaming `stream=true` (SSE)](#streaming)
- [Translation `POST /audio/translations`](#translation)
- [Models `GET /models`](#models)
- [Realtime `WS /realtime`](#realtime)
- [Error codes](#error-codes)

---

## Transcription

```
POST /audio/transcriptions
```

Synchronous transcription: upload audio, **the response returns the result** (internally waits on the queue, capped by `--openai-sync-timeout`, default 300s).

**Request** (`multipart/form-data`):

| Field | Required | Supported | Notes |
|------|------|------|------|
| `file` | yes | ✅ | Audio file; same extension allowlist as v2 |
| `model` | yes | lenient | Any value; the actually-loaded model is used (see `GET /models`) |
| `language` | no | ✅ | ISO-639-1, e.g. `zh`/`en` |
| `response_format` | no | ✅ | `json` (default)/`text`/`srt`/`verbose_json`/`vtt` |
| `timestamp_granularities[]` | no | ✅ | `word`/`segment` (needs `verbose_json`); include `word` for word-level timestamps |
| `stream` | no | ✅ | `true` switches to SSE (see [Streaming](#streaming)) |
| `prompt` | no | ❌ ignored | Guiding text not supported |
| `temperature` | no | ❌ ignored | Sampling temperature not supported |

**Response**:

- `json`: `{"text": "full transcript"}`
- `text`: plain text (`text/plain`)
- `verbose_json`: includes `segments[]` (`start`/`end`/`text` are real) and optional `words[]`
  > ⚠️ `tokens`/`avg_logprob`/`compression_ratio`/`no_speech_prob`/`seek` are **placeholders** (no corresponding data); do not treat them as real confidence.
- `srt`/`vtt`: standard subtitle text (`text/plain`)

**Example**:
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8765/compat/openai/v1", api_key="sk-xxx")
r = client.audio.transcriptions.create(
    model="whisper-1", file=open("a.wav", "rb"),
    response_format="verbose_json", timestamp_granularities=["word", "segment"])
print(r.text)
```
```bash
curl http://localhost:8765/compat/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-xxx" \
  -F file=@a.wav -F model=whisper-1 -F response_format=srt
```

## Streaming

`POST /audio/transcriptions` with `stream=true` uses Server-Sent Events, emitting `transcript.text.delta` / `transcript.text.done`:

```
data: {"type": "transcript.text.delta", "delta": "Hello"}

data: {"type": "transcript.text.delta", "delta": " world"}

data: {"type": "transcript.text.done", "text": "Hello world"}
```

> Note: this service decodes the whole clip offline — it **transcribes fully first, then emits sentence by sentence**, so the first delta's latency ≈ full transcription time (not token-by-token while decoding). `delta` text is a chunk of the final result (no timestamps); `response_format` does not apply when `stream=true`.

## Translation

```
POST /audio/translations
```

**Not supported**. This service is speech recognition only. Always returns `501`:
```json
{"error":{"message":"This service performs speech recognition only; translation is not supported.","type":"invalid_request_error","param":null,"code":"unsupported"}}
```

## Models

```
GET /models
```

```json
{"object":"list","data":[{"id":"qwen3-asr-0.6b","object":"model","created":0,"owned_by":"qwen3-asr"}]}
```
`id` reflects the actually-loaded model size; the request-side `model` accepts any value leniently.

## Realtime

```
WS /realtime
```

OpenAI Realtime transcription session (needs `--enable-stream`). ws base = `ws://<host>:8765/compat/openai/v1`.

1. Server → `session.created` (sent on connect)
2. Client → `session.update`: configure language/sample rate (accepts GA `session.audio.input` and beta field paths)
3. Client → `input_audio_buffer.append`: `{"type":"...append","audio":"<base64 PCM16>"}` (continuous)
4. Client → `input_audio_buffer.commit` (or close) flushes the trailing sentence
5. Server → per sentence `conversation.item.input_audio_transcription.completed`: `{"item_id":"item_0","transcript":"full sentence"}`

> **Capabilities & limits**: the current realtime backend is VAD-offline — it **emits only whole-sentence `…completed`, never per-token `…delta`** (`partial_results=false`). Per-token deltas require a future vLLM streaming backend.

---

## Error codes

OpenAI style `{"error":{"message","type","param","code"}}`:

| HTTP | code | Scenario |
|------|------|------|
| 400 | `invalid_value` | invalid response_format, etc. |
| 401 | `invalid_api_key` | auth failure |
| 501 | `unsupported` | translations |
| 503 | `overloaded` | task queue full |
| 504 | `timeout` | sync wait exceeded `--openai-sync-timeout` |
| 500 | `internal_error` | transcription failed |

> For the DashScope ecosystem see [DashScope Compatibility API](dashscope_EN.md); for compat-vs-native tradeoffs see the [Compatibility APIs Overview](../compat_EN.md#compat-vs-native-v2).
