# Qwen3-ASR Service Deployment Guide

[中文](deployment.md) | **English**

## Table of Contents

- [System Requirements](#system-requirements)
- [Linux Deployment](#linux-deployment)
- [Windows Deployment (Python Embeddable)](#windows-deployment-python-embeddable)
- [Enable API Authentication](#enable-api-authentication)
- [Docker Deployment](#docker-deployment)
- [Interactive Management Script](#interactive-management-script)
- [Verify the Service](#verify-the-service)
- [Three Operation Modes](#three-operation-modes)
- [CPU Mode Details](#cpu-mode-details)
- [Web UI](#web-ui)
- [Graceful Shutdown](#graceful-shutdown)

---

## System Requirements

- Python 3.10+
- ffmpeg (required)
- NVIDIA GPU + CUDA 12.1+ (required for GPU mode)
- OpenVINO >= 2024.0 (required for CPU mode, auto-installed via pip)

```bash
# Install ffmpeg (Ubuntu/Debian)
apt install ffmpeg

# Verify GPU environment (optional)
nvidia-smi
```

### GPU Mode PyTorch Version Requirements

| CUDA Version | PyTorch Version |
|-------------|----------------|
| CUDA 12.4 | `torch==2.6.0+cu124` |
| CUDA 12.1 | `torch==2.5.1+cu121` |

Installation example (CUDA 12.4):

```bash
pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
```

> Note: `qwen-asr` requires PyTorch 2.6+ or 2.5.1+cu121, and `funasr==1.3.1` to work properly.

## Linux Deployment

### 1. Initialize Environment

```bash
cd asr-service
bash setup.sh
```

### 2. Start the Service

```bash
# Default mode (auto-detects VRAM to select a model; first startup generates config.yaml and downloads models)
bash start.sh

# GPU full-featured mode (1.7B model + alignment)
bash start.sh --model-size 1.7b --enable-align

# GPU lightweight mode (0.6B model, no alignment)
bash start.sh --model-size 0.6b --no-align

# CPU mode (OpenVINO INT8 inference, no GPU required)
bash start.sh --device cpu --model-size 0.6b

# CPU mode + 1.7B model (higher accuracy, requires more memory)
bash start.sh --device cpu --model-size 1.7b

# Custom VAD segment merge duration (default 5 seconds)
bash start.sh --max-segment 15

# Specify model download source (modelscope recommended in China, huggingface overseas)
bash start.sh --model-source modelscope
bash start.sh --model-source huggingface
```

The service listens on `http://127.0.0.1:8765` by default (localhost only). For LAN access:

```bash
bash start.sh --host 0.0.0.0
bash start.sh --host 0.0.0.0 --port 9000
```

> Startup parameters can also be managed in `config.yaml` (auto-generated on first startup). Full parameter table and priority rules: see the [configuration reference](configuration_EN.md).

## Windows Deployment (Python Embeddable)

Windows can use the Python Embeddable Package for standalone portable deployment:

1. Download the [Python 3.12 Embeddable Package](https://www.python.org/downloads/windows/) and place it in the `bin/` directory
2. Download [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) and place `ffmpeg.exe` in the `bin/` directory
3. Run the initialization script (PowerShell):
   ```powershell
   cd asr-service
   .\setup.ps1
   ```
4. Start the service:
   ```powershell
   .\start.ps1 --device cuda --model-size 0.6b --host 0.0.0.0
   ```

> 💡 The PowerShell scripts (`.ps1`) are recommended. The same-named `.bat` files (`setup.bat` / `start.bat`) are kept only for legacy cmd compatibility and are not guaranteed to run on newer setups — switch to `.ps1` if you hit issues. If the first `.ps1` run is blocked by execution policy, use `powershell -ExecutionPolicy Bypass -File .\setup.ps1`.

## Enable API Authentication

After setting an API key, all endpoints (except `/health` and `/capabilities`) require a Bearer Token:

```bash
# Set via startup parameter
bash start.sh --api-key sk-your-key-here

# Or set via environment variable
export ASR_API_KEY=sk-your-key-here
bash start.sh

# Or set the api_key key in config.yaml (never commit that file)
```

Client usage: see [API reference · Authentication](api/v2/basics_EN.md#authentication).

## Docker Deployment

### Using Pre-built Images

```bash
# Pull the image
docker pull lancelrq/qwen3-asr-service:latest

# Start the container (GPU mode)
docker run -d --gpus all \
  -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  -v ./asr-service/logs:/app/logs \
  -v ./asr-service/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest \
  --model-size 0.6b --device auto --web
```

`/app/data` holds the task-persistence DB `tasks.db` and voiceprint DB `speakers.db`; mount it to keep them across container re-creation (compose mounts it by default). CPU / ARM64 images (`latest-cpu` / `latest-arm64`) and more volume details: see the [Docker Hub page](https://hub.docker.com/r/lancelrq/qwen3-asr-service).

### Using docker-compose

```bash
# Start directly (using default configuration in docker/docker-compose.yml)
docker compose -f docker/docker-compose.yml up -d

# Stop
docker compose -f docker/docker-compose.yml down
```

Startup parameters, API keys, port mappings, etc. can be configured in `docker/docker-compose.yml`. See comments in the file. The CPU variant lives in `docker/docker-compose.cpu.yml`.

### vLLM Native Streaming Image (standalone)

The vLLM mode (Route A, incremental partial→final streaming plus an offline `/v2/asr` with the same contract as standard) ships as a **standalone GPU-only image** derived from the official `vllm/vllm-openai` image — not merged with the default image, so standard users don't download vLLM's heavy CUDA kernels and vllm users don't download OpenVINO/funasr. For capability differences (including the offline segmentation/punctuation/speaker trade-offs) and parameters see [Configuration: vLLM Native Streaming Mode](configuration_EN.md#vllm-native-streaming-mode-route-a).

```bash
# Start (separate port 8766, coexists with standard asr on 8765)
docker compose -f docker/docker-compose.vllm.yml up -d

# Stop
docker compose -f docker/docker-compose.vllm.yml down

# Build the vLLM image locally (build.sh option 4)
bash docker/build.sh   # choose "4) vLLM"
```

> The vLLM engine holds the GPU in a separate EngineCore subprocess and the service runs a single worker (PID 1 reaps the subprocess in the container). It loads HF full-precision `models/asr/0.6b`/`1.7b` (shares the `models/` mount with standard).
>
> **Models for offline word timestamps / speakers**: `--vllm-enable-align` (on by default) needs the aligner `models/asr/aligner` (Qwen3-ForcedAligner, pulled from HF); `--enable-speaker` needs the voiceprint model `models/speaker/campplus` (CAM++, ModelScope-only) — `requirements-vllm.txt` bundles `modelscope` as a download fallback, but pre-mounting this directory is recommended in production to avoid runtime network access. Speaker clustering depends on `scipy`/`scikit-learn` (also bundled).
>
> ⚠️ **Long-audio alignment OOM**: the ForcedAligner loads in the main process and its VRAM is **not counted in `--gpu-memory-utilization`** (which only bounds the vLLM EngineCore subprocess). `transcribe` chunks internally at ≤180s but by default feeds **all** of a file's chunks into the aligner forward at once — long audio (e.g. 30 min) stacks activations and raises `CUDA out of memory` (short audio is a single chunk, unaffected). Remedies (recommended order): ① `--vllm-infer-batch-size` (now defaults to 4) aligns batch-by-batch, peak VRAM drops with batch size — drop to 1 if long audio still OOMs; ② `--vllm-align-device cpu` (aligner on CPU, no GPU contention, slower); ③ lower `--gpu-memory-utilization`; ④ `--no-vllm-align`. See the [configuration guide](configuration_EN.md).

> **Compatibility APIs (OpenAI/DashScope) & offline speakers**: vLLM mode also supports `--enable-openai-api`/`--enable-dashscope-api` (offline + realtime WS) and `--enable-speaker`/`--enable-speaker-db` (offline speaker diarization/identification, CAM++; speech regions come from an energy VAD, weaker than standard's FSMN). Key difference vs standard — vLLM streaming is always on, so **realtime compat auto-mounts with the compat switches and needs no `--enable-stream`**, and realtime supports per-token increments (DashScope intermediate results forwarded cleanly / OpenAI delta best-effort). Local launch uses the isolated env (not the default venv): `venv-vllm/bin/python -m app.main --serve-mode vllm …` (Docker is wrapped by the compose above; vLLM requires CUDA and exits on non-GPU devices). See the [compatibility API docs](api/compat_EN.md) for capabilities and protocol.

#### vLLM Startup Logs (expected, not failures)

On startup/shutdown the vLLM service prints two lines that look like errors but are **harmless** — safe to ignore:

1. **`ERROR … repo_utils.py … Error retrieving safetensors: Repo id must be in the form …` (retries twice)**
   While inferring the model dtype, vLLM has an upstream quirk for **local model directories**: instead of detecting the local path first, it passes the absolute path to the HF Hub as a repo id, which the repo-id format validator rejects. The exception is caught internally and the dtype falls back to the model config, so the **model still loads as bfloat16** — no impact on functionality or VRAM. `HF_HUB_OFFLINE=1` does not suppress this line (the format check runs before the offline check). Ignore it.

2. **On exit (`Ctrl+C` / `docker stop`): `Engine core proc EngineCore_DP0 died unexpectedly`**
   vLLM keeps the CUDA context in a separate `EngineCore` subprocess; on shutdown it exits together with the main process, and the client's monitor thread prints this line — a **normal shutdown event**, not a crash.

**To confirm the service is actually ready**, rely on these two signals (not on the presence/absence of the ERROR above):

```
INFO: Application startup complete.
INFO: Uvicorn running on http://<host>:<port>
```

```bash
curl http://127.0.0.1:8765/v2/health   # ready when it returns {"status":"ready","mode":"vllm",...}
```

> Model loading (torch.compile + weights) takes tens of seconds; wait for `Uvicorn running` before judging — don't mistake the loading phase for a failure. For local (non-container) runs, if VRAM doesn't drop after `Ctrl+C`, an `EngineCore` subprocess lingers — clear it with `pkill -KILL -f EngineCore` (container deployments reap it automatically via PID 1).

### Build Image Locally

```bash
bash docker/build.sh
```

## Interactive Management Script

The project provides interactive management scripts in the repository root for unified management of both Docker and local venv environments:

```bash
# Linux / macOS
bash manage.sh

# Windows
.\manage.ps1
```

Management script features:

- **Docker Compose start (config.yaml-driven, recommended)**: on first use, auto-generates `config.yaml` from `config.example.yaml`; edit the config then start/stop/restart the container, view logs, switch GPU/CPU/**vLLM** compose
- Docker management (pull images in GPU/CPU/**vLLM** variants, build images, parameter-wizard start/stop containers, view logs)
- Virtual environment management (install/uninstall/view info for both standard `venv` and **vLLM `venv-vllm`**)
- Start service (interactive parameter configuration including **`serve-mode` standard/vllm**; vLLM auto-uses the venv-vllm env / `-vllm` image; config saving supported)

## Verify the Service

```bash
curl http://127.0.0.1:8765/v2/health
```

Response example (GPU mode):

```json
{
  "status": "ready",
  "device": "cuda",
  "model_size": "0.6b",
  "align_enabled": true,
  "punc_enabled": true,
  "asr_backend": "qwen_asr",
  "vad_backend": "pytorch",
  "punc_backend": "pytorch"
}
```

In CPU mode, `asr_backend` is `openvino` and `vad_backend`/`punc_backend` are `onnx`. Full field reference: [API reference · Health Check](api/v2/basics_EN.md#health-check).

## Three Operation Modes

| | GPU Full-featured | GPU Lightweight | CPU (OpenVINO) |
|--|-------------------|-----------------|----------------|
| ASR | Qwen3-ASR + CUDA | Qwen3-ASR + CUDA | **OpenVINO INT8** |
| Inference Framework | PyTorch (transformers) | PyTorch (transformers) | **OpenVINO (pure NumPy preprocessing)** |
| Alignment | ForcedAligner | **Disabled** | **Force disabled** |
| VAD | FSMN-VAD (PyTorch) | FSMN-VAD (PyTorch) | FSMN-VAD (**ONNX**) |
| Punctuation | CT-Transformer (PyTorch) | CT-Transformer (PyTorch) | CT-Transformer (**ONNX**) |
| Timestamps | Word-level | Sentence-level | Sentence-level |
| VRAM Required | ~6-8GB | ~2-3GB | No GPU, ~4-6GB RAM |
| Model Source | ModelScope / HuggingFace | ModelScope / HuggingFace | **HuggingFace** |

> With `--device auto`, the service auto-selects based on VRAM: >=6GB uses 1.7B, 4-6GB uses 0.6B, <4GB force-disables alignment, no GPU falls back to CPU (OpenVINO).

## CPU Mode Details

CPU mode uses the OpenVINO inference engine instead of PyTorch. Key features:

- **INT8 Quantized Models**: Significantly reduced memory usage and computation compared to FP32
- **Pure NumPy Preprocessing**: Mel feature extraction and BPE decoding fully implemented in NumPy, no torch/transformers dependency for inference
- **Initial Compilation Time**: OpenVINO model compilation takes ~10-30 seconds, executed only once at startup
- **Auto Model Download**: Automatically downloads OpenVINO format models from HuggingFace on first startup

OpenVINO models used in CPU mode:

| Model Size | HuggingFace Repository | Quantization |
|-----------|----------------------|--------------|
| 0.6B | `dseditor/Qwen3-ASR-0.6B-INT8_ASYM-OpenVINO` | INT8 Asymmetric |
| 1.7B | `dseditor/Qwen3-ASR-1.7B-INT8_OpenVINO` | INT8 |

## Web UI

Start with `--web` (enabled by default in configs generated from the example) and open `http://<host>:<port>/web-ui`:

- Drag-and-drop or click to upload audio files
- Real-time recognition progress with one-click cancel
- Segmented results with clickable segments for audio playback at corresponding positions
- Full text display, raw JSON viewing and download
- **Auto-refreshing task list**: updates every 3s while tasks are running, every 30s when idle (paused in background tabs); persisted historical tasks can be viewed and deleted
- Dark theme: follows the OS, with a manual toggle

The UI is built with Vue 3 + Naive UI (library files ship with the repository — **no node/npm, no build step**, works offline right after clone).

When started with `--enable-stream`, `/web-ui/stream` provides a real-time transcription test page (microphone capture / simulated streaming from an audio file, with protocol log and diagnostics views). The ffmpeg-wasm transcoder used by simulated file streaming loads from the internet; on failure it automatically falls back to the browser's native decoder.

`/web-ui/docs` is the built-in documentation center — it renders all of this repository's user docs offline (deployment / configuration / API / architecture, bilingual), browsable without internet access.

> The service also ships Swagger UI, an interactive API playground at `http://<host>:<port>/docs` (auto-generated by FastAPI, independent of the `--web` switch). Note: this link only works while the service is running (it won't navigate when reading this document on GitHub), and the Swagger page loads its static assets from a public CDN, so it won't render in offline environments.

## Graceful Shutdown

The service supports `Ctrl+C` for graceful shutdown. Upon pressing:

1. Stops accepting new requests
2. Cancels in-progress ASR tasks (stops immediately after the current chunk completes)
3. Shuts down worker threads and thread pool
4. Cleans up temporary files
