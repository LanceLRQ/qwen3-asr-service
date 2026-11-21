# 兼容接口（OpenAI / 阿里云 DashScope）

[← API v2 概览](v2.md) ｜ **中文** | [English](compat_EN.md)

为已接入 **OpenAI** 或 **阿里云 DashScope（Paraformer）** 语音生态的客户端提供 drop-in 兼容垫片：改写 SDK 的 base url 指向本服务的 `/compat/...` 前缀即可对接，无需改动业务代码。兼容层是适配垫片，复用现有转写管线与任务队列，与原生 `/v1`、`/v2` 完全隔离。

> 兼容接口默认**关闭**，需启动时显式开启：
> ```bash
> # standard 模式：离线 + 实时（实时需同时 --enable-stream）
> python -m app.main --enable-openai-api --enable-dashscope-api --enable-stream --api-key sk-xxx
> # vLLM 模式（--serve-mode vllm）：流式恒开，实时兼容随开关自动挂载，无需 --enable-stream
> python -m app.main --serve-mode vllm --enable-openai-api --enable-dashscope-api --api-key sk-xxx
> ```

**设计原则**：诚实降级——上游协议里本服务不具备的能力（翻译、温度、引导词、热词、逐字增量等）一律**显式忽略并告警或报错，绝不静默伪造**。

## 文档导航

兼容接口按上游生态拆分为两个子文档：

| 子文档 | 内容 |
|--------|------|
| [OpenAI 兼容接口](compat/openai.md) | 转写 `POST /audio/transcriptions`、SSE 流式、翻译（501）、模型清单、实时 `WS /realtime`、OpenAI 错误码 |
| [DashScope 兼容接口](compat/dashscope.md) | 录音文件识别（提交 / 轮询 / 二跳结果）、实时 `WS /inference`、DashScope 错误码 |

本页提供跨生态共性内容：客户端指向、认证、能力速查、与原生 v2 的取舍。

## 1. 客户端如何指向本服务

两套上游 SDK 都支持改写 base url——指到本服务的 `/compat/...` 前缀即可：

| 上游 | 配置项 | 指向 |
|------|--------|------|
| OpenAI Python SDK | `OpenAI(base_url=...)` | `http://<host>:8765/compat/openai/v1` |
| OpenAI 实时 | ws base | `ws://<host>:8765/compat/openai/v1` |
| DashScope SDK | `dashscope.base_http_api_url` | `http://<host>:8765/compat/dashscope/api/v1` |
| DashScope 实时（Paraformer） | `dashscope.base_websocket_api_url` | `ws://<host>:8765/compat/dashscope/api-ws/v1` |
| DashScope 实时（Qwen3-ASR-Flash-Realtime） | WebSocket URL | `ws://<host>:8765/compat/dashscope/api-ws/v1/realtime?model=qwen3-asr-flash-realtime` |

前缀之后的上游子路径逐段原样保留（SDK 硬编码），故兼容路径与 `/v1`、`/v2` 零碰撞。

### DashScope Qwen3-ASR-Flash-Realtime WebSocket

- **端点**：`WS /compat/dashscope/api-ws/v1/realtime`，查询参数 `model` 缺省为 `qwen3-asr-flash-realtime`；仅接受 Qwen ASR 系列模型名。
- **鉴权**：复用兼容层鉴权；配置 `--api-key` 时用 `Authorization: Bearer <api-key>` 或 `?token=<api-key>`。
- **上游协议来源**：适配 DashScope 官方 `wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=qwen3-asr-flash-realtime` 的 Qwen3-ASR-Flash-Realtime 事件协议；官方 URL 与模型名用于协议对照。实际用途是让 Qwen realtime 协议客户端连接本服务，由本机流式 ASR 引擎转写；不会代理到 DashScope 云端，也不消耗 DashScope 额度或需要其 API key。
- **与 Paraformer `/inference` 的关系**：`/inference` 使用 `run-task`/`finish-task` 信封，`/realtime` 使用 Qwen realtime 事件信封；两者各自创建会话，但共用本服务注入的实时后端、鉴权与容量控制。两个实时路由都受 `--enable-dashscope-api` 控制（standard 模式还需 `--enable-stream`；vLLM 模式流式常开）。
- **与 OpenAI Realtime 的差异**：此端点采用 DashScope Qwen 字段：`session.sample_rate`、`session.finish`、`session.finished`，中间识别文本为 `conversation.item.input_audio_transcription.text` 的 `text`/`stash`；OpenAI 端点采用 OpenAI GA transcription 字段和会话生命周期。
- **事件序列**：连接后 `session.created`；客户端发 `session.update` 后收到 `session.updated`；音频通过 `input_audio_buffer.append` 的 base64 PCM16 字段发送；识别过程可产生 `input_audio_buffer.speech_started`、`conversation.item.input_audio_transcription.text`（vLLM partial）；句末产生 `speech_stopped`、`input_audio_buffer.committed`、`conversation.item.created`、`conversation.item.input_audio_transcription.completed`；`session.finish` 后返回 `session.finished`。错误以 `error` 事件报告，非法模型用 1008 关闭。空缓冲 commit 使用 `input_audio_buffer_commit_empty`（沿用 OpenAI Realtime 错误码；Qwen 文档仅规定空缓冲产生错误，未指定独立错误码）；非法或超长 base64 音频帧分别返回 `invalid_value` 或 `frame_too_large`。

## 2. 认证

服务配置 `--api-key` 时，所有兼容端点要求 `Authorization: Bearer <api-key>`（与两套 SDK 默认携带方式一致）。实时 WS 也接受 query 参数 `?token=<api-key>`。未配置 api-key 时放行（不建议生产环境）。

## 能力与限制速查

| 能力 | OpenAI 兼容 | DashScope 兼容 |
|------|------------|---------------|
| 离线转写 | ✅ transcriptions | ✅ 录音文件识别 |
| 本地文件上传 | ✅ multipart | ❌ 仅 file_urls（URL） |
| 词级时间戳 | ✅ verbose_json + word | ✅ words[] |
| 说话人分离 | ➖（OpenAI 无对应字段）| ✅ diarization_enabled |
| 翻译 | ❌ 501 | ➖ 不涉及 |
| HTTP 流式 | ✅ stream=true（SSE） | ➖ 不涉及 |
| 实时整句 | ✅ completed | ✅ result-generated |
| 实时逐字增量 | ✅ delta（仅 vLLM 模式，best-effort）| ✅ 中间结果（仅 vLLM 模式）|
| 置信度/logprob | ➖ 占位 | ➖ 不提供 |
| 引导/温度/热词/顺滑 | ❌ 忽略 | ❌ 忽略 |

> 实时（上表最后两行）的挂载与增量能力因模式而异：**standard**（VAD-offline）需 `--enable-stream`，仅产整句 final、无逐字增量；**vLLM**（`--serve-mode vllm`，原生流式）流式恒开、实时随兼容开关自动挂载（**无需 `--enable-stream`**），并产逐字增量——DashScope 中间 `result-generated`（`sentence_end:false`）天然累计、干净直发；OpenAI `…delta` 为 **best-effort**（partial 累计且可修订，仅纯追加时取新增后缀作 delta、修订帧跳过，权威全文仍以 `…completed` 为准）。

## 实时转写生命周期事件（OpenAI Realtime）

`WS /compat/openai/v1/realtime?intent=transcription` 按 OpenAI Realtime 协议下发完整生命周期；同一句的 `item_id` 在 `speech_started` → `speech_stopped` → `committed` → `item.created` → `completed` 之间保持一致，下一句自动换新 id。

服务端 VAD（`turn_detection: {"type": "server_vad"}`）：

```text
input_audio_buffer.speech_started                        # item_id=item_0
input_audio_buffer.speech_stopped                        # item_id=item_0
input_audio_buffer.committed                             # item_id=item_0
conversation.item.created                                # item.id=item_0
conversation.item.input_audio_transcription.completed    # item_id=item_0
```

手动 commit（客户端发送 `input_audio_buffer.commit`，通常配 `turn_detection: null`）：

```text
input_audio_buffer.committed                             # 此前已接收音频处理完成后回执 item_id=item_0
conversation.item.created                                # item.id=item_0
conversation.item.input_audio_transcription.completed    # item_id=item_0
```

缓冲区为空时发送 `input_audio_buffer.commit` 会收到 `error`，错误码为 `input_audio_buffer_commit_empty`，不会创建 item。

- 迟到或重复的 VAD end 不会重复发送 `committed`，也不会在 `committed` 之后补发 `speech_stopped`。
- `session.update` 同时接受 GA（`session.audio.input.turn_detection`）与 beta（`session.turn_detection`），并在 `session.updated` 中回显 `turn_detection`。
- `turn_detection: null` 时按协议不再下发 `speech_started` / `speech_stopped`；后端仍使用 VAD 完成转写断句，手动 `commit` 的 `committed` / `completed` 语义不变。
- 后端 VAD 帧由兼容层按需开启（`emit_vad_events`），原生 `/v2/asr/stream` 默认关闭，帧序列与行为不变。

## 与原生 v2 的取舍

- 需要**对接已有 OpenAI/DashScope 生态**（SDK、现成客户端）→ 用兼容接口。
- 需要本服务**全部能力**（声纹库、任务列表/取消、统一实时信封、按请求覆盖参数）→ 用原生 [API v2](v2.md)。
- 超长音频：OpenAI 同步端点受 `--openai-sync-timeout` 限制，超长建议走 DashScope 异步兼容或原生 v2 异步。
