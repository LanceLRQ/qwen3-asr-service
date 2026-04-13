# Qwen3-ASR Service

[中文](README.md) | **English**

An out-of-the-box long-form speech recognition API service based on Qwen3-ASR.

## Features

- **Out-of-the-box** - One-click installation and deployment with automatic model download
- **Long Audio Support** - Supports audio files from 1s to 4 hours with automatic VAD segmentation
- **Multi-format Support** - WAV / MP3 / FLAC / M4A / AAC / OGG and more
- **Flexible Deployment** - Dual mode: GPU (CUDA) and CPU (OpenVINO INT8)
- **Async Tasks** - Submit tasks and poll for results, supports large file processing
- **Timestamps** - Sentence-level / word-level timestamps (GPU mode)
- **Auto Punctuation** - Integrated CT-Transformer punctuation restoration model
- **Web UI** - Built-in browser interface with audio upload, real-time progress, result playback and export
- **API Authentication** - Optional Bearer Token authentication, compatible with OpenAI API format
- **Interactive Management** - CLI management script supporting Docker / venv dual-mode management

## System Requirements

- Python 3.10+
- ffmpeg (required)
- NVIDIA GPU + CUDA 12.1+ (required for GPU mode)
- OpenVINO >= 2024.0 (required for CPU mode, auto-installed via pip)

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

```bash
# Install ffmpeg (Ubuntu/Debian)
apt install ffmpeg

# Verify GPU environment (optional)
nvidia-smi
```

## Quick Start

### Windows Deployment (Python Embeddable)

Windows can use Python Embeddable Package for standalone portable deployment:

1. Download [Python 3.12 Embeddable Package](https://www.python.org/downloads/windows/) and place it in the `bin/` directory
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

### Linux Deployment

#### 1. Initialize Environment

```bash
cd asr-service
bash setup.sh
```

### 2. Start the Service

```bash
# GPU default mode (auto-detect VRAM, select model size)
bash start.sh

# GPU full-featured mode (1.7B model + alignment)
bash start.sh --model-size 1.7b --enable-align

# GPU lightweight mode (0.6B model, no alignment)
bash start.sh --model-size 0.6b --no-align

# CPU mode (OpenVINO INT8 inference, no GPU required)
bash start.sh --device cpu --model-size 0.6b

# CPU mode + 1.7B model (higher accuracy, requires more memory)
bash start.sh --device cpu --model-size 1.7b

# Enable Web UI (access at http://0.0.0.0:8765/web-ui)
bash start.sh --web

# Custom VAD segment merge duration (default 5 seconds)
bash start.sh --max-segment 15

# Specify model download source (modelscope recommended for China, huggingface for overseas)
bash start.sh --model-source modelscope
bash start.sh --model-source huggingface
```

The service listens on `http://127.0.0.1:8765` by default (localhost only).

For LAN access:

```bash
bash start.sh --host 0.0.0.0
bash start.sh --host 0.0.0.0 --port 9000
```

#### Enable API Authentication

After setting an API key, all `/v1/asr` and `/v1/asr/{task_id}` endpoints require a Bearer Token:

```bash
# Set via startup parameter
bash start.sh --api-key sk-your-key-here

# Or set via environment variable
export ASR_API_KEY=sk-your-key-here
bash start.sh
```

### Docker Deployment

#### Using Pre-built Images

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

#### Using docker-compose

```bash
# Start directly (using default configuration in docker-compose.yml)
docker compose up -d

# Stop
docker compose down
```

Startup parameters, API keys, port mappings, etc. can be configured in `docker-compose.yml`. See comments in the file for details.

#### Build Image Locally

```bash
bash build.sh
```

### Interactive CLI Management

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

### 3. Verify the Service

```bash
curl http://127.0.0.1:8765/v1/health
```

Response examples:

GPU mode:

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

CPU mode:

```json
{
  "status": "ready",
  "device": "cpu",
  "model_size": "0.6b",
  "align_enabled": false,
  "punc_enabled": true,
  "asr_backend": "openvino",
  "vad_backend": "onnx",
  "punc_backend": "onnx"
}
```

## Startup Parameters

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--device` | `auto` / `cuda` / `cpu` | `auto` | Device, auto-detects |
| `--model-size` | `0.6b` / `1.7b` | Auto-selected by VRAM | ASR model size |
| `--enable-align` / `--no-align` | - | `--enable-align` | Load alignment model (word-level timestamps) |
| `--use-punc` | - | Disabled | Enable punctuation restoration |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | Model download source |
| `--host` | IP address | `127.0.0.1` | Listen address, set to `0.0.0.0` for LAN access |
| `--port` | Port number | `8765` | Listen port |
| `--web` | - | Disabled | Enable Web UI (access at `/web-ui`) |
| `--max-segment` | Seconds | `5` | VAD segment merge max duration |
| `--api-key` | String | None | API key, enables Bearer Token auth when set (overrides `ASR_API_KEY` env var) |
| `--max-queue-size` | Number | `100` | Maximum task queue size |

### Three Operation Modes

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

### CPU Mode Details

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

## API Reference

### Submit ASR Task

```bash
curl -X POST http://127.0.0.1:8765/v1/asr \
  -F "file=@/path/to/audio.wav"
```

With optional parameters:

```bash
curl -X POST http://127.0.0.1:8765/v1/asr \
  -F "file=@/path/to/audio.mp3" \
  -F "language=zh"
```

If API authentication is enabled, include a Bearer Token in the request:

```bash
curl -X POST http://127.0.0.1:8765/v1/asr \
  -H "Authorization: Bearer sk-your-key-here" \
  -F "file=@/path/to/audio.wav"
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| file | File | Required | Audio file (WAV/MP3/FLAC/M4A/AAC/OGG, etc.) |
| language | string | null | Language code, null for auto-detection |

Response:

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

**Limits**: Maximum file size 1GB, audio duration 1s to 4 hours.

### Query Task Status

```bash
curl http://127.0.0.1:8765/v1/asr/{task_id}
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
  "error": null
}
```

- The `words` field is only present when `align_enabled=true`
- Task status flow: `pending` → `processing` → `completed` / `failed`

### Health Check

```bash
curl http://127.0.0.1:8765/v1/health
```

Response:

```json
{
  "status": "ready",
  "device": "cuda",
  "model_size": "0.6b",
  "align_enabled": true,
  "punc_enabled": false,
  "asr_backend": "qwen_asr",
  "vad_backend": "pytorch",
  "punc_backend": "pytorch"
}
```

| Field | Description |
|-------|-------------|
| status | Service status, `ready` means operational |
| device | Running device, `cuda` or `cpu` |
| model_size | ASR model size, `0.6b` or `1.7b` |
| align_enabled | Whether alignment model is enabled (word-level timestamps) |
| punc_enabled | Whether punctuation restoration is enabled |
| asr_backend | ASR backend, `qwen_asr` or `openvino` |
| vad_backend | VAD backend, `pytorch` or `onnx` |
| punc_backend | Punctuation backend, `pytorch`, `onnx`, or `disabled` |

## Web UI

Add the `--web` parameter at startup to enable the browser interface:

```bash
bash start.sh --web
```

Access `http://<host>:<port>/web-ui` for the following features:

- Drag-and-drop or click to upload audio files
- Real-time recognition progress display
- Segmented results with clickable segments for audio playback at corresponding positions
- Full text display
- Raw JSON data viewing and download

## Project Structure

```
asr-service/
├── app/
│   ├── main.py                    # Service entry point (argparse startup parameters)
│   ├── config.py                  # Global configuration
│   ├── api/
│   │   ├── routes.py              # FastAPI routes
│   │   └── schemas.py             # Request/response data models
│   ├── engines/
│   │   ├── qwen_asr_engine.py     # Qwen3-ASR recognition engine (GPU)
│   │   ├── openvino_asr_engine.py # OpenVINO ASR engine (CPU)
│   │   ├── processor_numpy.py     # Pure NumPy Mel extraction + BPE decoding
│   │   ├── vad_engine.py          # FSMN-VAD voice activity detection engine
│   │   └── punc_engine.py         # CT-Transformer punctuation engine
│   ├── pipeline/
│   │   ├── asr_pipeline.py        # ASR pipeline orchestration
│   │   └── audio_preprocessor.py  # ffmpeg format conversion
│   ├── runtime/
│   │   ├── device.py              # Device detection and selection
│   │   └── task_manager.py        # Task queue management
│   ├── web/
│   │   ├── views.py               # Web UI routes
│   │   └── page.py                # Web UI single-page app (HTML/CSS/JS)
│   └── utils/
│       ├── logger.py              # Logging configuration
│       ├── model_manager.py       # Model download management
│       └── openvino_model_downloader.py  # OpenVINO model download
├── models/                        # Model storage (auto-downloaded, not committed to Git)
├── cache/                         # Runtime cache (uploaded files, audio segments)
├── logs/                          # Log files
├── setup.sh / setup.bat           # Environment initialization
├── start.sh / start.bat           # Service startup
├── cli.sh / cli.bat               # Interactive CLI management script
└── requirements.txt               # Dependencies

# Project root
├── Dockerfile                     # Docker image build
├── docker-compose.yml             # Docker Compose orchestration
└── build.sh                       # Image build script
```

## Processing Pipeline

**GPU Mode:**

```
Audio File → ffmpeg convert (16kHz WAV) → VAD segmentation → Segment merge → ASR recognition → [Punctuation] → Output
                                          (FSMN-VAD)         (≤5s)          (Qwen3-ASR)       (CT-Transformer)
                                                                                ↓
                                                                     [Optional] Alignment (ForcedAligner)
```

**CPU Mode (OpenVINO):**

```
Audio File → ffmpeg convert (16kHz WAV) → VAD segmentation → Segment merge → ASR recognition → [Punctuation] → Output
                                          (FSMN-VAD          (≤5s)          (OpenVINO          (CT-Transformer
                                           ONNX)                              INT8)               ONNX)
                                                                ↓
                                              NumPy Mel extraction → audio_encoder
                                                                   → thinker_embeddings
                                                                   → decoder autoregressive decoding
                                                                   → BPE decode
```

## Configuration

Main configuration in `app/config.py`:

| Config | Default | Description |
|--------|---------|-------------|
| HOST | 127.0.0.1 | Listen address (overridable via `--host`) |
| PORT | 8765 | Listen port (overridable via `--port`) |
| MAX_SEGMENT_DURATION | 5s | VAD segment merge / long segment split threshold (overridable via `--max-segment`) |
| MAX_AUDIO_DURATION | 14400s | Maximum audio duration (4 hours) |
| MAX_AUDIO_FILE_SIZE | 1024MB | Maximum file size |
| MIN_AUDIO_DURATION | 1.0s | Minimum audio duration |
| MAX_QUEUE_SIZE | 100 | Maximum task queue size (overridable via `--max-queue-size`) |
| TASK_TIMEOUT | 1800s | Single task timeout (30 minutes) |

## Graceful Shutdown

The service supports `Ctrl+C` for graceful shutdown. Upon pressing:

1. Stops accepting new requests
2. Cancels in-progress ASR tasks (stops immediately after current chunk completes)
3. Shuts down worker threads and thread pool
4. Cleans up temporary files
