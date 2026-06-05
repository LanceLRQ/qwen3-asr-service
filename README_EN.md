# Qwen3-ASR Service

[中文](README.md) | **English**

An out-of-the-box long-form speech recognition API service based on Qwen3-ASR, with dual-mode inference: GPU (CUDA) and CPU (OpenVINO INT8).

## Features

- ⚡ **Fast startup, fast transcription** - The service starts quickly; long-audio transcription takes far less time than the audio duration — especially on GPU, while CPU mode stays efficient thanks to OpenVINO INT8 quantization
- **Out-of-the-box** - One-click installation and deployment, automatic model download, config file auto-generated on first startup
- **Long Audio Support** - Audio files from 1s to 4 hours with automatic VAD segmentation
- **Real-time Transcription** - WebSocket streaming endpoint, sentence-by-sentence results for microphone / streamed audio
- **Async Tasks + Persistence** - Submit and poll for results; task results queryable across restarts (tasks.db)
- **Multi-format Support** - WAV / MP3 / FLAC / M4A / AAC / OGG and more
- **Timestamps** - Sentence-level / word-level timestamps (GPU mode)
- **Auto Punctuation** - Integrated CT-Transformer punctuation restoration model
- **Web UI** - Modern interface (Vue 3 + Naive UI, dark theme): offline transcription, real-time transcription, auto-refreshing task history and offline documentation center
- **API Authentication** - Optional Bearer Token authentication
- **Flexible Configuration** - Four priority layers: YAML config file / CLI arguments / environment variables
- **Interactive Management** - CLI management script supporting Docker / venv dual-mode management

## Quick Start

> Requirements: Python 3.10+, ffmpeg; GPU mode needs NVIDIA GPU + CUDA 12.1+ (see the [deployment guide](docs/deployment_EN.md)).

```bash
cd asr-service
bash setup.sh        # Initialize the environment
bash start.sh        # Start the service (auto-detects device, downloads models, generates config.yaml)

# Verify
curl http://127.0.0.1:8765/v2/health
```

Open `http://127.0.0.1:8765/web-ui` in a browser to try it out (Web UI, real-time transcription and task persistence are enabled by default in the auto-generated config).

With Docker:

```bash
docker run -d --gpus all -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest --web
```

> Windows deployment, CPU/ARM64 modes, docker-compose, LAN access, API authentication and more: see the [deployment guide](docs/deployment_EN.md).

## Documentation

| Document | Contents |
|----------|----------|
| [Deployment Guide](docs/deployment_EN.md) | System requirements, Linux / Windows / Docker deployment, operation modes, Web UI, graceful shutdown |
| [Configuration Reference](docs/configuration_EN.md) | Full startup-parameter table, config.yaml, environment variables, task persistence, built-in constants |
| [API Reference v2 (default)](docs/api/v2_EN.md) | Offline batch processing, health / capabilities, real-time WebSocket protocol |
| [API Reference v1 (legacy)](docs/api/v1_EN.md) | Legacy-client compatibility notes and versioning conventions |
| [Architecture](docs/architecture_EN.md) | Project structure, processing pipeline, key design decisions |

---

If you find this project helpful, please consider giving a ⭐ on [GitHub](https://github.com/LanceLRQ/qwen3-asr-service) and [Docker Hub](https://hub.docker.com/r/lancelrq/qwen3-asr-service) — it really helps!
