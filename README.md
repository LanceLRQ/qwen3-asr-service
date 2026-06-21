# Qwen3-ASR Service

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![GitHub stars](https://img.shields.io/github/stars/LanceLRQ/qwen3-asr-service?style=flat-square&logo=github)](https://github.com/LanceLRQ/qwen3-asr-service/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/lancelrq/qwen3-asr-service?style=flat-square&logo=docker)](https://hub.docker.com/r/lancelrq/qwen3-asr-service)
[![Docker Image Size](https://img.shields.io/docker/image-size/lancelrq/qwen3-asr-service/latest?style=flat-square&logo=docker)](https://hub.docker.com/r/lancelrq/qwen3-asr-service)
[![Docker Build](https://img.shields.io/github/actions/workflow/status/LanceLRQ/qwen3-asr-service/docker-publish.yml?style=flat-square&logo=githubactions&logoColor=white&label=Docker%20Build)](https://github.com/LanceLRQ/qwen3-asr-service/actions/workflows/docker-publish.yml)
[![Powered by Qwen3-ASR](https://img.shields.io/badge/Powered%20by-Qwen3--ASR-ff6a00?style=flat-square)](https://github.com/QwenLM)

[中文](README_zh.md) | **English**

A simple, fast and efficient speech recognition API service based on Qwen3-ASR. Out-of-the-box, with offline long-form and real-time streaming transcription in one, speaker diarization / voiceprint identification, and a feature-rich, polished Web UI. Cross-platform on Linux / macOS / Windows with Docker container deployment, and dual-mode inference on GPU (CUDA) and CPU (OpenVINO INT8).

一个基于 Qwen3-ASR 的简单、快速、高效的语音识别 API 服务。开箱即用，集离线长音频转写与实时流式转写于一体，支持说话人分离 / 声纹识别，并配备功能丰富、精致的 Web UI。跨平台支持 Linux / macOS / Windows 及 Docker 容器化部署，同时提供 GPU（CUDA）与 CPU（OpenVINO INT8）双模式推理。

## Features

- ⚡ **Fast startup, fast transcription** - The service starts quickly; long-audio transcription takes far less time than the audio duration — especially on GPU, while CPU mode stays efficient thanks to OpenVINO INT8 quantization
- **Real-time Transcription** - WebSocket streaming endpoint, sentence-by-sentence results for microphone / streamed audio
- **vLLM Native Streaming Engine** *(new in v2.1.0, optional)* - a separate GPU-only serving mode (`--serve-mode vllm`) with real-time incremental partial→final decoding within each sentence, plus long-audio chunked transcription — see [vLLM vs Standard](docs/vllm-vs-standard_EN.md)
- **Speaker Diarization** - Offline / real-time transcripts annotated with anonymous speaker labels A/B/C… (CAM++ voiceprint model, CPU inference)
- **Voiceprint Database** - Enrolled speakers show their real names in transcripts; unknown speakers are auto-enrolled with placeholder names, with one-click rename in the Web management page (speakers.db, authentication required)
- **Audio Tagging** *(optional)* - General audio event tagging (full AudioSet via PANNs 527-class / YAMNet 521-class) + derived scene (silence/speech/singing/music/other): offline results add `audio_events` + per-segment `scene`; the realtime stream pushes `scene` messages; plus a tagging-only `POST /v2/audio/tag`. Toggleable, dual-engine, with a configurable scene map. Enable with `--enable-audio-tagging`. (YAMNet is a non-recommended lightweight fallback: lower accuracy than PANNs, requires an extra optional dependency (`pip install -r requirements-yamnet.txt`), and is unavailable in vLLM mode.)
- **Far-field Filtering / Tunable Params** - Real-time segment-level energy/SNR gating reduces far-field and ambient false triggers; speaker, endpointing and output params can be overridden per request/session
- **OpenAI / DashScope Compatible APIs** - Point your base_url at this service to integrate the OpenAI / Alibaba Cloud DashScope ecosystem (offline + realtime) — no business-code changes
- **Async Tasks + Persistence** - Submit and poll for results; task results queryable across restarts (tasks.db)
- **Web UI** - Modern interface (Vue 3 + Naive UI, dark theme): offline transcription, real-time transcription, speaker management, auto-refreshing task history and offline documentation center
- **Flexible Configuration** - Four priority layers: YAML config file / CLI arguments / environment variables
- **Out-of-the-box** - One-click installation and deployment, automatic model download, config file auto-generated on first startup
- **Long Audio Support** - Audio files from 1s to 4 hours with automatic VAD segmentation
- **Multi-format Support** - WAV / MP3 / FLAC / M4A / AAC / OGG and more
- **Timestamps** - Sentence-level / word-level timestamps (GPU mode)
- **Accurate Sentence Segmentation** *(new in v2.2.0)* - Sentences reassembled by punctuation, pause and speaker change, decoupled from processing-chunk duration (no fixed-length mid-sentence cuts)
- **Auto Punctuation** - Integrated CT-Transformer punctuation restoration model
- **API Authentication** - Optional Bearer Token authentication
- **Interactive Management** - CLI management script supporting Docker / venv dual-mode management

## Quick Start

> Requirements: Python 3.10+, ffmpeg; GPU mode needs NVIDIA GPU + CUDA 12.1+ (see the [deployment guide](docs/deployment_EN.md)).

**Recommended**: run the interactive management script at the repo root (unified Docker / venv entry, guided install and start/stop):

```bash
bash manage.sh          # Linux / macOS; on Windows run .\manage.ps1 in PowerShell
```

> Or do it manually, step by step (in the app directory):
> ```bash
> cd asr-service
> bash setup.sh        # Initialize the environment
> bash start.sh        # Start the service (auto-detects device, downloads models, generates config.yaml)
> ```

```bash
# Verify
curl http://127.0.0.1:8765/v2/health
```

> ⚠️ **Upgrading from v1**: if you already have a v1 virtual environment, v2 adds new dependencies (speaker diarization, voiceprint database, documentation center, etc.) — update them before starting. Re-run `bash setup.sh` and answer `N` when asked to recreate the venv to keep your existing one; the script still installs/updates the new dependencies from `requirements.txt`.

Open `http://127.0.0.1:8765/web-ui` in a browser to try it out (Web UI, real-time transcription and task persistence are enabled by default in the auto-generated config).

With Docker:

```bash
docker run -d --gpus all -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest --web
```

> Windows deployment, CPU/ARM64 modes, docker-compose, LAN access, API authentication and more: see the [deployment guide](docs/deployment_EN.md).

## Preview

| Offline Transcription | Real-time Transcription |
| :---: | :---: |
| ![Offline Transcription](docs/images/offline.webp) | ![Real-time Transcription](docs/images/online.webp) |

## Documentation

| Document | Contents |
|----------|----------|
| [Deployment Guide](docs/deployment_EN.md) | System requirements, Linux / Windows / Docker deployment, operation modes, Web UI, graceful shutdown |
| [Configuration Reference](docs/configuration_EN.md) | Full startup-parameter table, config.yaml, environment variables, task persistence, built-in constants |
| [vLLM vs Standard](docs/vllm-vs-standard_EN.md) | Feature differences of the vLLM engine vs the default standard mode (selection & upgrade guide) |
| [API Reference v2 (default)](docs/api/v2_EN.md) | Offline batch processing, health / capabilities, real-time WebSocket protocol |
| [API Reference v1 (legacy)](docs/api/v1_EN.md) | Legacy-client compatibility notes and versioning conventions |
| [Compatibility APIs](docs/api/compat_EN.md) | OpenAI / Alibaba Cloud DashScope drop-in compatibility (offline + realtime), just change base_url |
| [Architecture](docs/architecture_EN.md) | Project structure, processing pipeline, key design decisions |
| [Development Guide](docs/development_EN.md) | Dev environment, testing, E2E smoke, single-schema / docs / compat-layer conventions |
| [Third-Party Notices](THIRD_PARTY_NOTICES.md) | Licenses and attributions for bundled models and dependencies |

---

If you find this project helpful, please consider giving a ⭐ on [GitHub](https://github.com/LanceLRQ/qwen3-asr-service) and [Docker Hub](https://hub.docker.com/r/lancelrq/qwen3-asr-service) — it really helps!
