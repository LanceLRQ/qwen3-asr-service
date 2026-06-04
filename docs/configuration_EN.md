# Qwen3-ASR Service Configuration Reference

[中文](configuration.md) | **English**

Configuration is layered in four levels, lowest to highest priority:

```
built-in defaults  <  environment variables  <  config file (config.yaml)  <  explicit CLI arguments
```

Higher layers override lower ones for the same parameter; **explicitly passed** CLI values always win (including explicitly passing a default, e.g. `--device auto`).

## Table of Contents

- [Startup Parameters (full table)](#startup-parameters-full-table)
- [Config File (config.yaml)](#config-file-configyaml)
- [Environment Variables](#environment-variables)
- [Offline Task Persistence (tasks.db)](#offline-task-persistence-tasksdb)
- [Built-in Constants (app/config.py)](#built-in-constants-appconfigpy)

---

## Startup Parameters (full table)

All parameters are passed through `bash start.sh <args>`. Config-file key = long CLI flag with dashes converted to underscores (e.g. `--model-size` → `model_size`; the only exception: `--use-punc` → `use_punc`).

### Basics

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--serve-mode` | `standard` / `vllm` | `standard` | Serving mode; `vllm` is a Phase 3 placeholder, not implemented yet (only /health and /capabilities) |
| `--device` | `auto` / `cuda` / `cpu` | `auto` | Device; `auto` detects (≥6GB VRAM → 1.7B, 4–6GB → 0.6B, <4GB disables alignment, no GPU falls back to CPU/OpenVINO) |
| `--model-size` | `0.6b` / `1.7b` | Auto by VRAM | ASR model size |
| `--enable-align` / `--no-align` | - | Enabled | Alignment model (word-level timestamps); force-disabled in CPU mode |
| `--use-punc` / `--no-punc` | - | Disabled | Punctuation restoration |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | Model download source |

### Service

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--host` | IP address | `127.0.0.1` | Listen address, `0.0.0.0` for LAN access |
| `--port` | Port number | `8765` | Listen port |
| `--web` / `--no-web` | - | Disabled | Web UI (`/web-ui` offline demo, `/web-ui/stream` real-time test page, `/web-ui/docs` documentation center) |
| `--api-key` | String | None | API key; enables Bearer Token auth (overrides the `ASR_API_KEY` env var) |
| `--max-segment` | Seconds | `5` | Max VAD segment merge duration |
| `--max-queue-size` | Number | `100` | Max offline task queue length |

### Real-time Transcription

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--enable-stream` / `--no-stream` | - | Disabled (enabled in configs generated from the example) | Mount the real-time endpoint `WS /v2/asr/stream` (standard mode) |
| `--max-stream-sessions` | Number | `16` | Max concurrent real-time sessions (excess connections closed with 1013) |
| `--stream-asr-concurrency` | Number | `1` | Real-time ASR decoding concurrency cap (the model layer holds an inference lock; >1 brings no gain) |

### Task Persistence

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--enable-task-store` / `--no-task-store` | - | Disabled (enabled in configs generated from the example) | Offline task persistence (results queryable across restarts) |
| `--task-db-path` | Path | `data/tasks.db` | Task database path (relative to the service root) |
| `--task-retention-days` | Days | `7` | Retention window for expired tasks, cleaned at startup; `0` = never clean |

### Config-file Meta Parameters

| Parameter | Description |
|-----------|-------------|
| `--config <PATH>` | Explicitly specify a YAML config file (startup fails if missing) |
| `--no-config` | Skip config-file loading and bootstrap generation (pure defaults + env vars + CLI; for troubleshooting) |

## Config File (config.yaml)

Startup parameters can be managed in a single YAML file instead of long command lines.

### Auto-discovery and Bootstrap Generation

```bash
# Default behavior: auto-loads asr-service/config.yaml (config.yml alias supported);
# on first startup, an editable config.yaml is generated from config.example.yaml
bash start.sh

# Explicitly specify a config file
bash start.sh --config /path/to/my-config.yaml

# CLI arguments temporarily override the config file (this launch only, file unchanged)
bash start.sh --device cpu

# Skip the config file
bash start.sh --no-config
```

- The scan directory is the service root (`asr-service/`); `config.yaml` takes precedence over `config.yml` (a warning is logged when both exist).
- **Deleting `config.yaml` and restarting = resetting the configuration** (regenerated from the example).
- The bootstrap-generated `config.yaml` has permission `600` (it may contain `api_key`).

### Format and Validation

- YAML only, flat key-value mapping at the top level; all available keys are listed in [`asr-service/config.example.yaml`](../asr-service/config.example.yaml).
- **Hard validation at startup**: unknown keys (with did-you-mean hints), null values, type errors, out-of-range values and duplicate keys all abort startup with readable errors — typos never take effect silently; all errors are reported at once.
- Boolean switches set to `true` in the file can be overridden from the CLI with negative flags (`--no-punc` / `--no-web` / `--no-stream` / `--no-align` / `--no-task-store`).

### Security

- `config.yaml` / `config.yml` are in `.gitignore` — do not commit them (they may contain `api_key`).
- The `config_file` field of `GET /health` echoes the name of the active config file, so you can verify which configuration is in effect (anti "ghost config").

## Environment Variables

| Variable | Config Key | Description |
|----------|------------|-------------|
| `ASR_API_KEY` | `api_key` | API key; lower priority than the config file and CLI (`api_key: ""` in the config file also overrides it — remove that line to use the env var) |
| `MODEL_SOURCE` | `model_source` | Model download source |

Empty environment variables are treated as unset.

## Offline Task Persistence (tasks.db)

By default (built-in defaults) tasks live in memory only: terminal results are kept for 1 hour and lost on restart. With task persistence enabled, task metadata and final results are written to `asr-service/data/tasks.db` (SQLite) and remain queryable across restarts.

```yaml
# config.yaml (already enabled in configs generated from config.example.yaml)
enable_task_store: true
# task_db_path: data/tasks.db
# task_retention_days: 7    # retention window in days; 0 = never clean
```

### Behavior

- **Queryable results, no resume**: tasks left unfinished (`pending` / `processing`) at the previous shutdown are marked `failed` (`error: "service restarted"`) on restart; they are not re-run automatically.
- **Retention cleanup runs at startup only**: terminal records older than `task_retention_days` are deleted and space is reclaimed.
- Query and deletion endpoints for historical tasks: see [API reference · How Task Persistence Affects the API](api/v2_EN.md#how-task-persistence-affects-the-api).
- Only text results and metadata are stored — **no original audio is retained**; persistence write failures are logged as warnings and never affect task execution.
- Deleting `data/tasks.db` = clearing history without affecting functionality. For stricter content-retention requirements, lower `task_retention_days` or turn the switch off.

## Built-in Constants (app/config.py)

Built-in limits not exposed as startup parameters / config-file keys (edit `app/config.py` directly to change):

| Constant | Default | Description |
|----------|---------|-------------|
| MAX_AUDIO_DURATION | 14400s | Max audio duration (4 hours) |
| MAX_AUDIO_FILE_SIZE | 1024MB | Max upload file size |
| MIN_AUDIO_DURATION | 1.0s | Min audio duration |
| TASK_TIMEOUT | 1800s | Per-task timeout (30 minutes) |
| TASK_RESULT_TTL | 3600s | In-memory retention of terminal tasks (persisted history is unaffected) |
| STREAM_MAX_SESSION_SECONDS | 3600s | Max real-time session duration |
| STREAM_MAX_FRAME_BYTES | 2MB | Max binary frame size (real-time) |
| STREAM_MAX_BACKLOG_BYTES | 8MB | Max processing backlog (real-time, disconnects when exceeded) |
