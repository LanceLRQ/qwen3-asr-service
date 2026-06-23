# Speaker Management (v2)

[← API v2 overview](../v2_EN.md) ｜ [中文](speakers.md) | **English**

How speaker diarization (anonymous labels) and voiceprint identification (real names) work, plus the voiceprint database `/v2/speakers*` enrollment / management / identification endpoints.

## Table of Contents

- [Speaker Diarization & Voiceprint Identification](#speaker-diarization--voiceprint-identification)
- [Voiceprint Database Endpoints `/v2/speakers*`](#voiceprint-database-endpoints)
  - [Enroll a Speaker](#enroll-a-speaker)
  - [List / Detail / Rename & Note / Delete](#list--detail--rename--note--delete)
  - [Template Management / Identify](#template-management--identify)
  - [Error Codes](#error-codes)

---

## Speaker Diarization & Voiceprint Identification

Two layers of capability, both disabled by default and enabled on demand (configuration in the [configuration reference](../../configuration_EN.md)):

| Layer | Switch | Output |
|-------|--------|--------|
| **Speaker diarization** (anonymous) | `enable_speaker` | Offline `segments[].speaker` and top-level `speakers`, real-time `final.speaker` — labels `A`/`B`/`C`… ordered by first time speaking, **scoped to a single file / single session** (same person is not guaranteed to keep the same label across tasks) |
| **Voiceprint identification** (real name) | `enable_speaker_db` (depends on the layer above + `api_key` must be configured) | When the request sets `identify_speakers=true`, speakers are matched against the voiceprint database: matches emit `speaker_name`; an unmatched speaker with enough speech (default ≥10s) is **auto-enrolled** under a placeholder name `说话人_NN` ("Speaker_NN") — offline `speaker_auto_enroll` (on by default), real-time `stream_speaker_auto_enroll` (off by default) — renaming via [List / Detail / Rename & Note / Delete](#list--detail--rename--note--delete) |

Key points:

- **Failures always degrade gracefully**: a failure in any diarization/identification step only drops the label/real name; the transcription result is unaffected.
- **Real-time identification follows "latest final wins"**: early finals may have no `speaker_name` (centroid not yet stable); once a later match is found, new finals carry the real name, and **historical messages are not rewritten**.
- **Return the voiceprint uuid for clients to remember**: when the request sets `return_speaker_id=true`, matched/enrolled speakers carry the voiceprint-DB uuid in offline `segments[].speaker_id` and real-time `final.speaker_id` (real-time also requires `identify_speakers=true`). Offline `result.speakers[]` always carries `speaker_id`; the flag only controls per-segment attachment.
- **Enroll voiceprints in real time**: besides server-side `stream_speaker_auto_enroll`, a client can send an `enroll` message mid-session to explicitly enroll a `final.speaker` label into the DB (must set `consent=true`); the server replies `enroll.ack` with the new `speaker_id`, and subsequent finals for that label carry the real name/uuid. See [Transcription · Client → Server](transcription_EN.md#client--server).
- `speakers[].speaker_id` is a **pure value snapshot**: it has no association with the task store; after a speaker is deleted, the id in historical tasks dangles (`GET /v2/speakers/{id}` returning 404 means "deleted"), and callers must tolerate this.
- Consent semantics of auto-enrollment: enabling `speaker_auto_enroll` / `stream_speaker_auto_enroll` means the deployer declares that consent for voiceprint enrollment has been obtained from the data subject (the same liability attribution as manual enrollment with `consent=true`).

How transcription carries `identify_speakers`: offline in [Transcription · Submit ASR Task](transcription_EN.md#submit-asr-task), real-time in [Transcription · Client → Server](transcription_EN.md#client--server); the speaker fields in offline results are in [Task Management · Result Structure](tasks_EN.md#result-structure).

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

## Voiceprint Database Endpoints

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
