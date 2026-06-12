# vLLM Engine Feature Overview (differences vs the default standard mode)

[← Back to docs home](../README.md) ｜ [中文](vllm-vs-standard.md) | **English**

> This page summarizes the **functional differences** of the **vLLM engine** (`--serve-mode vllm`) relative to the original default **standard** mode (funasr / OpenVINO), to aid selection and upgrade evaluation.
>
> **In one line**: the service originally had only the `standard` mode; this feature adds an **optional `vllm` mode** — GPU-native streaming (incremental, per-sentence) plus an offline endpoint with the same contract as standard, and brings speaker / compatibility-API capabilities to that mode. **The two modes are mutually exclusive at startup; standard mode's behavior, parameters and defaults are completely unchanged (zero regression).**

---

## 1. Core difference: a new vLLM serving mode

| | Original default (standard) | New (vllm) |
|---|---|---|
| Startup | fixed standard | `--serve-mode standard \| vllm` (pick one) |
| Inference backend | funasr (GPU) / OpenVINO (CPU) | vLLM native engine (in-process synchronous `vllm.LLM`) |
| Device | GPU / **CPU** | **GPU only (CUDA required; exits on non-GPU)** |
| Runtime env | `venv` (default) | isolated `venv-vllm` / separate Docker image |
| Process model | multi-worker OK | **uvicorn workers=1** (CUDA context in a separate subprocess) |

> vLLM's torch/CUDA stack cannot coexist with standard's, hence the **isolated environment and separate image** — they don't interfere.

---

## 2. Functional differences (by category)

### 2.1 Realtime streaming: whole-sentence → incremental (a qualitative jump)
- **standard**: the realtime backend is VAD-offline and **emits only a whole-sentence `final` after each segment is cut**, no per-token/intermediate increments (`partial_results=false`).
- **vLLM**: native streaming decode that **progressively refreshes `partial` within a sentence → `final` at sentence end** (`partial_results=true`) — the most visible realtime UX improvement of vLLM mode over standard.

### 2.2 Offline transcription `/v2/asr`
- Both modes share the **same contract**: `segments / full_text / words / speaker / speakers / warnings` fields are identical; zero changes for frontends and SDKs.
- vLLM implementation differences (trade-offs):
  - Segmentation: **punctuation-first** (split at sentence-end punctuation + word-level timestamp positioning), no FSMN fine segmentation.
  - Punctuation: **model-native**, always present, cannot be turned off individually (a request to disable it is recorded in `warnings`).
  - **Long-audio chunked transcription** (see 2.5).

### 2.3 Speaker diarization / identification
- Speaker capabilities (`--enable-speaker` / voiceprint DB `--enable-speaker-db`) are **supported in both modes** with identical output fields.
- vLLM difference: the voiceprint engine is the same CAM++, but **speech regions come from an energy VAD instead of FSMN-VAD** (dependency-neutral, no funasr, coarser boundaries); realtime streaming still carries no speaker labels (offline only).

### 2.4 Compatibility APIs (OpenAI / DashScope)
- Offline compatibility (OpenAI `audio/transcriptions`, DashScope recorded-file recognition) is **supported in both modes**.
- **The realtime incremental capability is new and exclusive to vLLM**:
  - standard realtime compat can only deliver **whole sentences** (limited by VAD-offline producing no partials).
  - vLLM realtime compat produces **per-token / intermediate increments**: DashScope intermediate `result-generated` (`sentence_end=false`, naturally cumulative, forwarded cleanly); OpenAI `…delta` (**best-effort**: partials are cumulative and may be revised, so only a pure append yields a delta suffix, revision frames are skipped, and the authoritative full text is the `…completed` event).
  - In vLLM mode, realtime compat **auto-mounts with the compat switches and needs no `--enable-stream`** (the only startup difference vs standard).

### 2.5 Long-audio handling (new, vLLM-exclusive)
- Offline audio longer than `vllm_offline_chunk_sec` (default 180s) is **transcribed chunk-by-chunk along silence boundaries** (chunks concatenate back to the original):
  - **Smooth progress**: reported per chunk during transcription (no more 10%→90% jump).
  - **Cancellable between chunks**: long tasks can respond to cancellation mid-way.
  - **VRAM convergence**: only 1 chunk is aligned at a time, **eliminating long-audio alignment `CUDA out of memory`**.
- Companion knobs: `--vllm-infer-batch-size` (audio chunks per alignment/ASR batch, default 4), `--vllm-align-device cpu` (move the aligner to CPU as an escape hatch).

---

## 3. Capability cheat sheet

| Capability | standard | vLLM |
|---|---|---|
| Device | GPU / CPU(OpenVINO) | GPU only (CUDA required) |
| Realtime streaming | optional; whole-sentence final only | always on; **incremental partial→final per sentence** |
| Offline `/v2/asr` | ✅ | ✅ (same contract) |
| Segmentation | FSMN-VAD fine segmentation | punctuation-first (word-timestamp positioning) |
| Punctuation | CT-Transformer (can disable) | model-native (always on, not separately disableable) |
| Speaker diarization / ID | ✅ (FSMN-VAD + CAM++) | ✅ (energy VAD + CAM++) |
| Compat offline (OpenAI/DashScope) | ✅ | ✅ |
| Compat realtime — whole sentence | ✅ (needs `--enable-stream`) | ✅ (with compat switches, no `--enable-stream`) |
| Compat realtime — per-token/intermediate | ❌ | ✅ DashScope intermediate / OpenAI delta(best-effort) |
| Long-audio chunked (progress/cancel/VRAM) | — (FSMN pipeline) | ✅ |

---

## 4. New configuration items and startup parameters

### vLLM-specific config (`config.yaml` key / corresponding CLI)

| Config key / CLI | Default | Description |
|---|---|---|
| `gpu_memory_utilization` / `--gpu-memory-utilization` | `0.6` | vLLM GPU memory utilization (single-stream ASR needs no 0.8) |
| `vllm_max_model_len` / `--vllm-max-model-len` | `32768` | Max context length per sequence |
| `vllm_chunk_size_sec` / `--vllm-chunk-size-sec` | `1.0` | Streaming decode chunk size (smaller = finer partials) |
| `vllm_max_utterance_sec` / `--vllm-max-utterance-sec` | `20` | Single-utterance fallback cut (sec) |
| `vllm_concurrency` / `--vllm-concurrency` | `1` | Concurrent decoding sessions (generate is serial; >1 gives no throughput gain) |
| `vllm_end_silence_ms` / `--vllm-end-silence-ms` | `800` | Energy-endpoint trailing-silence stop threshold |
| `vllm_enable_align` / `--vllm-enable-align`·`--no-vllm-align` | on | Offline word-level timestamps (load the aligner model) |
| `vllm_align_device` / `--vllm-align-device` | `cuda` | Aligner device; switch to `cpu` on long-audio OOM |
| `vllm_infer_batch_size` / `--vllm-infer-batch-size` | `4` | Audio chunks per alignment/ASR batch (`-1` risks OOM) |
| `vllm_segment_gap_ms` / `--vllm-segment-gap-ms` | `500` | Offline segmentation: inter-word gap split threshold |

### Config-file only (no CLI)

| Config key | Default | Description |
|---|---|---|
| `vllm_unfixed_chunk_num` | `2` | Leading streaming chunks that don't take history as prefix (cold-start stability) |
| `vllm_unfixed_token_num` | `5` | After leading chunks, roll back the last K tokens as prefix (reduce jitter) |
| `vllm_energy_floor_dbfs` | `-45.0` | Streaming energy-endpoint gate (dBFS) |
| `vllm_offline_chunk_sec` | `180` | Offline chunk-by-chunk transcription chunk length (sec) |

> Switches such as speaker (`--enable-speaker*`) and compatibility APIs (`--enable-openai-api` / `--enable-dashscope-api`) **already exist in standard**; vLLM mode reuses them — they are not new.

---

## 5. Deployment & operations changes

- **Isolated local env**: `bash setup.sh --vllm` creates `venv-vllm`; start with `QWEN_VENV=venv-vllm bash start.sh --serve-mode vllm`.
- **Separate Docker image**: `docker/Dockerfile.vllm` (derived from `vllm/vllm-openai:v0.14.0`) + `docker/docker-compose.vllm.yml` (separate port **8766**, coexists with standard's 8765); built via `docker/build.sh` option 4.
- **Interactive management script `manage.sh`**: the parameter panel gains a "serving mode" item (the panel morphs after picking vllm); venv launch auto-uses `venv-vllm`, Docker auto-uses the `-vllm` image tag, Compose orchestration / image pull gain a vLLM variant, and the venv menu manages both standard/vLLM environments.
- **CI**: a new standalone vLLM image build job (tags `:<ver>-vllm` / `:latest-vllm`, decoupled from GPU/CPU).

---

## 6. Impact on standard mode: zero regression

- All vLLM-related code is **dependency-neutral + lazily imported** (loaded only when `--serve-mode vllm`); standard / CPU modes pull in no vLLM dependencies.
- Top-level funasr-family imports in `main.py` are made fault-tolerant (so the app loads even in a vLLM env without funasr).
- standard's CLI, config defaults, API contract and behavior are **all unchanged**.

---

## 7. Known trade-offs and limitations

- **GPU only**: vLLM mode does not support CPU, and there is no CPU container.
- **Quality trade-offs**: segmentation (punctuation-first / word-gap) and speaker speech regions (energy VAD) are weaker than standard's FSMN; punctuation cannot be disabled individually. Use standard for high-fidelity segmentation/punctuation/realtime-speaker needs.
- **OpenAI realtime delta is best-effort**: the protocol expects incremental fragments while vLLM partials are cumulative and revisable; revision frames are skipped and the authoritative full text is the `completed` event.
- **Concurrency**: in-process synchronous inference; `workers=1`, and raising `concurrency` yields no throughput gain.

---

## Related docs

- [Configuration · vLLM Native Streaming Mode](configuration_EN.md#vllm-native-streaming-mode)
- [Deployment · vLLM image](deployment_EN.md)
- [Compatibility APIs](api/compat_EN.md)
