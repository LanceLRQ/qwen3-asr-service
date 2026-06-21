# 转写（v2）

[← API v2 概览](../v2.md) ｜ **中文** | [English](transcription_EN.md)

两种转写方式：**离线批处理**（上传整段音频、异步出结果）与**实时转写**（WebSocket 流式、逐句返回）。

## 目录

- [离线批处理 · 提交 ASR 任务 `POST /v2/asr`](#提交-asr-任务)
  - [语言代码取值与归一化](#语言代码取值与归一化)
- [音频标注 `POST /v2/audio/tag`](#音频标注)
- [实时转写 `WS /v2/asr/stream`](#实时转写)
  - [鉴权](#鉴权)
  - [消息流程](#消息流程)
  - [客户端 → 服务端](#客户端--服务端)
  - [服务端 → 客户端](#服务端--客户端)
  - [错误码](#错误码)
  - [WebSocket 关闭码](#websocket-关闭码)

---

## 提交 ASR 任务

```
POST /v2/asr
Content-Type: multipart/form-data
```

```bash
curl -X POST http://127.0.0.1:8765/v2/asr \
  -F "file=@/path/to/audio.mp3" \
  -F "language=zh"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| file | 文件 | 必填 | 音频文件，支持 WAV/MP3/FLAC/M4A/AAC/OGG/WMA/AMR/OPUS |
| language | string | null | 识别语言提示，`null`/省略=自动检测；取值与归一化规则见[下方说明](#语言代码取值与归一化) |
| identify_speakers | bool | false | 对分离出的说话人做声纹识别（需说话人分离与[声纹库](speakers.md#说话人分离与声纹识别)均已启用） |
| with_punc | bool | 服务端默认 | 是否做标点恢复（降级开关，只能关；服务端未加载标点模型则本就无标点） |
| with_words | bool | 服务端默认 | 是否输出词级时间戳（需对齐模型已加载） |
| diarize | bool | 服务端默认 | 是否做说话人分离（关闭可省算力；需说话人引擎已加载） |
| max_segment | int | 服务端默认 | VAD 切片合并最大时长（秒），范围 `[1, 30]` |
| speaker_id_threshold | float | 服务端默认 | 声纹 1:N 识别阈，范围 `[0, 1]`（需声纹库已启用） |
| speaker_id_margin | float | 服务端默认 | 声纹 top1-top2 margin，范围 `[0, 1]`（需声纹库已启用） |

> 数值越界 → 400；功能未启用的覆盖项不报错，转写结果的 `result.warnings`（字符串数组）列出被忽略项。

#### 语言代码取值与归一化

`language` 接受三种写法，服务端在送入引擎前统一归一为引擎语种名：

- **ISO-639-1 码**：`zh` / `en` / `yue` / `ja` …
- **规范英文名**（大小写不敏感）：`Chinese` / `English` / …
- **带地区子标签**：`zh-CN` / `en_US`（按主标签解析）

**无法识别的取值**（拼写错误、未支持语种、`Zh` 这类大小写变体）一律**降级为自动检测，不再报错**——既往直接透传导致引擎抛 `Unsupported language` 的行为已在服务层拦截。离线与[实时转写](#实时转写)共用同一归一化规则。

支持语种（30）：`Chinese`、`English`、`Cantonese`、`Arabic`、`German`、`French`、`Spanish`、`Portuguese`、`Indonesian`、`Italian`、`Korean`、`Russian`、`Thai`、`Vietnamese`、`Japanese`、`Turkish`、`Hindi`、`Malay`、`Dutch`、`Swedish`、`Danish`、`Finnish`、`Polish`、`Czech`、`Filipino`、`Persian`、`Greek`、`Romanian`、`Hungarian`、`Macedonian`。

响应：

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

提交成功仅返回 `task_id`，**识别结果通过任务管理接口轮询获取**——查询详情、结果结构（`segments` / `words` / 说话人增量字段）见[任务管理 · 查询任务详情](tasks.md#查询任务详情)。

**限制**：文件最大 1GB，音频时长 1s ~ 4 小时。

| 状态码 | 含义 |
|--------|------|
| 200 | 提交成功，返回 `task_id` |
| 400 | 不支持的音频格式 |
| 401 | 认证失败 |
| 413 | 文件过大（>1GB） |
| 503 | 服务未就绪 / 任务队列已满 |

## 音频标注

```
POST /v2/audio/tag
Content-Type: multipart/form-data
```

**前置条件**：服务端启用音频标注（`--enable-audio-tagging`）。未启用时返回 `503`；可先 [`GET /v2/capabilities`](basics.md#能力查询) 预检 `audio_tagging`。

仅做音频事件标注与场景识别，**不做转写**，同步返回结果（非任务异步）。

```bash
curl -X POST http://127.0.0.1:8765/v2/audio/tag \
  -F "file=@/path/to/audio.mp3"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| file | 文件 | 必填 | 音频文件，格式同 `/v2/asr`（WAV/MP3/FLAC/M4A/AAC/OGG/WMA/AMR/OPUS） |
| with_scene | bool | true | 是否输出场景时间线 `scene_timeline`；`false` 时该字段省略 / 为 `null` |

> 配置了 API 密钥时同其他接口要求携带 Bearer Token（未配置密钥时免认证）。

响应：

```json
{
  "audio_events": [{"label": "Speech", "start_ms": 0, "end_ms": 2000, "confidence": 0.9}],
  "scene_timeline": [{"label": "speech", "start_ms": 0, "end_ms": 2000}]
}
```

| 字段 | 说明 |
|------|------|
| `audio_events[]` | 按起止时间聚合的事件段：`label`（AudioSet 类别）、`start_ms` / `end_ms`（毫秒）、`confidence`（段内最大概率） |
| `scene_timeline[]` | 连续场景段的游程合并列表：`label`（`silence`/`speech`/`singing`/`music`/`other` 或自定义桶）、`start_ms` / `end_ms`；`with_scene=false` 时省略 / 为 `null` |

| 状态码 | 含义 |
|--------|------|
| 200 | 标注成功 |
| 400 | 不支持的音频格式 |
| 401 | 认证失败 |
| 413 | 文件过大（>1GB） |
| 503 | 服务未就绪 / 未启用音频标注 |

## 实时转写

```
WS /v2/asr/stream
```

**前置条件**：`standard` 模式 + 启用实时（`--enable-stream` 或配置 `enable_stream: true`）。未启用时端点不存在；可先 [`GET /v2/capabilities`](basics.md#能力查询) 预检 `stream.enabled`。

> 浏览器测试页：启动加 `--web` 后访问 `/web-ui/stream`（支持麦克风与音频文件模拟推流）。

### 鉴权

配置了 API 密钥时，连接需携带其一（失败以关闭码 `1008` 拒绝）：

- Query 参数：`ws://host:port/v2/asr/stream?token=sk-your-key`
- 请求头：`Authorization: Bearer sk-your-key`（浏览器 WebSocket API 不支持自定义头，建议用 query）

### 消息流程

```
客户端                                服务端
  │ ──── WebSocket 连接 ────────────────▶ │
  │ ◀─── {"type":"session.created",...} ─ │   连接即声明协议/后端/能力
  │ ──── {"type":"start",...} ──────────▶ │   会话配置
  │ ──── 二进制音频帧 × N ───────────────▶ │   PCM16 小端、单声道
  │ ◀─── {"type":"final",...}（逐句） ──── │   VAD 断句后逐段返回
  │ ──── {"type":"stop"} ───────────────▶ │   结束推流
  │ ◀─── {"type":"final",...}（末句冲刷）─ │
  │ ◀─── {"type":"session.closed",...} ── │
  │ ◀──── WebSocket 正常关闭 ──────────── │
```

### 客户端 → 服务端

**`start`（首条消息，JSON 文本帧）**：

```json
{"type": "start", "audio_fs": 16000, "language": null, "wav_name": "stream"}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| audio_fs | 16000 | 音频采样率，允许 8000–96000，非 16k 时服务端自动重采样 |
| language | null | 识别语言提示，`null`/省略=自动检测；取值与归一化规则同[离线提交](#语言代码取值与归一化)（非法/未识别码降级为自动检测，不报错） |
| wav_name | "stream" | 会话名（展示用） |
| identify_speakers | false | 对说话人标签做声纹识别（需 `session.created.capabilities.speaker_identification=true`） |
| noise_filter | 服务端默认 | 本会话覆盖远场段级过滤开关（缺省沿用服务端配置；需 `capabilities.noise_filter_tunable=true`） |
| energy_floor_dbfs | 服务端默认 | 本会话覆盖绝对能量门（dBFS），范围 `[-90, 0]`，越界回 `invalid_config` |
| snr_min_db | 服务端默认 | 本会话覆盖自适应信噪比门（dB），范围 `[0, 40]`，`0`=关闭该门 |
| speaker_threshold | 服务端默认 | 在线归簇余弦阈值，范围 `[0.2, 0.9]`（需 `capabilities.speaker_labels=true`） |
| speaker_min_seg_ms | 服务端默认 | 短段门槛（毫秒），范围 `[0, 10000]` |
| speaker_max | 服务端默认 | 说话人数上限，范围 `[1, 50]` |
| speaker_id_threshold | 服务端默认 | 声纹识别阈，范围 `[0, 1]`（需 `capabilities.speaker_identification=true`） |
| speaker_id_margin | 服务端默认 | 声纹 top1-top2 margin，范围 `[0, 1]` |
| max_end_silence_ms | 服务端默认 | 断句尾静音（毫秒），范围 `[200, 2000]`：调小出字更快、易切碎；调大不打断、出字慢 |
| max_segment_sec | 服务端默认 | 长句兜底切分（秒），范围 `[1, 60]` |
| with_punc / with_words / diarize | 服务端默认 | 降级开关：可关闭标点 / 词级时间戳 / 说话人分离（只能关，不能开启未加载的模型） |

> **范围钳制与软提示**：以上覆盖仅影响本会话；数值越界 / 类型错误 → `invalid_config`（致命）。
> 参数合法但对应功能未启用（如 `diarize:true` 但服务端未加载说话人引擎）→ 不报错，
> 服务端在 `start` 后补发一条非致命 `error`（`code="params_ignored"`, `fatal=false`），`message` 列出被忽略项。
> VAD 灵敏度 `vad_speech_noise_thres` 受 FunASR 限制为服务端全局配置，不支持按会话调整。

**音频帧（二进制帧）**：PCM16 小端、单声道、采样率与 `audio_fs` 一致。单帧上限 2MB（超限拒帧不断连）。

**`stop`（JSON 文本帧）**：`{"type": "stop"}` —— 冲刷末句后服务端回 `session.closed` 并正常关闭。

### 服务端 → 客户端

服务端下发的消息均为统一信封，均带 `type`：

| type | 字段 | 说明 |
|------|------|------|
| `session.created` | `protocol`("qwen3-asr-stream") / `protocol_version`("1.0") / `mode` / `backend` / `sample_rate` / `capabilities` / `limits` | 连接建立即下发；`capabilities` 含 `partial_results` / `word_timestamps` / `languages_auto` / `speaker_labels` / `speaker_identification` / `scene`（实时场景通知是否下发），以及可调声明 `noise_filter_tunable` / `speaker_tunable` / `endpoint_tunable` / `output_toggles`（标示对应覆盖项本会话是否可调）；`limits` 含 `max_frame_bytes` / `max_backlog_bytes`，超实时推流的客户端应据此控速（参考 `final.end` 反馈的处理进度，保持未处理积压低于上限） |
| `partial` | `seg_id` / `text` | 中间结果（仅 `partial_results=true` 的后端，vad-offline 不产生） |
| `final` | `seg_id` / `text` / `start` / `end` / `words` / `speaker` / `speaker_name` | 句级定稿结果；`start`/`end` 为毫秒；`words` 仅 `word_timestamps=true` 时存在；`speaker`（匿名标签 A/B/C…）仅 `speaker_labels=true` 且本段可判定时存在；`speaker_name` 仅 `identify_speakers=true` 且声纹命中时存在（说话人标签 / 真名语义见[说话人管理](speakers.md#说话人分离与声纹识别)） |
| `scene` | `label` / `confidence` / `since` / `scores` | 场景状态切换通知（仅 `capabilities.stream.scene=true` 时下发）：`label` 当前场景；`since` 该场景状态的起始时间戳（毫秒）；`scores` 各内容桶的代表性得分。仅在状态**发生变化**时推送（带迟滞平滑），不逐窗下发 |
| `error` | `code` / `message` / `seg_id` / `fatal` | `fatal=true` 后会话终止 |
| `session.closed` | `reason` | 会话结束 |

`final` 示例：

```json
{"type": "final", "seg_id": 0, "text": "甚至出现交易几乎停滞的情况。", "start": 320, "end": 3520, "words": null}
```

`scene` 示例：

```json
{"type": "scene", "label": "speech", "confidence": 0.86, "since": 1000, "scores": {"speech": 0.86, "singing": 0.0, "music": 0.04}}
```

### 错误码

统一信封 `error` 的 `code` 取值：

| code | fatal | 说明 |
|------|-------|------|
| `invalid_config` | 是 | `start` 消息校验失败（如 `audio_fs` 越界） |
| `frame_too_large` | 否 | 单帧超过 2MB，该帧被丢弃 |
| `backlog_overflow` | 是 | 处理积压超过 8MB（约 4 分钟音频），会话断开 |
| `feed_failed` | 否 | 某段音频处理失败，跳过该段继续 |
| `session_timeout` | 是 | 会话超过最长时长（默认 1 小时） |
| `internal` | 是 | 内部错误 |

### WebSocket 关闭码

| 关闭码 | 说明 |
|--------|------|
| 1000 | 正常结束（stop 流程完成） |
| 1008 | 鉴权失败 |
| 1011 | 服务未就绪 / 致命内部错误 |
| 1013 | 并发会话数超限（默认 16，可调 `max_stream_sessions`） |
