**[English](#features)** | **[中文](#特性)**

---

A simple, fast and efficient speech recognition API service based on Qwen3-ASR. Offline long-form + real-time streaming transcription, speaker diarization / voiceprint identification, OpenAI / DashScope compatible APIs and a built-in Web UI; dual-mode inference on GPU (CUDA) and CPU (OpenVINO INT8).

### Supported tags and respective Dockerfile links

**GPU** (CUDA 12.1, requires NVIDIA GPU and nvidia-docker)
- [`latest`, `2.4.0`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile)
- [`2.2.0`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile) — previous release

**CPU** (multi-arch: amd64 + arm64, no GPU required — for standard Linux/Windows servers, Apple Silicon and ARM64 Linux servers)
- [`latest-cpu`, `2.4.0-cpu`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.cpu) — multi-arch manifest; Docker auto-selects amd64 or arm64 by host
- [`2.2.0-cpu`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.cpu) — previous release

**vLLM** (GPU-native streaming, amd64, requires NVIDIA GPU and nvidia-docker) — *new in 2.1.0*
- [`latest-vllm`, `2.4.0-vllm`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.vllm) — vLLM-native engine (based on `vllm/vllm-openai`); adds real-time incremental partial→final streaming

> Note: separate `*-arm64` tags are deprecated since 2.0.2 — arm64 is now folded into the multi-arch `*-cpu` tag. Older `2.0.0-arm64` / `1.2.0-arm64` tags remain available for existing users.
> The `*-vllm` image is an independent, optional GPU-only variant — it does not replace `latest`; standard mode behavior is unchanged. See [vLLM vs Standard](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard_EN.md).

### Image tag comparison

| Tag | Base Image | Arch | Inference Engine | NVIDIA GPU | Image Size (compressed / on-disk) |
|-----|-----------|------|-----------------|-----------|-----------|
| `latest` / `2.4.0` | `nvidia/cuda:12.1.1-runtime-ubuntu22.04` | amd64 | PyTorch (CUDA) | Required | ~4.9GB / ~8-10GB |
| `latest-cpu` / `2.4.0-cpu` | `ubuntu:22.04` | amd64 + arm64 (multi-arch) | OpenVINO (amd64: INT8 / arm64: FP32, selected at runtime) | Not required | ~2GB / ~3-4GB |
| `latest-vllm` / `2.4.0-vllm` | `vllm/vllm-openai:v0.14.0` | amd64 | vLLM (CUDA, native streaming) | Required | ~9GB / very large |

### Features

- Long audio support from 1s to 4 hours with automatic VAD segmentation
- Multiple formats: WAV / MP3 / FLAC / M4A / AAC / OGG / WMA / AMR / OPUS
- Async task queue — submit and poll for results
- Sentence-level and word-level timestamps (GPU mode)
- **Accurate sentence segmentation** *(new in 2.2.0)* — sentences are reassembled by punctuation, pause and speaker change, decoupled from processing-chunk duration (no fixed-length mid-sentence cuts)
- Optional punctuation restoration (CT-Transformer)
- **Speaker diarization** with anonymous labels (A/B/C…), enabled via `--enable-speaker`
- **Voiceprint library** for real-name speaker identification (`/v2/speakers*`), enabled via `--enable-speaker-db` (requires an API key)
- **Real-time voiceprint enrollment / speaker_id return** *(new in 2.4.0)* — a per-request `return_speaker_id` switch returns voiceprint-DB UUIDs (offline `segments[].speaker_id`, real-time `final.speaker_id`) for cross-session client-side memory; real-time also supports an `enroll` WebSocket message to enroll the current speaker, with an optional server-side `stream_speaker_auto_enroll`
- **Audio tagging** *(new in 2.3.0, optional)* — general audio event tagging (full AudioSet, PANNs 527-class / YAMNet 521-class) + derived scene (silence/speech/singing/music/other): offline results gain `audio_events` + per-segment `scene`, the realtime stream pushes `scene` messages, plus a tagging-only `POST /v2/audio/tag`. Enable with `--enable-audio-tagging`
- **Real-time speech transcription** (WebSocket endpoint, enabled via `--enable-stream`)
- **vLLM native-streaming engine** *(new in 2.1.0, optional)* — a separate GPU-only serving mode (`latest-vllm` image) with real-time incremental `partial`→`final` decoding within each sentence, plus long-audio chunked transcription; see [vLLM vs Standard](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard_EN.md)
- **OpenAI / DashScope compatible APIs** — drop-in `/compat/*` endpoints (offline + realtime), just change `base_url`; enabled via `--enable-openai-api` / `--enable-dashscope-api`
- Task management: list, filter by status, cancel tasks, persisted task history (survives restarts)
- Optional Bearer Token API authentication (OpenAI-compatible format)
- YAML config file for unified parameter management (auto-generated on first startup)
- Built-in Web UI for uploading audio, tracking progress, playing results, and exporting; bundled offline docs center at `/web-ui/docs`

### Quick Start

#### GPU Mode

```bash
docker run -d --gpus all \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest
```

#### CPU Mode (x86)

```bash
docker run -d \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest-cpu
```

#### ARM64 (Apple Silicon, ARM64 Linux servers)

ARM64 is served by the same multi-arch `latest-cpu` tag — Docker automatically pulls the arm64 image on ARM64 hosts. Just use `latest-cpu` as in CPU mode above:

```bash
docker run -d \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest-cpu
```

#### vLLM Mode (GPU-native streaming, new in 2.1.0)

```bash
docker run -d --gpus all \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-vllm \
  lancelrq/qwen3-asr-service:latest-vllm
```

> The `latest-vllm` image bakes in `--serve-mode vllm` and requires an NVIDIA GPU (CUDA, amd64 only). It adds real-time incremental streaming and long-audio chunked transcription; see [vLLM vs Standard](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard_EN.md) for the full feature differences and trade-offs. To run it alongside the standard image, map a different host port (e.g. `-p 8766:8765`).

Models are downloaded automatically on first startup. Mount `/app/models` to persist them across restarts.

> The CPU image does not require NVIDIA GPU or nvidia-docker.

### Docker Compose

```yaml
services:
  asr:
    image: lancelrq/qwen3-asr-service:latest
    ports:
      - "8765:8765"
    # environment:
    #   - ASR_API_KEY=sk-your-key-here
    volumes:
      - ./models:/app/models
      - ./logs:/app/logs
      - ./data:/app/data
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command:
      - --model-size=0.6b
      - --device=auto
      - --model-source=modelscope
      - --enable-align
      - --web
      # - --enable-stream        # real-time WebSocket transcription
      # - --enable-task-store     # offline task persistence (results survive restarts)
      # - --enable-speaker        # speaker diarization (anonymous A/B/C… labels)
      # - --enable-speaker-db     # voiceprint real-name identification (needs --api-key)
      # - --enable-openai-api     # OpenAI-compatible /compat/openai/v1/* endpoints
      # - --enable-dashscope-api  # DashScope-compatible /compat/dashscope/* endpoints
    restart: unless-stopped
```

### Parameters

All parameters are passed via `command`:

| Parameter | Values | Default | Description |
|-----------|--------|---------|-------------|
| `--device` | `auto` / `cuda` / `cpu` | `auto` | Device selection, auto-detects GPU |
| `--model-size` | `0.6b` / `1.7b` | Auto | ASR model size |
| `--enable-align` / `--no-align` | - | Enabled | Forced alignment (word-level timestamps) |
| `--use-punc` | - | Disabled | Punctuation restoration |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | Model download source |
| `--port` | Port number | `8765` | Listening port |
| `--web` | - | Disabled | Enable Web UI (access `/web-ui`) |
| `--max-segment` | Seconds | `5` | Max VAD segment merge duration |
| `--api-key` | String | None | API key, enables Bearer Token authentication |
| `--max-queue-size` | Number | `100` | Max task queue size |
| `--enable-stream` | - | Disabled | Real-time endpoint `WS /v2/asr/stream` |
| `--enable-task-store` | - | Disabled | Offline task persistence (results survive restarts) |
| `--enable-speaker` | - | Disabled | Speaker diarization (anonymous A/B/C… labels) |
| `--enable-speaker-db` | - | Disabled | Voiceprint library for real-name identification (requires `--enable-speaker` + API key) |
| `--enable-openai-api` | - | Disabled | OpenAI-compatible endpoints `/compat/openai/v1/*` (realtime `WS /realtime` needs `--enable-stream`) |
| `--enable-dashscope-api` | - | Disabled | DashScope-compatible endpoints `/compat/dashscope/*` (realtime `WS /inference` needs `--enable-stream`) |

> The container always listens on `0.0.0.0` internally. Use `-p` to map the port for external access.
> Full parameter table and YAML config-file usage: see the [configuration reference](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/configuration_EN.md).

### Volumes

| Container Path | Description |
|---------------|-------------|
| `/app/models` | Model files (auto-downloaded on first run, mount to persist) |
| `/app/logs` | Service logs |
| `/app/data` | SQLite databases: task persistence `tasks.db` and voiceprint library `speakers.db` — mount to keep them across container re-creation |

### API Usage

Full API documentation (parameters, response structures, error codes, WebSocket real-time protocol) is in the GitHub repository:

- [API reference v2 (default version)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2_EN.md) — split into sub-docs:
  - [Basics & authentication](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/basics_EN.md)
  - [Transcription (offline batch + real-time stream)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/transcription_EN.md)
  - [Task management](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/tasks_EN.md)
  - [Speaker diarization & voiceprint library](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/speakers_EN.md)
- [API reference v1 (legacy version)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v1_EN.md)
- [Compatibility APIs (OpenAI / DashScope drop-in, offline + realtime)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/compat_EN.md)
- [Configuration reference (startup parameters / config.yaml / task persistence)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/configuration_EN.md)
- [vLLM vs Standard (vLLM-native streaming mode, feature differences & selection guide)](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard_EN.md)

Quick check:

```bash
# Health check
curl http://localhost:8765/v2/health

# Submit a task (add -H "Authorization: Bearer sk-your-key-here" when auth is enabled)
curl -X POST http://localhost:8765/v2/asr -F "file=@audio.wav"

# Query the result
curl http://localhost:8765/v2/tasks/{task_id}
```

### Mode Comparison

| | GPU Mode | CPU Mode (amd64) | CPU Mode (arm64) |
|--|---------|---------|-----------|
| Image Tag | `latest` | `latest-cpu` | `latest-cpu` (same multi-arch tag) |
| Inference Engine | PyTorch (CUDA) | OpenVINO (INT8) | OpenVINO (FP32) |
| Alignment (word timestamps) | Supported | Not supported | Not supported |
| VRAM / Memory | ~2-8GB VRAM | ~4-6GB RAM | ~4-6GB RAM |
| Model Source | ModelScope / HuggingFace | HuggingFace | HuggingFace |
| NVIDIA GPU | Required | Not required | Not required |

> With `--device auto`, the service selects automatically: >=6GB VRAM uses 1.7B, 4-6GB uses 0.6B, no GPU falls back to CPU.
>
> The above three columns are the **standard** image variants. The optional **vLLM** image (`latest-vllm`) is a distinct GPU-native streaming mode with its own segmentation, speaker and realtime behavior — see [vLLM vs Standard](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard_EN.md).

### Source Code

[GitHub: qwen3-asr-service](https://github.com/LanceLRQ/qwen3-asr-service)

If you find this project helpful, please consider giving a ⭐ on [GitHub](https://github.com/LanceLRQ/qwen3-asr-service) and [Docker Hub](https://hub.docker.com/r/lancelrq/qwen3-asr-service) — it really helps!

---

基于 Qwen3-ASR 的简单、快速、高效语音识别 API 服务。离线长音频 + 实时流式转写，支持说话人分离 / 声纹库识别、OpenAI / DashScope 兼容接口与内置 Web UI；支持 GPU（CUDA）和 CPU（OpenVINO INT8）双模式推理。

### Supported tags and respective Dockerfile links

**GPU 版本**（CUDA 12.1，需要 NVIDIA GPU 和 nvidia-docker）
- [`latest`, `2.4.0`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile)
- [`2.2.0`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile) — 历史版本

**CPU 版本**（多架构：amd64 + arm64，无需 GPU，适用于普通 Linux/Windows 服务器、Apple Silicon、ARM64 Linux 服务器）
- [`latest-cpu`, `2.4.0-cpu`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.cpu) — 多架构 manifest，Docker 按本机架构自动选择 amd64 或 arm64
- [`2.2.0-cpu`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.cpu) — 历史版本

**vLLM 版本**（GPU 原生流式，amd64，需要 NVIDIA GPU 和 nvidia-docker）— *2.1.0 新增*
- [`latest-vllm`, `2.4.0-vllm`](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docker/Dockerfile.vllm) — vLLM 原生引擎（基底 `vllm/vllm-openai`），新增实时逐句 partial→final 增量流式

> 说明：自 2.0.2 起独立 `*-arm64` tag 已弃用，arm64 已并入多架构 `*-cpu` tag。历史 `2.0.0-arm64` / `1.2.0-arm64` 仍保留供存量用户使用。
> `*-vllm` 镜像是独立、可选的纯 GPU 变体，不替代 `latest`；standard 模式行为不变。详见 [vLLM 与 standard 差异](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard.md)。

### 镜像版本对比

| Tag | 基础镜像 | 架构 | 推理引擎 | NVIDIA GPU | 镜像体积（压缩 / 解压） |
|-----|---------|------|---------|-----------|---------|
| `latest` / `2.4.0` | `nvidia/cuda:12.1.1-runtime-ubuntu22.04` | amd64 | PyTorch (CUDA) | 需要 | ~4.9GB / ~8-10GB |
| `latest-cpu` / `2.4.0-cpu` | `ubuntu:22.04` | amd64 + arm64（多架构） | OpenVINO（amd64: INT8 / arm64: FP32，运行时自选） | 不需要 | ~2GB / ~3-4GB |
| `latest-vllm` / `2.4.0-vllm` | `vllm/vllm-openai:v0.14.0` | amd64 | vLLM（CUDA，原生流式） | 需要 | ~9GB / 体积较大 |

### 特性

- 支持 1s ~ 4 小时的长语音文件，自动 VAD 切片处理
- 多格式支持：WAV / MP3 / FLAC / M4A / AAC / OGG / WMA / AMR / OPUS
- 异步任务队列，提交后轮询结果
- 句子级 / 单词级时间戳（GPU 模式）
- **准确分句** *（2.2.0 新增）*：按标点 + 停顿 + 说话人切换重组句子，与处理切块时长解耦（不再按固定时长拦腰切句）
- 可选标点恢复（CT-Transformer）
- **说话人分离**：匿名标签（A/B/C…），`--enable-speaker` 开启
- **声纹库识别**：真名识别接口 `/v2/speakers*`，`--enable-speaker-db` 开启（需配置 API 密钥）
- **实时声纹登记 / speaker_id 回传** *（2.4.0 新增）*：按请求开关 `return_speaker_id` 回传声纹库 UUID（离线 `segments[].speaker_id`、实时 `final.speaker_id`），供客户端跨会话记忆声纹；实时另支持经 WebSocket `enroll` 消息登记当前说话人，服务端可选 `stream_speaker_auto_enroll` 自动登记
- **音频标注** *（2.3.0 新增，可选）*：通用音频事件标注（AudioSet 全类，PANNs 527 类 / YAMNet 521 类）+ 派生场景（静音/说话/唱歌/音乐/其他）：离线结果加 `audio_events` 与每段 `scene`，实时流推 `scene` 消息，另有只打标不转写的 `POST /v2/audio/tag`。`--enable-audio-tagging` 开启
- **实时语音转写**：WebSocket 端点，`--enable-stream` 开启
- **vLLM 原生流式引擎** *（2.1.0 新增，可选）*：独立的纯 GPU 运行模式（`latest-vllm` 镜像），句内实时 `partial`→`final` 增量解码 + 长音频逐块转写，详见 [vLLM 与 standard 差异](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard.md)
- **OpenAI / DashScope 兼容接口**：drop-in `/compat/*` 端点（离线 + 实时），改 `base_url` 即接入，`--enable-openai-api` / `--enable-dashscope-api` 开启
- 任务管理：列表查询、状态筛选、任务取消、历史任务持久化（跨重启可查）
- 可选 Bearer Token API 认证（兼容 OpenAI 格式）
- YAML 配置文件统一管理启动参数（首启自动生成）
- 内置 Web UI，支持音频上传、进度展示、结果播放和导出；内置离线文档中心 `/web-ui/docs`

### 快速启动

#### GPU 模式

```bash
docker run -d --gpus all \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest
```

#### CPU 模式（x86）

```bash
docker run -d \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest-cpu
```

#### ARM64（Apple Silicon、ARM64 Linux 服务器）

ARM64 由多架构 `latest-cpu` tag 直接提供——在 ARM64 主机上 Docker 会自动拉取 arm64 镜像，直接按上面 CPU 模式使用 `latest-cpu` 即可：

```bash
docker run -d \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest-cpu
```

#### vLLM 模式（GPU 原生流式，2.1.0 新增）

```bash
docker run -d --gpus all \
  -p 8765:8765 \
  -v /path/to/models:/app/models \
  -v /path/to/logs:/app/logs \
  -v /path/to/data:/app/data \
  --name qwen3-asr-vllm \
  lancelrq/qwen3-asr-service:latest-vllm
```

> `latest-vllm` 镜像已内置 `--serve-mode vllm`，需要 NVIDIA GPU（CUDA，仅 amd64）。新增实时增量流式与长音频逐块转写，完整功能差异与取舍见 [vLLM 与 standard 差异](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard.md)。如需与 standard 镜像并行运行，请映射不同的宿主端口（如 `-p 8766:8765`）。

首次启动会自动下载模型文件，挂载 `/app/models` 目录可持久化模型避免重复下载。

> CPU 镜像无需 NVIDIA GPU 和 nvidia-docker，开箱即用。

### Docker Compose

```yaml
services:
  asr:
    image: lancelrq/qwen3-asr-service:latest
    ports:
      - "8765:8765"
    # environment:
    #   - ASR_API_KEY=sk-your-key-here
    volumes:
      - ./models:/app/models
      - ./logs:/app/logs
      - ./data:/app/data
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    command:
      - --model-size=0.6b
      - --device=auto
      - --model-source=modelscope
      - --enable-align
      - --web
      # - --enable-stream        # 实时 WebSocket 转写
      # - --enable-task-store     # 离线任务持久化（结果跨重启可查）
      # - --enable-speaker        # 说话人分离（匿名 A/B/C… 标签）
      # - --enable-speaker-db     # 声纹库真名识别（需配合 --api-key）
      # - --enable-openai-api     # OpenAI 兼容端点 /compat/openai/v1/*
      # - --enable-dashscope-api  # DashScope 兼容端点 /compat/dashscope/*
    restart: unless-stopped
```

### 启动参数

所有参数均通过 `command` 传入：

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--device` | `auto` / `cuda` / `cpu` | `auto` | 运行设备，auto 自动检测 |
| `--model-size` | `0.6b` / `1.7b` | 自动选择 | ASR 模型大小 |
| `--enable-align` / `--no-align` | - | 启用 | 对齐模型（单词级时间戳） |
| `--use-punc` | - | 关闭 | 标点恢复 |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | 模型下载源 |
| `--port` | 端口号 | `8765` | 监听端口 |
| `--web` | - | 关闭 | 启用 Web UI（访问 `/web-ui`） |
| `--max-segment` | 秒数 | `5` | VAD 切片合并最大时长 |
| `--api-key` | 字符串 | 无 | API 密钥，启用 Bearer Token 认证 |
| `--max-queue-size` | 数字 | `100` | 任务队列最大长度 |
| `--enable-stream` | - | 关闭 | 实时转写端点 `WS /v2/asr/stream` |
| `--enable-task-store` | - | 关闭 | 离线任务持久化（结果跨重启可查） |
| `--enable-speaker` | - | 关闭 | 说话人分离（匿名 A/B/C… 标签） |
| `--enable-speaker-db` | - | 关闭 | 声纹库真名识别（需 `--enable-speaker` + API 密钥） |
| `--enable-openai-api` | - | 关闭 | OpenAI 兼容端点 `/compat/openai/v1/*`（实时 `WS /realtime` 需 `--enable-stream`） |
| `--enable-dashscope-api` | - | 关闭 | DashScope 兼容端点 `/compat/dashscope/*`（实时 `WS /inference` 需 `--enable-stream`） |

> 容器内部固定监听 `0.0.0.0`，通过 `-p` 映射端口即可从外部访问。
> 完整参数表与 YAML 配置文件用法见 [配置文档](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/configuration.md)。

### 数据卷

| 容器路径 | 说明 |
|---------|------|
| `/app/models` | 模型文件（首次启动自动下载，建议挂载持久化） |
| `/app/logs` | 服务日志 |
| `/app/data` | SQLite 数据库：任务持久化库 `tasks.db` 与声纹库 `speakers.db`——挂出以跨容器重建保留 |

### API 使用

完整接口文档（参数、响应结构、错误码、WebSocket 实时协议）见 GitHub 仓库：

- [API 文档 v2（默认版本）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2.md) —— 按功能拆分为子文档：
  - [基础接口与认证](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/basics.md)
  - [转写（离线批处理 + 实时推流）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/transcription.md)
  - [任务管理](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/tasks.md)
  - [说话人分离与声纹库](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v2/speakers.md)
- [API 文档 v1（兼容版本）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/v1.md)
- [兼容接口（OpenAI / DashScope drop-in，离线 + 实时）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/api/compat.md)
- [配置文档（启动参数 / config.yaml / 任务持久化）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/configuration.md)
- [vLLM 与 standard 差异（vLLM 原生流式模式的功能差异与选型参考）](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard.md)

快速验证：

```bash
# 健康检查
curl http://localhost:8765/v2/health

# 提交任务（启用认证时加 -H "Authorization: Bearer sk-your-key-here"）
curl -X POST http://localhost:8765/v2/asr -F "file=@audio.wav"

# 查询结果
curl http://localhost:8765/v2/tasks/{task_id}
```

### 运行模式对比

| | GPU 模式 | CPU 模式 (amd64) | CPU 模式 (arm64) |
|--|---------|---------|-----------|
| 镜像 Tag | `latest` | `latest-cpu` | `latest-cpu`（同一多架构 tag） |
| 推理引擎 | PyTorch (CUDA) | OpenVINO (INT8) | OpenVINO (FP32) |
| 对齐（字级时间戳） | 支持 | 不支持 | 不支持 |
| 显存/内存需求 | ~2-8GB 显存 | ~4-6GB 内存 | ~4-6GB 内存 |
| 模型来源 | ModelScope / HuggingFace | HuggingFace | HuggingFace |
| NVIDIA GPU | 需要 | 不需要 | 不需要 |

> `--device auto` 时根据显存自动选择：>=6GB 用 1.7B，4-6GB 用 0.6B，无 GPU 回退 CPU。
>
> 上表三列为 **standard** 镜像的运行变体。可选的 **vLLM** 镜像（`latest-vllm`）是独立的 GPU 原生流式模式，分段、说话人与实时行为不同——详见 [vLLM 与 standard 差异](https://github.com/LanceLRQ/qwen3-asr-service/blob/main/docs/vllm-vs-standard.md)。

### 源码

[GitHub: qwen3-asr-service](https://github.com/LanceLRQ/qwen3-asr-service)

如果这个项目对你有帮助，欢迎给 [GitHub 仓库](https://github.com/LanceLRQ/qwen3-asr-service) 和 [Docker Hub](https://hub.docker.com/r/lancelrq/qwen3-asr-service) 点个 ⭐，你的支持是项目持续更新的动力！
