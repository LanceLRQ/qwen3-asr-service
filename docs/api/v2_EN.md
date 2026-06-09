# Qwen3-ASR Service API Reference (v2, default version)

[中文](v2.md) | **English**

All endpoints are prefixed with `/v2`. Default base URL: `http://127.0.0.1:8765`.

v1 is kept for legacy clients; its offline endpoints are identical to v2 (only the prefix differs). See the [v1 reference](v1_EN.md).

> While the service is running, `http://127.0.0.1:8765/docs` opens Swagger UI (FastAPI's auto-generated interactive API playground). Note: this link only works against a running service — it won't navigate when reading this document on GitHub — and the Swagger page loads its static assets from a public CDN, so it won't render in offline environments.

## Contents

API v2 is split into four sub-documents by function:

| Sub-document | Contents |
|--------------|----------|
| [Basics](v2/basics_EN.md) | Authentication, service entry `GET /`, health check `GET /v2/health`, capabilities `GET /v2/capabilities` |
| [Transcription](v2/transcription_EN.md) | Offline batch submit `POST /v2/asr`, real-time transcription `WS /v2/asr/stream` |
| [Task Management](v2/tasks_EN.md) | List / detail / cancel-delete tasks, transcription result structure, how task persistence affects the API |
| [Speaker Management](v2/speakers_EN.md) | Speaker diarization & voiceprint identification, voiceprint database endpoints `/v2/speakers*` |

## Authentication at a glance

When an API key is configured, **offline batch endpoints** require a Bearer Token, otherwise `401` is returned; `GET /health` and `GET /capabilities` are open; the real-time WebSocket and speaker-management endpoints have their own rules. See [Basics · Authentication](v2/basics_EN.md#authentication) for the full rules.

```bash
curl -H "Authorization: Bearer sk-your-key-here" http://127.0.0.1:8765/v2/tasks
```

## Typical call flows

- **Offline transcription**: [`POST /v2/asr`](v2/transcription_EN.md#submit-asr-task) to submit audio and get a `task_id` → poll [`GET /v2/tasks/{task_id}`](v2/tasks_EN.md#get-task-detail) for the result.
- **Real-time transcription**: probe [`GET /v2/capabilities`](v2/basics_EN.md#capabilities) for `stream.enabled` → open [`WS /v2/asr/stream`](v2/transcription_EN.md#real-time-transcription), push PCM audio frames and receive `final` sentence by sentence.
- **Speakers**: set `identify_speakers=true` during transcription to integrate the [voiceprint database](v2/speakers_EN.md#speaker-diarization--voiceprint-identification); enrollment / renaming via the [voiceprint database endpoints](v2/speakers_EN.md#voiceprint-database-endpoints).
