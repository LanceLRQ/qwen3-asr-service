# Transcription (v2)

[← API v2 overview](../v2_EN.md) ｜ [中文](transcription.md) | **English**

Two ways to transcribe: **offline batch** (upload a whole clip, get the result asynchronously) and **real-time** (WebSocket streaming, sentence by sentence).

## Table of Contents

- [Offline Batch · Submit ASR Task `POST /v2/asr`](#submit-asr-task)
  - [Language codes and normalization](#language-codes-and-normalization)
- [Audio Tagging `POST /v2/audio/tag`](#audio-tagging)
- [Real-time Transcription `WS /v2/asr/stream`](#real-time-transcription)
  - [Authentication](#authentication)
  - [Message Flow](#message-flow)
  - [Client → Server](#client--server)
  - [Server → Client](#server--client)
  - [Error Codes](#error-codes)
  - [WebSocket Close Codes](#websocket-close-codes)

---

## Submit ASR Task

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
| language | string | null | Language hint; `null`/omitted = auto-detect. Accepted forms and normalization: see [below](#language-codes-and-normalization) |
| identify_speakers | bool | false | Run voiceprint identification on the diarized speakers (requires both speaker diarization and the [voiceprint database](speakers_EN.md#speaker-diarization--voiceprint-identification) to be enabled) |
| return_speaker_id | bool | false | Return the voiceprint-DB uuid in `segments[].speaker_id` for matched/enrolled speakers (so clients can remember voiceprints; `result.speakers[]` always carries `speaker_id` — this flag only controls per-segment attachment) |
| with_punc | bool | server default | Whether to restore punctuation (downgrade-only toggle; no punctuation if the model isn't loaded server-side) |
| with_words | bool | server default | Whether to emit word-level timestamps (requires the alignment model loaded) |
| diarize | bool | server default | Whether to run speaker diarization (turn off to save compute; requires the speaker engine loaded) |
| max_segment | int | server default | Max VAD-merge segment length (seconds), range `[1, 30]` |
| speaker_id_threshold | float | server default | Voiceprint 1:N identification threshold, range `[0, 1]` (requires the voiceprint DB enabled) |
| speaker_id_margin | float | server default | Voiceprint top1-top2 margin, range `[0, 1]` (requires the voiceprint DB enabled) |

> Out-of-range values → 400; overrides for features that aren't enabled don't error — the transcription `result.warnings` (string array) lists the ignored params.

#### Language codes and normalization

`language` accepts three forms, normalized server-side to the engine's language name before inference:

- **ISO-639-1 codes**: `zh` / `en` / `yue` / `ja` …
- **Canonical English names** (case-insensitive): `Chinese` / `English` / …
- **With region subtags**: `zh-CN` / `en_US` (resolved by primary subtag)

**Unrecognized values** (typos, unsupported languages, case variants like `Zh`) **fall back to auto-detection instead of erroring** — the previous pass-through behavior that made the engine raise `Unsupported language` is now intercepted at the service layer. Offline and [real-time transcription](#real-time-transcription) share the same normalization rule.

Supported languages (30): `Chinese`, `English`, `Cantonese`, `Arabic`, `German`, `French`, `Spanish`, `Portuguese`, `Indonesian`, `Italian`, `Korean`, `Russian`, `Thai`, `Vietnamese`, `Japanese`, `Turkish`, `Hindi`, `Malay`, `Dutch`, `Swedish`, `Danish`, `Finnish`, `Polish`, `Czech`, `Filipino`, `Persian`, `Greek`, `Romanian`, `Hungarian`, `Macedonian`.

Response:

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

A successful submission returns only the `task_id`; **the recognition result is retrieved by polling the task-management endpoints** — see [Task Management · Get Task Detail](tasks_EN.md#get-task-detail) for detail queries and the result structure (`segments` / `words` / speaker fields).

**Limits**: max file size 1GB, audio duration 1s to 4 hours.

| Status Code | Meaning |
|-------------|---------|
| 200 | Submitted, returns `task_id` |
| 400 | Unsupported audio format |
| 401 | Authentication failed |
| 413 | File too large (>1GB) |
| 503 | Service not ready / task queue full |

## Audio Tagging

```
POST /v2/audio/tag
Content-Type: multipart/form-data
```

Tagging only — detects audio events and the scene timeline without transcribing speech. Requires audio tagging to be enabled on the server (`--enable-audio-tagging`).

```bash
curl -X POST http://127.0.0.1:8765/v2/audio/tag \
  -F "file=@/path/to/audio.mp3" \
  -F "with_scene=true"
```

**Authentication**: when an API key is configured, a Bearer Token is required (otherwise `401`); open when no `api_key` is set.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| file | File | Required | Audio file: same formats as [`POST /v2/asr`](#submit-asr-task) |
| with_scene | bool | true | Whether to return the scene timeline |
| scene_preset | string | (server default) | Per-request scene preset override: `balanced` / `live` / `music` |

> Note: this endpoint has no transcript, so **lyrics-aware singing recovery does not apply** (see [Configuration · Audio Tagging](../../configuration_EN.md#audio-tagging-general-audio-event-tagging--derived-scene)); accompanied singing may read as `music` — for per-sentence scenes use `/v2/asr`.

Response (200):

```json
{
  "audio_events": [{"label": "Speech", "start_ms": 0, "end_ms": 2000, "confidence": 0.9}],
  "scene_timeline": [{"label": "speech", "start_ms": 0, "end_ms": 2000,
                      "scene_scores": {"speech": 0.62, "music": 0.18, "singing": 0.03}}]
}
```

- `audio_events`: onset/offset-aggregated event segments (see [Result Structure](tasks_EN.md#result-structure) for the field semantics).
- `scene_timeline`: run-length-merged contiguous scene segments, each with `scene_scores` (per-bucket distribution); omitted / `null` when `with_scene=false`.

| Status Code | Meaning |
|-------------|---------|
| 200 | Tagging result returned |
| 400 | Unsupported audio format |
| 401 | Authentication failed |
| 503 | Audio tagging is not enabled on the server |

## Real-time Transcription

```
WS /v2/asr/stream
```

**Prerequisites**: `standard` mode + real-time enabled (`--enable-stream` or config `enable_stream: true`). The endpoint does not exist otherwise; probe [`GET /v2/capabilities`](basics_EN.md#capabilities) and check `stream.enabled` first.

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
| language | null | Language hint; `null`/omitted = auto-detect. Same accepted forms and normalization as [offline submit](#language-codes-and-normalization) (invalid/unrecognized codes fall back to auto-detect, no error) |
| wav_name | "stream" | Session name (for display) |
| identify_speakers | false | Run voiceprint identification on speaker labels (requires `session.created.capabilities.speaker_identification=true`) |
| return_speaker_id | false | Return the voiceprint-DB uuid in `final.speaker_id` for matched/enrolled speakers (so clients can remember voiceprints); requires `identify_speakers=true`, otherwise ignored |
| noise_filter | server default | Override far-field segment gating for this session (defaults to the server config; requires `capabilities.noise_filter_tunable=true`) |
| energy_floor_dbfs | server default | Override the absolute energy gate (dBFS) for this session, range `[-90, 0]`; out-of-range returns `invalid_config` |
| snr_min_db | server default | Override the adaptive SNR gate (dB) for this session, range `[0, 40]`; `0` disables this gate |
| speaker_threshold | server default | Online clustering cosine threshold, range `[0.2, 0.9]` (requires `capabilities.speaker_labels=true`) |
| speaker_min_seg_ms | server default | Short-segment gate (ms), range `[0, 10000]` |
| speaker_max | server default | Max speakers, range `[1, 50]` |
| speaker_id_threshold | server default | Voiceprint identification threshold, range `[0, 1]` (requires `capabilities.speaker_identification=true`) |
| speaker_id_margin | server default | Voiceprint top1-top2 margin, range `[0, 1]` |
| max_end_silence_ms | server default | Endpoint trailing silence (ms), range `[200, 2000]`: smaller = faster output but choppier; larger = won't interrupt but slower |
| max_segment_sec | server default | Long-sentence fallback split (seconds), range `[1, 60]` |
| with_punc / with_words / diarize | server default | Downgrade toggles: disable punctuation / word timestamps / diarization (off only; can't enable a model that isn't loaded) |

> **Clamping & soft notices**: these overrides affect only the current session; out-of-range / wrong-type → `invalid_config` (fatal).
> A well-formed param whose feature isn't enabled (e.g. `diarize:true` with no speaker engine loaded) does NOT error —
> the server sends a non-fatal `error` after `start` (`code="params_ignored"`, `fatal=false`) whose `message` lists the ignored params.
> The VAD sensitivity `vad_speech_noise_thres` is a server-global setting (FunASR constraint) and cannot be adjusted per session.

**Audio frames (binary frames)**: PCM16 little-endian, mono, at the declared `audio_fs`. Max 2MB per frame (oversized frames are rejected without disconnecting).

**`stop` (JSON text frame)**: `{"type": "stop"}` — the server flushes the last segment, sends `session.closed`, and closes normally.

**`enroll` (JSON text frame, optional)**: explicitly enroll a speaker cluster into the voiceprint DB mid-session so the client gets a stable uuid.

```json
{"type": "enroll", "label": "B", "name": "Zhang San", "consent": true}
```

| Field | Description |
|-------|-------------|
| label | In-session anonymous label (the A/B/C… of `final.speaker`), max 32 chars |
| name | Display name to enroll, max 128 chars |
| consent | Must be `true` (voiceprint is biometric data); otherwise returns `enroll_failed` |

> Requires `capabilities.speaker_identification=true` and diarization enabled for this session. The server enrolls the label's current session centroid as a single template and replies with `enroll.ack` (carrying `speaker_id`); subsequent `final`s for that label then carry `speaker_name`/`speaker_id`.
> Quality gate: the label's accumulated effective speech must be ≥ `speaker_enroll_min_sec` (default 3s, same as offline manual enrollment); otherwise it returns `enroll_failed` — let the speaker talk a bit more and retry.
> Dedup: if the label's centroid already matches an existing speaker, the server **adds a template and reuses that `speaker_id`** (no duplicate row; a placeholder name `说话人_NN` is renamed to this `name`), and `enroll.ack.matched_existing=true`.
> Any failure returns a non-fatal `error` (`code="enroll_failed"`, no disconnect). The server can also auto-enroll unmatched clusters via `stream_speaker_auto_enroll` (off by default).

### Server → Client

All server-to-client messages use a uniform envelope and carry a `type`:

| type | Fields | Description |
|------|--------|-------------|
| `session.created` | `protocol`("qwen3-asr-stream") / `protocol_version`("1.0") / `mode` / `backend` / `sample_rate` / `capabilities` / `limits` | Sent on connect; `capabilities` contains `partial_results` / `word_timestamps` / `languages_auto` / `speaker_labels` / `speaker_identification`, plus tunability flags `noise_filter_tunable` / `speaker_tunable` / `endpoint_tunable` / `output_toggles` (whether the corresponding overrides can be tuned in this session); `limits` contains `max_frame_bytes` / `max_backlog_bytes` — clients pushing faster than real time should pace themselves accordingly (use `final.end` as processing-progress feedback and keep the unprocessed backlog below the limit) |
| `partial` | `seg_id` / `text` | Intermediate result (only for backends with `partial_results=true`; vad-offline does not produce them) |
| `final` | `seg_id` / `text` / `start` / `end` / `words` / `speaker` / `speaker_name` / `speaker_id` / `scene` / `scene_scores` | Finalized sentence-level result; `start`/`end` in milliseconds; `words` only when `word_timestamps=true`; `speaker` (anonymous label A/B/C…) only when `speaker_labels=true` and this segment is decidable; `speaker_name` only when `identify_speakers=true` and a voiceprint matches; `speaker_id` (voiceprint-DB uuid) only when `return_speaker_id=true` and matched/enrolled; `scene` (segment's dominant scene) / `scene_scores` (per-bucket distribution) only when `capabilities.stream.scene=true`, semantics identical to offline `segments[].scene` / `scene_scores` |
| `enroll.ack` | `label` / `speaker_id` / `name` / `matched_existing` | Acknowledgement of a successful explicit `enroll`: `speaker_id` is the speaker's uuid, `name` the final display name; `matched_existing=true` means it matched an existing speaker and appended a template (no new row) |
| `scene` | `label` / `confidence` / `since` / `scores` | Current scene update (only when `capabilities.stream.scene=true`); `label` is the current scene; `since` is the start timestamp in milliseconds; `scores` holds per-content-bucket representative scores. Emitted only on a state **change** (hysteresis-smoothed; a continuous state is emitted once) — for per-sentence scene use `final.scene` |
| `error` | `code` / `message` / `seg_id` / `fatal` | The session terminates when `fatal=true` |
| `session.closed` | `reason` | Session ended |

`final` example:

```json
{"type": "final", "seg_id": 0, "text": "甚至出现交易几乎停滞的情况。", "start": 320, "end": 3520, "words": null}
```

`scene` example:

```json
{"type": "scene", "label": "speech", "confidence": 0.86, "since": 1000, "scores": {"speech": 0.86, "singing": 0.0, "music": 0.04}}
```

### Error Codes

Values of `code` in the uniform `error` envelope:

| code | fatal | Description |
|------|-------|-------------|
| `invalid_config` | yes | `start` message validation failed (e.g. `audio_fs` out of range) |
| `frame_too_large` | no | Frame exceeds 2MB; the frame is dropped |
| `backlog_overflow` | yes | Processing backlog exceeds 8MB (~4 minutes of audio); session disconnected |
| `feed_failed` | no | A segment failed to process; skipped, session continues |
| `enroll_failed` | no | An `enroll` message failed (malformed / missing consent / unknown label / insufficient sample duration / feature not enabled / DB error); no disconnect |
| `session_timeout` | yes | Session exceeded the max duration (default 1 hour) |
| `internal` | yes | Internal error |

### WebSocket Close Codes

| Close Code | Description |
|------------|-------------|
| 1000 | Normal completion (stop flow finished) |
| 1008 | Authentication failed |
| 1011 | Service not ready / fatal internal error |
| 1013 | Concurrent session limit reached (default 16, tunable via `max_stream_sessions`) |
