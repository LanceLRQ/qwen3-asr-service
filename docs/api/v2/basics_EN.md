# Basics (v2)

[← API v2 overview](../v2_EN.md) ｜ [中文](basics.md) | **English**

Service probing, capability declaration, and authentication conventions. All endpoints are prefixed with `/v2`. Default base URL: `http://127.0.0.1:8765`.

## Table of Contents

- [Authentication](#authentication)
- [Service Entry `GET /`](#service-entry)
- [Service Status](#service-status)
  - [Health Check `GET /v2/health`](#health-check)
  - [Capabilities `GET /v2/capabilities`](#capabilities)

---

## Authentication

When an API key is configured (startup parameter `--api-key` / config key `api_key` / environment variable `ASR_API_KEY`, see the [configuration reference](../../configuration_EN.md)), **offline batch endpoints** require a Bearer Token, otherwise `401` is returned:

```bash
curl -H "Authorization: Bearer sk-your-key-here" http://127.0.0.1:8765/v2/tasks
```

- `GET /health` and `GET /capabilities` do not require authentication (for probing).
- WebSocket authentication is described in [Transcription · Authentication](transcription_EN.md#authentication).
- Speaker management `/v2/speakers*` endpoints **all enforce Bearer authentication** (the voiceprint database is entirely unavailable when the server has no `api_key`), see [Speaker Management](speakers_EN.md#voiceprint-database-endpoints).
- Without an API key, all endpoints are open.

## Service Entry

```
GET /
```

When the Web UI is enabled (`--web`), `307`-redirects to `/web-ui`; otherwise returns a service index JSON (pointing to `health` / `capabilities`) — never blank or 404.

```json
{
  "service": "Qwen3-ASR Service",
  "version": "2.0.0",
  "mode": "standard",
  "health": "/v2/health",
  "capabilities": "/v2/capabilities",
  "web_ui": "未启用，启动加 --web 开启 / disabled, start with --web"
}
```

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

> In vllm mode (placeholder, not yet implemented), non-applicable fields are `null`.

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
  "audio_tagging": false,
  "scene": false,
  "stream": {
    "enabled": true,
    "backend": "vad-offline",
    "path": "/v2/asr/stream",
    "partial_results": false,
    "word_timestamps": true,
    "speaker_labels": true,
    "scene": false
  },
  "defaults": {
    "max_segment": 5, "max_end_silence_ms": 800, "max_segment_sec": 12,
    "speaker_threshold": 0.5, "speaker_id_threshold": 0.45, "speaker_id_margin": 0.1,
    "energy_floor_dbfs": -50.0, "snr_min_db": 6.0
  }
}
```

| Field | Description |
|-------|-------------|
| speaker_labels | Whether speaker diarization is enabled (offline and real-time share the same switch) |
| speaker_identification | Whether voiceprint real-name identification is available (enrollment / identify / transcription integration) |
| audio_tagging | Whether audio tagging is enabled (`--enable-audio-tagging`; gates `POST /v2/audio/tag` and the offline `audio_events` field) |
| scene | Whether scene detection is available (the offline `segments[].scene` field) |
| stream.enabled | Whether the real-time endpoint is mounted (requires `--enable-stream`) |
| stream.backend | `vad-offline` / `vllm-native` (not yet implemented) |
| stream.partial_results | Whether intermediate `partial` results are produced (false for vad-offline) |
| stream.word_timestamps | Whether `final` carries word-level timestamps (follows the alignment switch) |
| stream.speaker_labels | Whether real-time `final` carries speaker labels |
| stream.scene | Whether the real-time stream emits `scene` messages |
| defaults | Current effective defaults of the overridable params (real-time `start` fields / offline Form fields), reflecting actual config; used by the Web UI for input placeholders |
