# Qwen3-ASR Service Deployment Guide

[中文](deployment.md) | **English**

## Table of Contents

- [System Requirements](#system-requirements)
- [Linux Deployment](#linux-deployment)
- [Windows Deployment (Python Embeddable)](#windows-deployment-python-embeddable)
- [Enable API Authentication](#enable-api-authentication)
- [Docker Deployment](#docker-deployment)
- [Interactive CLI Management](#interactive-cli-management)
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
3. Run the initialization script:
   ```cmd
   cd asr-service
   setup.bat
   ```
4. Start the service:
   ```cmd
   start.bat --device cuda --model-size 0.6b --host 0.0.0.0
   ```

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

Client usage: see [API reference · Authentication](api/v2_EN.md#authentication).

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
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest \
  --model-size 0.6b --device auto --web
```

CPU / ARM64 images (`latest-cpu` / `latest-arm64`) and volume details: see the [Docker Hub page](https://hub.docker.com/r/lancelrq/qwen3-asr-service). When task persistence is enabled, also mount `/app/data`.

### Using docker-compose

```bash
# Start directly (using default configuration in docker-compose.yml)
docker compose up -d

# Stop
docker compose down
```

Startup parameters, API keys, port mappings, etc. can be configured in `docker-compose.yml`. See comments in the file.

### Build Image Locally

```bash
bash build.sh
```

## Interactive CLI Management

The project provides interactive management scripts for unified management of both Docker and local venv environments:

```bash
# Linux / macOS
bash asr-service/cli.sh

# Windows
asr-service\cli.bat
```

CLI management script features:

- Docker management (pull/build images, start/stop containers, view logs)
- Virtual environment management (install/uninstall/view info)
- Start service (interactive parameter configuration with config saving)

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

In CPU mode, `asr_backend` is `openvino` and `vad_backend`/`punc_backend` are `onnx`. Full field reference: [API reference · Health Check](api/v2_EN.md#health-check).

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
