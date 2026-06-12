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
- [Speaker Diarization & Voiceprint Database (speakers.db)](#speaker-diarization--voiceprint-database-speakersdb)
- [vLLM Native Streaming Mode (Route A)](#vllm-native-streaming-mode-route-a)
- [Built-in Constants (app/config.py)](#built-in-constants-appconfigpy)

---

## Startup Parameters (full table)

All parameters are passed through `bash start.sh <args>`. Config-file key = long CLI flag with dashes converted to underscores (e.g. `--model-size` → `model_size`; the only exception: `--use-punc` → `use_punc`).

### Basics

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--serve-mode` | `standard` / `vllm` | `standard` | Serving mode; `vllm` = vLLM native streaming (GPU-only, partial→final realtime + offline `/v2/asr`) |
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

### Far-field Noise Filtering

Reduces false triggers from far-field sounds and ambient noise. `--vad-speech-noise-thres` tunes VAD sensitivity (offline + real-time unified); `--stream-noise-filter` enables real-time segment-level energy/SNR gating (real-time only, off by default).

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--vad-speech-noise-thres` | Float | `0.6` | FSMN-VAD speech/noise decision threshold (offline + real-time unified); higher = more aggressive filtering of far-field/weak frames, recommended `0.6`–`0.8` |
| `--stream-noise-filter` / `--no-stream-noise-filter` | - | Disabled | Master switch for real-time segment-level energy/SNR gating (opt-in) |
| `--stream-energy-floor-dbfs` | Float | `-50.0` | Absolute energy gate (dBFS, full-scale referenced): segments quieter than this are dropped |
| `--stream-snr-min-db` | Float | `6.0` | Adaptive SNR gate (dB): segments not exceeding the session noise floor by this margin are dropped; `<=0` disables this gate |

### Task Persistence

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--enable-task-store` / `--no-task-store` | - | Disabled (enabled in configs generated from the example) | Offline task persistence (results queryable across restarts) |
| `--task-db-path` | Path | `data/tasks.db` | Task database path (relative to the service root) |
| `--task-retention-days` | Days | `7` | Retention window for expired tasks, cleaned at startup; `0` = never clean |

### Speaker Diarization

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--enable-speaker` / `--no-speaker` | - | Disabled | Speaker diarization: offline `segments[].speaker` / real-time `final.speaker` (anonymous A/B/C…); the CAM++ model (28MB) is auto-downloaded on first use and runs on CPU without consuming VRAM |
| `--speaker-threshold` | 0–1 | `0.5` | Online clustering cosine threshold for real-time (recommended range 0.35–0.65; higher splits speakers more aggressively, lower merges them more aggressively) |
| `--speaker-max` | Number | `8` | Upper bound on speaker count (hard cap in real-time; upper bound of the cluster-count search in offline spectral clustering) |
| `--speaker-min-seg-ms` | Milliseconds | `1500` | Real-time short-segment gate: segments shorter than this neither create a new cluster nor update a centroid (voiceprint features only stabilize at ≥1.5s) |
| `--speaker-max-windows` | Number | `4000` | Upper bound on offline sliding windows; the excess is uniformly subsampled (memory guard for clustering very long audio) |

### Voiceprint Database

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--enable-speaker-db` / `--no-speaker-db` | - | Disabled | Voiceprint database (enrollment + real-name identification): requires `enable_speaker` and **must have `api_key` configured** (voiceprints are biometric data, no unauthenticated access allowed, otherwise the module automatically degrades and disables itself) |
| `--speaker-db-path` | Path | `data/speakers.db` | Voiceprint database path (relative to the service root); **data is never auto-cleaned** |
| `--speaker-id-threshold` | 0–1 | `0.45` | 1:N open-set identification threshold; if the highest similarity is below this, the result is `unknown` |
| `--speaker-id-margin` | 0–1 | `0.10` | top1-top2 margin; if the gap is smaller than this, the result is `unknown` (when neighbors compete, prefer omission over error) |
| `--speaker-enroll-min-sec` | Seconds | `3.0` | Minimum effective speech per sample for manual enrollment (after VAD) |
| `--speaker-auto-enroll` / `--no-speaker-auto-enroll` | - | Enabled | Auto-enroll unmatched speakers from offline identification as `Speaker_NN` (**enabling auto-enroll = the deployer declares data-subject consent has been obtained**) |
| `--speaker-auto-enroll-min-sec` | Seconds | `10.0` | Minimum total speech duration of a cluster for auto-enrollment (stricter than manual enrollment, to reduce noisy records) |
| `--speaker-store-audio` / `--no-speaker-store-audio` | - | Disabled | Retain enrollment sample audio in `data/speaker_audio/` (widens the compliance surface, off by default) |

### vLLM Native Streaming (only `--serve-mode vllm`)

Effective only in vllm mode; requires a CUDA GPU and an isolated environment/image (see [vLLM Native Streaming Mode](#vllm-native-streaming-mode-route-a) below).

| Parameter | Value | Default | Description |
|------|------|--------|------|
| `--gpu-memory-utilization` | 0–1 | `0.6` | vLLM GPU memory utilization (×total VRAM as budget; single-stream ASR needs no 0.8) |
| `--vllm-max-model-len` | number | `32768` | Max context length; too large raises the KV cache floor and prevents low-utilization startup |
| `--vllm-chunk-size-sec` | float | `1.0` | Streaming decode chunk size (sec); smaller = finer partials (range 0.5–5) |
| `--vllm-max-utterance-sec` | number | `20` | Per-utterance hard cut (sec); bounds context/memory growth |
| `--vllm-concurrency` | number | `1` | Concurrent decoding sessions (generate is serial; >1 yields no throughput) |
| `--vllm-end-silence-ms` | ms | `800` | Energy endpointer end-silence threshold |
| `--vllm-enable-align` / `--no-vllm-align` | - | on | Offline `/v2/asr` word timestamps: load the aligner (off saves VRAM, no words) |
| `--vllm-align-device` | `cuda` / `cpu` | `cuda` | Aligner device; its VRAM is **outside the `gpu_memory_utilization` budget** — switch to `cpu` (float32, slower, no GPU contention) if the aligner OOMs on long audio |
| `--vllm-infer-batch-size` | number | `4` | Audio chunks per alignment/ASR batch (chunks ≤180s); `-1`=all at once (long-audio aligner OOM from stacked activations), lower to save VRAM, drop to `1` if long audio still OOMs |
| `--vllm-segment-gap-ms` | ms | `500` | Offline segmentation: split when the inter-word gap exceeds this (no FSMN; word-gap proxy) |

**Config-file only (no CLI; set in `config.yaml`):**

| Config key | Default | Description |
|------------|---------|-------------|
| `vllm_unfixed_chunk_num` | `2` | Number of leading streaming chunks that don't take history as prefix (cold-start stability) |
| `vllm_unfixed_token_num` | `5` | After the leading chunks, roll back the last K tokens as prefix (reduces jitter) |
| `vllm_energy_floor_dbfs` | `-45.0` | Streaming energy-endpoint gate (dBFS); above this counts as speech / sentence start |
| `vllm_offline_chunk_sec` | `180` | Offline chunk-by-chunk transcription chunk length (sec); lower = finer progress, lower peak VRAM (see [Long audio & progress](#vllm-native-streaming-mode-route-a) below) |

### Config-file Meta Parameters

| Parameter | Description |
|-----------|-------------|
| `--config <PATH>` | Explicitly specify a YAML config file (startup fails if missing) |
| `--no-config` | Skip config-file loading and bootstrap generation (pure defaults + env vars + CLI; for troubleshooting) |
| `--update-config` | **Update the local config and exit without starting the service**: append **recommended** keys missing from `config.yaml` from `config.example.yaml` (only add, never overwrite; keep existing values) |
| `--all` | With `--update-config`: also add **advanced/optional** keys, written commented out (`# key: default`, ready to uncomment); by default only recommended keys are added |

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
- **Sync missing keys (`--update-config`)**: `--update-config` is a **standalone maintenance command** — it only updates the config file, then **exits without starting the service**. It appends keys **missing** from the target config, **only adding, never overwriting**: existing values and keys you've commented/declared stay untouched. Appended lines have **inline comments stripped and no extra marker**, keeping `config.yaml` concise.
  - **Default adds recommended keys only**: the **active (uncommented)** keys in the example, written as `key: default`.
  - **`--all` also adds advanced keys**: the **commented** advanced/optional keys in the example are added too, but kept commented out (`# key: default`, a disabled default reference, ready to uncomment) — this avoids mistaking an "enabled recommended value" for the default and writing it active.
  - Target precedence: the file given via `--config` > the auto-discovered `config.yaml`/`config.yml`; if neither exists locally it is bootstrapped from the example. `--update-config` is mutually exclusive with `--no-config`.

  ```bash
  # Fill in new config keys after an upgrade (updates then exits, no service start)
  bash start.sh --update-config           # recommended keys only
  bash start.sh --update-config --all     # also advanced/optional keys (commented out)
  ```

### Format and Validation

- YAML only, flat key-value mapping at the top level; all available keys are listed in [`asr-service/config.example.yaml`](../asr-service/config.example.yaml).
- **Hard validation at startup**: unknown keys (with did-you-mean hints), null values, type errors, out-of-range values and duplicate keys all abort startup with readable errors — typos never take effect silently; all errors are reported at once.
- Boolean switches set to `true` in the file can be overridden from the CLI with negative flags (`--no-punc` / `--no-web` / `--no-stream` / `--no-align` / `--no-task-store` / `--no-speaker` / `--no-speaker-db` / `--no-speaker-auto-enroll` / `--no-speaker-store-audio`).

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
- Query and deletion endpoints for historical tasks: see [API reference · How Task Persistence Affects the API](api/v2/tasks_EN.md#how-task-persistence-affects-the-api).
- Only text results and metadata are stored — **no original audio is retained**; persistence write failures are logged as warnings and never affect task execution.
- Deleting `data/tasks.db` = clearing history without affecting functionality. For stricter content-retention requirements, lower `task_retention_days` or turn the switch off.

## Speaker Diarization & Voiceprint Database (speakers.db)

```yaml
# config.yaml: enable diarization (anonymous labels)
enable_speaker: true

# further enable the voiceprint database (real-name identification) — api_key must also be configured
enable_speaker_db: true
api_key: "sk-your-key"
```

### Behavior

- **Diarization**: anonymous labels `A`/`B`/`C`… scoped to a single file/session; the same person is not guaranteed the same label across tasks; any failure along the way only drops the label, transcription is unaffected.
- **Voiceprint-database degradation matrix**: if any of the following conditions is not met, the module automatically degrades and disables itself (ERROR log + `/v2/speakers*` returns 503, while the service still starts normally): ① `enable_speaker` is on and the engine loads successfully; ② `api_key` is non-empty; ③ the database is created successfully. When stored templates do not match the current engine's `model_tag`, only enrollment/identification is disabled while viewing and deletion are retained (right to be forgotten).
- **Data is never auto-cleaned**: `speakers.db` has no TTL (unlike tasks.db's 7-day cleanup) — voiceprints are a long-term accumulating asset, and identification gets more accurate the more they are used; the only way to delete is `DELETE /v2/speakers/{id}` (hard delete + physical reclamation) or deleting the database file.
- **Auto-enrollment**: during offline transcription with `identify_speakers` enabled, speakers that miss the database and have sufficient speech (default ≥10s) are auto-enrolled as `说话人_NN` (Chinese for "Speaker_NN"); after renaming via `/web-ui/speakers` or `PATCH /v2/speakers/{id}`, subsequent transcriptions display the real name directly; the real-time path does not auto-enroll (online clustering drift easily causes duplicate records); matched speakers never get templates auto-appended either (to prevent sample poisoning) — add samples manually via `POST /v2/speakers/{id}/templates`.
- **Compliance**: the enrollment endpoint enforces `consent=true` as double-insurance (endpoint + database constraint); enabling auto-enrollment means the deployer declares data-subject consent has been obtained; audio is not retained by default; the audit log is persisted alongside the database. **Backup = copy the single `data/speakers.db` file** (recommend including it in your regular backup plan); deleting the database completely erases all voiceprint data.

## vLLM Native Streaming Mode (Route A)

`--serve-mode vllm` enables the vLLM native streaming engine, providing **incremental (partial→final)** real-time transcription, plus an **offline `/v2/asr`** with the same contract as `standard`; it is **mutually exclusive** with the default `standard` mode (Route B: online VAD + offline decode, per-segment final).

**Capability differences**

| Aspect | standard (default) | vllm |
|------|-----------------|------|
| Endpoints | offline v1/v2 + realtime WS (`--enable-stream`) | **offline v1/v2 + realtime WS `/v2/asr/stream` (always on)** + `/health` `/capabilities` |
| Incremental results (realtime) | none (per-segment final) | **partial→final** |
| Word timestamps | supported | **offline supported** (ForcedAligner, on by default); not in realtime |
| Speaker diarization/ID | supported | **offline supported** (CAM++, requires `--enable-speaker`; energy-VAD windowing); not in realtime |
| Punctuation | CT-Transformer (toggleable) | model-native (**cannot be turned off**) |
| Offline segment boundaries | FSMN-VAD | punctuation-first / whole-text fallback (coarser) |
| Device | GPU / CPU | **CUDA GPU only** (CPU disabled by design, no CPU image) |
| Throughput | concurrent sessions | single-stream serial (generate is serial; ≈ standard) |
| Deps / image | funasr + OpenVINO… | isolated vLLM env (no funasr/OpenVINO), separate image |

**Why an isolated environment**: vLLM pins a specific torch/CUDA (incompatible with standard's torch), so it requires an isolated `venv-vllm` or a separate image (`docker/Dockerfile.vllm`, derived from the official vLLM image). See the [deployment guide](deployment_EN.md).

**Offline transcription (`/v2/asr`)**: vllm mode reuses the **exact same async task contract** as standard (`POST /v2/asr` → `task_id` → poll `GET /v2/tasks/{id}`, persistence, cancel), with ASR via vLLM batched `transcribe`. All differences from standard are **quality differences that do not break the result structure**, and are flagged in `result.warnings`:
- **Segmentation**: punctuation-first splitting on model-native sentence punctuation (`。！？；`, with comma sub-split for sentences exceeding `--max-segment`); word timestamps only locate start/end. Falls back to inter-word gap (`--vllm-segment-gap-ms`, default 500ms) / a single whole-text segment when the aligner is off. Boundary precision is lower than FSMN-VAD.
- **Punctuation**: produced natively by Qwen3-ASR (already punctuated); cannot be turned off — `with_punc=false` is recorded in `warnings`.
- **Word timestamps**: `--vllm-enable-align` (on by default) via ForcedAligner, same as standard; `--no-vllm-align` disables it to save VRAM.
  - ⚠️ **Long-audio alignment OOM**: the aligner is a standalone transformers model in the main process and its VRAM is **not counted in `gpu_memory_utilization`** (which only bounds the vLLM EngineCore subprocess). `transcribe` chunks internally at ≤180s, but by default (`max_inference_batch_size=-1`) it feeds **all** of a file's chunks into the aligner forward at once — long audio (e.g. 30 min ≈ 10 chunks) stacks activations and hits `CUDA out of memory` (short audio is a single chunk and is unaffected). Remedies (recommended order): ① **`--vllm-infer-batch-size`** (now defaults to `4`) aligns batch-by-batch, peak VRAM drops linearly with batch size, stays on GPU and is fastest — drop to `1` if long audio still OOMs; ② `--vllm-align-device cpu` to run the aligner on CPU (no GPU contention, slower but safe); ③ lower `--gpu-memory-utilization` to leave more GPU headroom; ④ `--no-vllm-align` to drop word timestamps.
- **Long audio & progress**: offline audio longer than `vllm_offline_chunk_sec` (default 180s) is transcribed **chunk by chunk** at silence boundaries, so transcription progress (0.1→0.85) updates per chunk and cancellation is honored between chunks (short audio is transcribed in one pass). Chunks use qwen_asr's own splitting (concatenation reproduces the original), so quality matches whole-file transcription; lowering `vllm_offline_chunk_sec` gives finer progress and lower peak VRAM.
- **Speaker diarization/ID**: with `--enable-speaker` (plus `--enable-speaker-db` for the voiceprint DB), offline `segments[].speaker` / `speaker_name` / `speakers` match standard; the engine is CAM++ (CPU, torch, not funasr), and **windowing uses an energy VAD instead of FSMN-VAD** (coarser boundaries). When disabled, `diarize`/`identify_speakers` are recorded in `warnings`. Requires extra deps `scipy`/`scikit-learn`/`modelscope` (or a pre-mounted CAM++ model dir); see [requirements-vllm.txt](../asr-service/requirements-vllm.txt). Realtime streaming still has no speaker labels.
> For high-fidelity with FSMN segmentation / CT-Transformer punctuation / realtime speakers, use `standard` mode.

**Compatibility APIs (`/compat/*`)**: vllm mode also supports the OpenAI / DashScope compatibility APIs, with the same switches as standard (`--enable-openai-api` / `--enable-dashscope-api`); endpoint docs are in the [development guide](development_EN.md). Differences from standard:
- **Offline compat** (OpenAI `audio/transcriptions`·`models`, DashScope file transcription) reuses the vLLM offline pipeline, so segmentation/punctuation/speaker quality is as described above; `audio/translations` stays 501 (ASR-only, naturally aligned with standard).
- **Realtime compat** (OpenAI `WS /realtime`, DashScope `WS …/inference`) is mounted alongside the compat switches (vLLM streaming is always on, **no** `--enable-stream` needed); vLLM's incremental partials are forwarded over the compat protocols (`capabilities.compat.realtime_partial=true`): DashScope uses intermediate `result-generated` (`sentence_end=false`, a natural fit for its cumulative semantics); OpenAI uses `…transcription.delta`, which is **best-effort** — OpenAI expects incremental chunks while vLLM partials are cumulative and may be revised, so only append-only frames emit the new suffix as a delta (revision frames are skipped) and the authoritative full transcript is always the `…completed` event.
- DashScope server-side `file_urls` download needs `httpx` (bundled in requirements-vllm); the SSRF guard and `--compat-fetch-*` options are inherited from standard.
- The `compat` section of `/capabilities` reflects the mounted endpoints: `{openai, dashscope, realtime, realtime_partial}`.

**Start**

```bash
# Local: first install the isolated venv-vllm (requirements-vllm.txt brings vllm/torch), then start
bash asr-service/setup.sh --vllm                                          # create venv-vllm + install deps
QWEN_VENV=venv-vllm bash asr-service/start.sh --serve-mode vllm --model-size 0.6b --web
# Or call the venv-vllm interpreter directly:
asr-service/venv-vllm/bin/python -m app.main --serve-mode vllm --model-size 0.6b --web

# Docker (separate image, separate port 8766)
docker compose -f docker/docker-compose.vllm.yml up -d

# Interactive: bash manage.sh → venv mode (pick serve-mode vllm) / compose mode (switch to vLLM)
```

**Notes**

- **Process model**: the vLLM engine holds the GPU in a separate EngineCore subprocess; the service runs a **single worker** (uvicorn default workers=1; multiple workers would each load the model and exhaust VRAM).
- **Graceful stop**: on exit the EngineCore subprocess may not be reaped immediately with the parent; in Docker the container stop reaps it. For manual runs, `pkill -f "serve-mode vllm"` then verify VRAM is freed via `nvidia-smi`.
- **Model**: loads HF full-precision `models/asr/0.6b` or `1.7b` (not the OpenVINO quantized variants); the web demo (`--web` → `/web-ui/stream`) already renders partial→final live.

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
