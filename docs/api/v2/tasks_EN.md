# Task Management (v2)

[← API v2 overview](../v2_EN.md) ｜ [中文](tasks.md) | **English**

Lifecycle management and result queries for offline transcription tasks. Tasks are created by [`POST /v2/asr`](transcription_EN.md#submit-asr-task); this section covers listing, querying results, cancelling/deleting, and how task persistence affects these endpoints.

## Table of Contents

- [List Tasks `GET /v2/tasks`](#list-tasks)
- [Get Task Detail `GET /v2/tasks/{task_id}`](#get-task-detail)
  - [Result Structure](#result-structure)
- [Cancel / Delete Task `DELETE /v2/tasks/{task_id}`](#cancel--delete-task)
- [How Task Persistence Affects the API](#how-task-persistence-affects-the-api)

---

## List Tasks

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

## Get Task Detail

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

- Task status flow: `pending` → `processing` → `completed` / `failed` / `cancelled`.
- For unknown tasks, the endpoint returns 200 with `status` set to `not_found`.
- With task persistence enabled, historical tasks (expired from memory or from before a restart) are served from the persistence store (including `result`).

### Result Structure

`result` is the offline transcription output. Fields:

| Field | Description |
|-------|-------------|
| `segments[]` | Sentence-level result array; each segment has `start` / `end` (seconds) and `text` |
| `segments[].words` | Word-level timestamps, **only present when `align_enabled=true`** |
| `full_text` | Concatenated full text |
| `language` | Detected language (`null` = auto-detect not backfilled) |
| `align_enabled` | Whether alignment was enabled (decides whether `words` exists) |
| `punc_enabled` | Whether punctuation restoration was enabled |

With speaker diarization enabled (`enable_speaker`), `result` gains the following fields (absent when disabled; semantics in [Speaker Management](speakers_EN.md#speaker-diarization--voiceprint-identification)):

- `segments[].speaker`: anonymous label `A`/`B`/`C`… (ordered by first time speaking);
- `segments[].speaker_name`: the real name when a voiceprint matches (only when `identify_speakers=true` and a match is found);
- top-level `speakers`: the speaker list — `["A","B"]` for plain diarization; upgraded to a mapping table
  `[{"label","speaker_id","name","score","auto_enrolled"?}]` when voiceprint identification is on (entries with no match have `speaker_id`/`name` set to `null`).

With audio tagging enabled (`--enable-audio-tagging`), `result` gains the following optional fields (absent when disabled):

- `segments[].scene`: the dominant scene string for the segment, one of `silence` / `speech` / `singing` / `music` / `other` (or custom buckets); present only when `scene_enable` is on;
- `segments[].scene_scores`: the segment's average probability per scene bucket `{bucket: prob}` — **independent confidences** (NOT normalized to 1; reflects "speech over background music" coexisting, e.g. `{"speech":0.62,"music":0.31}`);
- top-level `audio_events`: an array of onset/offset-aggregated event segments (NOT per-window) — each entry is `{"label","start_ms","end_ms","confidence"}` where `label` is an AudioSet class, `start_ms`/`end_ms` are integers (milliseconds), and `confidence` (float) is the max probability within the segment.
- An optional form field `scene_preset` (`balanced`/`live`/`music`) overrides the scene-decision preset per request.

```json
"segments": [{"start": 0.31, "end": 11.76, "text": "...", "scene": "speech",
              "scene_scores": {"speech": 0.62, "music": 0.31, "singing": 0.03}}],
"audio_events": [{"label": "Speech", "start_ms": 0, "end_ms": 25920, "confidence": 0.897}]
```

## Cancel / Delete Task

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

## How Task Persistence Affects the API

With `enable_task_store` on (see the [configuration reference](../../configuration_EN.md#offline-task-persistence-tasksdb)):

- **Results survive restarts**: `GET /tasks/{id}` still returns the full `result` for tasks completed before a restart (persistence store is queried on memory miss).
- **Restart reconciliation**: tasks left unfinished (`pending` / `processing`) at the previous shutdown are marked `failed` with `error` set to `"service restarted"`. They are **not** re-run automatically.
- **History query**: `GET /tasks?history=true&limit=N` merges historical tasks from the store.
- **History deletion**: `DELETE /tasks/{id}` deletes the record of a task that exists only in the store (returns `deleted`).
- **Retention cleanup**: terminal records older than `task_retention_days` (default 7 days) are removed at service startup.

When off (built-in default): tasks live in memory only — terminal results are kept for 1 hour and lost on restart.
