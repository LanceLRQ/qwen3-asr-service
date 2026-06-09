# Qwen3-ASR Service API Reference (v1, legacy version)

[中文](v1.md) | **English**

v1 is kept for legacy clients. **New integrations should use the [v2 reference](v2_EN.md) (default version).**

## Relationship with v2

The offline batch, health-check and capabilities endpoints of v1 and v2 **share the same implementation** and behave identically; only the path prefix differs (`/v1` vs `/v2`). For parameters, response structures and error codes, refer to the [v2 reference](v2_EN.md) and replace `/v2` with `/v1`:

| Endpoint | v1 Path | Notes |
|----------|---------|-------|
| Submit ASR task | `POST /v1/asr` | Same as v2 |
| List tasks | `GET /v1/tasks` | Same as v2 (incl. `status` / `history` / `limit` parameters) |
| Get task detail | `GET /v1/tasks/{task_id}` | Same as v2 |
| Cancel / delete task | `DELETE /v1/tasks/{task_id}` | Same as v2 (incl. historical-task deletion, `deleted`) |
| Health check | `GET /v1/health` | Same as v2 |
| Capabilities | `GET /v1/capabilities` | Same as v2 |

Authentication is identical: with an API key configured, offline endpoints require `Authorization: Bearer <key>`.

## v1-only: Deprecated Task Query Alias

```
GET /v1/asr/{task_id}
```

A historical alias of `GET /v1/tasks/{task_id}` with identical behavior. **Marked deprecated and kept in v1 only** — v2 does not provide it. For legacy clients in transition; please migrate.

## Not Available in v1

- **Real-time transcription**: `WS /v2/asr/stream` is provided in the v2 namespace only (single unified endpoint, no v1 variant). See [v2 reference · Real-time Transcription](v2/transcription_EN.md#real-time-transcription).
- **Speaker management**: `/v2/speakers*` (voiceprint enrollment/identification/management) is provided in the v2 namespace only. See [v2 reference · Speaker Management](v2/speakers_EN.md#voiceprint-database-endpoints). Note that because v1/v2 share the implementation, once speaker diarization / voiceprint identification is enabled, the `identify_speakers` parameter on `POST /v1/asr` and the `speaker` / `speaker_name` / `speakers` fields in offline results also take effect in v1 (additive, see below).

## Versioning Conventions

- All protocol changes are additive (new optional fields/parameters); old clients can simply ignore unknown fields. Since v1/v2 share the implementation, such changes also take effect in v1 (e.g. the `history` parameter and `wav_name` field added by task persistence, or the `segments[].speaker` and related fields added by speaker diarization).
- Brand-new capabilities (such as the real-time endpoint and the speaker management endpoints) land in the v2 namespace only; v1 gains no new endpoints.
