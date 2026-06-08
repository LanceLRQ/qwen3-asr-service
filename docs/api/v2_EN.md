# Qwen3-ASR Service API Reference (v2, default version)

[中文](v2.md) | **English**

All endpoints are prefixed with `/v2`. Default base URL: `http://127.0.0.1:8765`.

v1 is kept for legacy clients; its offline endpoints are identical to v2 (only the prefix differs). See the [v1 reference](v1_EN.md).

> While the service is running, `http://127.0.0.1:8765/docs` opens Swagger UI (FastAPI's auto-generated interactive API playground). Note: this link only works against a running service — it won't navigate when reading this document on GitHub — and the Swagger page loads its static assets from a public CDN, so it won't render in offline environments.

## Table of Contents

- [Authentication](#authentication)
- [Offline Batch Processing](#offline-batch-processing)
  - [Submit ASR Task `POST /v2/asr`](#submit-asr-task)
  - [List Tasks `GET /v2/tasks`](#list-tasks)
  - [Get Task Detail `GET /v2/tasks/{task_id}`](#get-task-detail)
  - [Cancel / Delete Task `DELETE /v2/tasks/{task_id}`](#cancel--delete-task)
- [Service Status](#service-status)
  - [Health Check `GET /v2/health`](#health-check)
  - [Capabilities `GET /v2/capabilities`](#capabilities)
- [Real-time Transcription `WS /v2/asr/stream`](#real-time-transcription)
- [Speaker Diarization & Voiceprint Identification](#speaker-diarization--voiceprint-identification)
- [Speaker Management `/v2/speakers*`](#speaker-management)
- [How Task Persistence Affects the API](#how-task-persistence-affects-the-api)

---

## Authentication

When an API key is configured (startup parameter `--api-key` / config key `api_key` / environment variable `ASR_API_KEY`, see the [configuration reference](../configuration_EN.md)), **offline batch endpoints** require a Bearer Token, otherwise `401` is returned:

```bash
curl -H "Authorization: Bearer sk-your-key-here" http://127.0.0.1:8765/v2/tasks
```

- `GET /health` and `GET /capabilities` do not require authentication (for probing).
- WebSocket authentication is described in [Real-time Transcription](#real-time-transcription).
- Without an API key, all endpoints are open.

## Offline Batch Processing

### Submit ASR Task

```
POST /v2/asr
Content-Type: multipart/form-data
```

```bash
curl -X POST http://127.0.0.1:8765/v2/asr \
  -F "file=@/path/to/audio.mp3" \
  -F "language=zh"
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| file | File | Required | Audio file: WAV/MP3/FLAC/M4A/AAC/OGG/WMA/AMR/OPUS |
| language | string | null | Language code, null for auto-detection |
| identify_speakers | bool | false | Run voiceprint identification on the diarized speakers (requires both speaker diarization and the [voiceprint database](#speaker-diarization--voiceprint-identification) to be enabled, otherwise silently ignored) |

Response:

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

**Limits**: max file size 1GB, audio duration 1s to 4 hours.

| Status Code | Meaning |
|-------------|---------|
| 200 | Submitted, returns `task_id` |
| 400 | Unsupported audio format |
| 401 | Authentication failed |
| 413 | File too large (>1GB) |
| 503 | Service not ready / task queue full |

### List Tasks

```
GET /v2/tasks
```

```bash
# All active tasks
curl http://127.0.0.1:8765/v2/tasks

# Filter by status
curl http://127.0.0.1:8765/v2/tasks?status=processing

# Include historical tasks (requires task persistence: enable_task_store)
curl "http://127.0.0.1:8765/v2/tasks?history=true&limit=20"
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| status | string | null | Filter: `pending` / `processing` / `completed` / `failed` / `cancelled` |
| history | bool | false | Merge historical tasks from the persistence store (no effect when `enable_task_store` is off) |
| limit | int | 50 | Max items returned when `history=true` |

Response (sorted by creation time, descending; results not included):

```json
{
  "total": 2,
  "tasks": [
    {
      "task_id": "550e8400-...",
      "status": "completed",
      "progress": 1.0,
      "language": null,
      "wav_name": "meeting.mp3",
      "created_at": "2026-06-04T10:30:00",
      "finished_at": "2026-06-04T10:31:00",
      "error": null
    },
    {
      "task_id": "660e8400-...",
      "status": "processing",
      "progress": 0.45,
      "language": "zh",
      "wav_name": "interview.wav",
      "created_at": "2026-06-04T10:31:00",
      "finished_at": null,
      "error": null
    }
  ]
}
```

### Get Task Detail

```
GET /v2/tasks/{task_id}
```

Response (completed):

```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "progress": 1.0,
  "result": {
    "segments": [
      {
        "start": 0.0,
        "end": 3.2,
        "text": "甚至出现交易几乎停滞的情况。",
        "words": [
          {"text": "甚", "start": 0.0, "end": 0.15},
          {"text": "至", "start": 0.15, "end": 0.30}
        ]
      }
    ],
    "full_text": "甚至出现交易几乎停滞的情况。",
    "language": null,
    "align_enabled": true,
    "punc_enabled": true
  },
  "error": null,
  "wav_name": "meeting.mp3",
  "created_at": "2026-06-04T10:30:00",
  "finished_at": "2026-06-04T10:31:00"
}
```

- `result.segments[].words` only exists when `align_enabled=true` (word-level timestamps).
- With speaker diarization enabled (`enable_speaker`), `result` gains the following fields (absent when disabled, see [Speaker Diarization & Voiceprint Identification](#speaker-diarization--voiceprint-identification)):
  - `segments[].speaker`: anonymous label `A`/`B`/`C`… (ordered by first time speaking);
  - `segments[].speaker_name`: the real name when a voiceprint matches (only when `identify_speakers=true` and a match is found);
  - top-level `speakers`: the speaker list — `["A","B"]` for plain diarization; upgraded to a mapping table
    `[{"label","speaker_id","name","score","auto_enrolled"?}]` when voiceprint identification is on (entries with no match have `speaker_id`/`name` set to `null`).
- Task status flow: `pending` → `processing` → `completed` / `failed` / `cancelled`.
- For unknown tasks, the endpoint returns 200 with `status` set to `not_found`.
- With task persistence enabled, historical tasks (expired from memory or from before a restart) are served from the persistence store (including `result`).

### Cancel / Delete Task

```
DELETE /v2/tasks/{task_id}
```

Response:

```json
{"task_id": "550e8400-...", "status": "cancelled", "message": "任务已取消"}
```

| Task State | Behavior | Returned `status` |
|-----------|----------|-------------------|
| `pending` | Cancelled immediately | `cancelled` |
| `processing` | Stops after the current chunk, returns partial results | `cancelled` |
| `completed` / `failed` / `cancelled` | No state change | `already_completed` / `already_failed` / `already_cancelled` |
| Historical task existing only in the persistence store | **Deletes the record** (requires `enable_task_store`) | `deleted` |
| Unknown | - | `not_found` |

## Service Status

### Health Check

```
GET /v2/health
```

```json
{
  "status": "ready",
  "mode": "standard",
  "device": "cuda",
  "model_size": "0.6b",
  "align_enabled": true,
  "punc_enabled": false,
  "speaker_enabled": false,
  "speaker_db_enabled": false,
  "asr_backend": "qwen_asr",
  "vad_backend": "pytorch",
  "punc_backend": "pytorch",
  "config_file": "config.yaml",
  "capabilities": {
    "mode": "standard",
    "offline_api": true,
    "speaker_labels": false,
    "speaker_identification": false,
    "stream": {
      "enabled": true,
      "backend": "vad-offline",
      "path": "/v2/asr/stream",
      "partial_results": false,
      "word_timestamps": true,
      "speaker_labels": false
    }
  }
}
```

| Field | Description |
|-------|-------------|
| status | Service status, `ready` means operational (503 when not ready) |
| mode | Serving mode: `standard` / `vllm` |
| device | Running device: `cuda` / `cpu` |
| model_size | ASR model size: `0.6b` / `1.7b` |
| align_enabled | Whether the alignment model is enabled (word-level timestamps) |
| punc_enabled | Whether punctuation restoration is enabled |
| speaker_enabled | Whether speaker diarization is enabled (`enable_speaker`) |
| speaker_db_enabled | Whether the voiceprint database is available (enabled and model_tag matches) |
| asr_backend | ASR backend: `qwen_asr` / `openvino` |
| vad_backend | VAD backend: `pytorch` / `onnx` |
| punc_backend | Punctuation backend: `pytorch` / `onnx` / `disabled` |
| config_file | Name of the active config file (`null` = no config file loaded) |
| capabilities | Capability summary, same as `GET /capabilities` |

> In vllm mode (Phase 3 placeholder), non-applicable fields are `null`.

### Capabilities

```
GET /v2/capabilities
```

Returns the current serving mode and capability declaration (clients can use it to detect real-time availability):

```json
{
  "mode": "standard",
  "offline_api": true,
  "speaker_labels": true,
  "speaker_identification": false,
  "stream": {
    "enabled": true,
    "backend": "vad-offline",
    "path": "/v2/asr/stream",
    "partial_results": false,
    "word_timestamps": true,
    "speaker_labels": true
  }
}
```

| Field | Description |
|-------|-------------|
| speaker_labels | Whether speaker diarization is enabled (offline and real-time share the same switch) |
| speaker_identification | Whether voiceprint real-name identification is available (enrollment / identify / transcription integration) |
| stream.enabled | Whether the real-time endpoint is mounted (requires `--enable-stream`) |
| stream.backend | `vad-offline` (Route B) / `vllm-native` (Phase 3) |
| stream.partial_results | Whether intermediate `partial` results are produced (false for vad-offline) |
| stream.word_timestamps | Whether `final` carries word-level timestamps (follows the alignment switch) |
| stream.speaker_labels | Whether real-time `final` carries speaker labels |

## Real-time Transcription

```
WS /v2/asr/stream
```

**Prerequisites**: `standard` mode + real-time enabled (`--enable-stream` or config `enable_stream: true`). The endpoint does not exist otherwise; probe `GET /v2/capabilities` and check `stream.enabled` first.

> Browser test page: start with `--web` and open `/web-ui/stream` (microphone capture / simulated streaming from an audio file).

### Authentication

When an API key is configured, the connection must carry one of the following (otherwise rejected with close code `1008`):

- Query parameter: `ws://host:port/v2/asr/stream?token=sk-your-key`
- Header: `Authorization: Bearer sk-your-key` (browser WebSocket API does not support custom headers — use the query parameter there)

### Message Flow

```
Client                                  Server
  │ ──── WebSocket connect ─────────────▶ │
  │ ◀─── {"type":"session.created",...} ─ │   protocol/backend/capabilities announced on connect
  │ ──── {"type":"start",...} ──────────▶ │   session configuration
  │ ──── binary audio frames × N ───────▶ │   PCM16 little-endian, mono
  │ ◀─── {"type":"final",...} (per seg) ─ │   sentence-level results after VAD segmentation
  │ ──── {"type":"stop"} ───────────────▶ │   end of stream
  │ ◀─── {"type":"final",...} (flush) ─── │
  │ ◀─── {"type":"session.closed",...} ── │
  │ ◀──── WebSocket normal close ──────── │
```

### Client → Server

**`start` (first message, JSON text frame)**:

```json
{"type": "start", "audio_fs": 16000, "language": null, "wav_name": "stream"}
```

| Field | Default | Description |
|-------|---------|-------------|
| audio_fs | 16000 | Sample rate, 8000–96000 allowed; non-16k input is resampled server-side |
| language | null | Language code, null for auto-detection |
| wav_name | "stream" | Session name (for display) |
| identify_speakers | false | Run voiceprint identification on speaker labels (requires `session.created.capabilities.speaker_identification=true`) |
| noise_filter | server default | Override far-field segment gating for this session (defaults to the server config; requires `capabilities.noise_filter_tunable=true`) |
| energy_floor_dbfs | server default | Override the absolute energy gate (dBFS) for this session, range `[-90, 0]`; out-of-range returns `invalid_config` |
| snr_min_db | server default | Override the adaptive SNR gate (dB) for this session, range `[0, 40]`; `0` disables this gate |

> Far-field overrides affect only the current session and are range-clamped server-side. The VAD sensitivity `vad_speech_noise_thres` is a server-global setting (FunASR constraint) and cannot be adjusted per session.

**Audio frames (binary frames)**: PCM16 little-endian, mono, at the declared `audio_fs`. Max 2MB per frame (oversized frames are rejected without disconnecting).

**`stop` (JSON text frame)**: `{"type": "stop"}` — the server flushes the last segment, sends `session.closed`, and closes normally.

### Server → Client (uniform envelopes, all carry `type`)

| type | Fields | Description |
|------|--------|-------------|
| `session.created` | `protocol`("qwen3-asr-stream") / `protocol_version`("1.0") / `mode` / `backend` / `sample_rate` / `capabilities` / `limits` | Sent on connect; `capabilities` contains `partial_results` / `word_timestamps` / `languages_auto` / `speaker_labels` / `speaker_identification`; `limits` contains `max_frame_bytes` / `max_backlog_bytes` — clients pushing faster than real time should pace themselves accordingly (use `final.end` as processing-progress feedback and keep the unprocessed backlog below the limit) |
| `partial` | `seg_id` / `text` | Intermediate result (only for backends with `partial_results=true`; vad-offline does not produce them) |
| `final` | `seg_id` / `text` / `start` / `end` / `words` / `speaker` / `speaker_name` | Finalized sentence-level result; `start`/`end` in milliseconds; `words` only when `word_timestamps=true`; `speaker` (anonymous label A/B/C…) only when `speaker_labels=true` and this segment is decidable; `speaker_name` only when `identify_speakers=true` and a voiceprint matches |
| `error` | `code` / `message` / `seg_id` / `fatal` | The session terminates when `fatal=true` |
| `session.closed` | `reason` | Session ended |

`final` example:

```json
{"type": "final", "seg_id": 0, "text": "甚至出现交易几乎停滞的情况。", "start": 320, "end": 3520, "words": null}
```

### Error Codes (`error.code`)

| code | fatal | Description |
|------|-------|-------------|
| `invalid_config` | yes | `start` message validation failed (e.g. `audio_fs` out of range) |
| `frame_too_large` | no | Frame exceeds 2MB; the frame is dropped |
| `backlog_overflow` | yes | Processing backlog exceeds 8MB (~4 minutes of audio); session disconnected |
| `feed_failed` | no | A segment failed to process; skipped, session continues |
| `session_timeout` | yes | Session exceeded the max duration (default 1 hour) |
| `internal` | yes | Internal error |

### WebSocket Close Codes

| Close Code | Description |
|------------|-------------|
| 1000 | Normal completion (stop flow finished) |
| 1008 | Authentication failed |
| 1011 | Service not ready / fatal internal error |
| 1013 | Concurrent session limit reached (default 16, tunable via `max_stream_sessions`) |

## Speaker Diarization & Voiceprint Identification

Two layers of capability, both disabled by default and enabled on demand (configuration in the [configuration reference](../configuration_EN.md)):

| Layer | Switch | Output |
|-------|--------|--------|
| **Speaker diarization** (anonymous) | `enable_speaker` | Offline `segments[].speaker` and top-level `speakers`, real-time `final.speaker` — labels `A`/`B`/`C`… ordered by first time speaking, **scoped to a single file / single session** (same person is not guaranteed to keep the same label across tasks) |
| **Voiceprint identification** (real name) | `enable_speaker_db` (depends on the layer above + `api_key` must be configured) | When the request sets `identify_speakers=true`, speakers are matched against the voiceprint database: matches emit `speaker_name`; offline, an unmatched speaker with enough speech (default ≥10s) is **auto-enrolled** under a placeholder name `说话人_NN` (Chinese for "Speaker_NN") (`speaker_auto_enroll`, can be turned off), renaming via [Speaker Management](#speaker-management) |

Key points:

- **Failures always degrade gracefully**: a failure in any diarization/identification step only drops the label/real name; the transcription result is unaffected.
- **Real-time identification follows "latest final wins"**: early finals may have no `speaker_name` (centroid not yet stable); once a later match is found, new finals carry the real name, and **historical messages are not rewritten**; the real-time path does not auto-enroll.
- `speakers[].speaker_id` is a **pure value snapshot**: it has no association with the task store; after a speaker is deleted, the id in historical tasks dangles (`GET /v2/speakers/{id}` returning 404 means "deleted"), and callers must tolerate this.
- Consent semantics of auto-enrollment: enabling `speaker_auto_enroll` means the deployer declares that consent for voiceprint enrollment has been obtained from the data subject (the same liability attribution as manual enrollment with `consent=true`).

Offline result example with voiceprint identification (the incremental part of `result`):

```json
{
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "大家好。", "speaker": "A", "speaker_name": "张三"},
    {"start": 3.5, "end": 6.0, "text": "我先说两句。", "speaker": "B", "speaker_name": "说话人_07"}
  ],
  "speakers": [
    {"label": "A", "speaker_id": "9f86d081884c7d659a2feaa0c55ad015", "name": "张三", "score": 0.62},
    {"label": "B", "speaker_id": "3c2a91f0a1b24e83b6f1c2d3e4f5a6b7", "name": "说话人_07", "score": null, "auto_enrolled": true},
    {"label": "C", "speaker_id": null, "name": null, "score": null}
  ]
}
```

## Speaker Management

```
/v2/speakers* (v2 only; all endpoints enforce Bearer authentication — when the server has no api_key configured, the voiceprint database is entirely unavailable)
```

> Browser management page: start with `--web` and open `/web-ui/speakers` (list / rename & note / delete).

Voiceprint data is **never auto-cleaned** (unlike the task store's 7-day TTL); the only way to delete is via the DELETE endpoint (hard delete + physical reclamation).

### Enroll a Speaker

```
POST /v2/speakers        (multipart) → 201
```

```bash
curl -X POST http://127.0.0.1:8765/v2/speakers \
  -H "Authorization: Bearer sk-your-key" \
  -F "name=张三" -F "consent=true" -F "note=产品部" \
  -F "files=@sample1.wav" -F "files=@sample2.wav" -F "files=@sample3.wav"
```

| Parameter | Description |
|-----------|-------------|
| name | Display name (required) |
| consent | Must be `true`: confirms that consent has been obtained from the data subject (otherwise 400) |
| note | Note (optional) |
| files | ≥1 clear **single-speaker** audio sample; each sample must have ≥3s of effective speech after VAD (configurable via `speaker_enroll_min_sec`); samples in which multiple speakers are detected are rejected with 400; ≥3 samples from different scenes are recommended (insufficient samples only return a `quality_hint`, without blocking) |

Response: `{"speaker_id": "9f86…", "name": "张三", "templates": 3, "quality_hint": null}`

### List / Detail / Rename & Note / Delete

```
GET    /v2/speakers                 → {"total": N, "speakers": [{id,name,note,source,template_count,created_at,updated_at}]}
GET    /v2/speakers/{id}            → detail (includes template summary templates: [{id,dur_sec,created_at}], without feature vectors)
PATCH  /v2/speakers/{id}            → body {"name"?, "note"?}; renaming does not affect speaker_id or templates, and subsequent transcriptions show the new name immediately
DELETE /v2/speakers/{id}            → hard delete (cascades templates + physical reclamation + cleanup of retained audio, unrecoverable); this speaker falls back to anonymous in subsequent transcriptions
```

- The `source` field distinguishes `manual` (manually enrolled) / `auto` (auto-enrolled), which helps audit placeholder-name entries.

### Template Management / Identify

```
POST   /v2/speakers/{id}/templates          (multipart file) → 201, appends a sample and recomputes the centroid (max 16 per speaker)
DELETE /v2/speakers/{id}/templates/{tid}    → {"remaining": N, "hint"?} (reaching 0 templates does not auto-delete the speaker; hint suggests adding samples or deleting)
POST   /v2/speakers/identify                (multipart file) → {"matched": bool, "speaker_id"?, "name"?, "score"?}
```

Identification is a 1:N open-set decision: it returns `matched: false` when the highest similarity is below the threshold (`speaker_id_threshold`, default 0.45) or when the margin to the second-highest is too small (`speaker_id_margin`, default 0.10 — when nearest neighbors clash, prefer no answer over a wrong one).

### Error Codes

| Status Code | Meaning |
|-------------|---------|
| 400 | Quality threshold not met (insufficient duration / multiple speakers / unsupported format) / missing consent |
| 401 | Authentication failed |
| 404 | Speaker / template not found |
| 503 | `speaker_db_disabled` (module not enabled / degraded) / `model_tag_mismatch` (templates in the database are inconsistent with the current engine version: enrollment and identification are disabled, **viewing and deletion remain available**) |
| 500 | Voiceprint database read/write failure |

## How Task Persistence Affects the API

With `enable_task_store` on (see the [configuration reference](../configuration_EN.md#offline-task-persistence-tasksdb)):

- **Results survive restarts**: `GET /tasks/{id}` still returns the full `result` for tasks completed before a restart (persistence store is queried on memory miss).
- **Restart reconciliation**: tasks left unfinished (`pending` / `processing`) at the previous shutdown are marked `failed` with `error` set to `"service restarted"`. They are **not** re-run automatically.
- **History query**: `GET /tasks?history=true&limit=N` merges historical tasks from the store.
- **History deletion**: `DELETE /tasks/{id}` deletes the record of a task that exists only in the store (returns `deleted`).
- **Retention cleanup**: terminal records older than `task_retention_days` (default 7 days) are removed at service startup.

When off (built-in default): tasks live in memory only — terminal results are kept for 1 hour and lost on restart.
