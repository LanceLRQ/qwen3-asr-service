# OpenAI 兼容接口

[← 兼容接口概览](../compat.md) ｜ **中文** | [English](openai_EN.md)

为已接入 **OpenAI** 语音生态的客户端提供 drop-in 垫片：把 SDK 的 base url 指向本服务的 `/compat/openai/v1` 前缀即可对接，无需改动业务代码。客户端 base_url = `http://<host>:8765/compat/openai/v1`。

> 离线接口需启动时 `--enable-openai-api`；实时转写另需 `--enable-stream`。认证与客户端指向见[兼容接口概览](../compat.md#2-认证)。

## 目录

- [转写 `POST /audio/transcriptions`](#转写)
- [流式转写 `stream=true`（SSE）](#流式转写)
- [翻译 `POST /audio/translations`](#翻译)
- [模型清单 `GET /models`](#模型清单)
- [实时转写 `WS /realtime`](#实时转写)
- [错误码](#错误码)

---

## 转写

```
POST /audio/transcriptions
```

同步转写：上传音频，**响应即返回**转写结果（内部走队列等待，超时上限 `--openai-sync-timeout`，默认 300s）。

**请求**（`multipart/form-data`）：

| 字段 | 必填 | 支持 | 说明 |
|------|------|------|------|
| `file` | 是 | ✅ | 音频文件，扩展名白名单同 v2 |
| `model` | 是 | 宽容 | 任意值；以服务实际加载模型为准（见 `GET /models`）|
| `language` | 否 | ✅ | ISO-639-1，如 `zh`/`en` |
| `response_format` | 否 | ✅ | `json`(默认)/`text`/`srt`/`verbose_json`/`vtt` |
| `timestamp_granularities[]` | 否 | ✅ | `word`/`segment`（需 `verbose_json`）；含 `word` 才返回词级时间戳 |
| `stream` | 否 | ✅ | `true` 走 SSE（见 [流式转写](#流式转写)）|
| `prompt` | 否 | ❌ 忽略 | 本服务不支持引导文本 |
| `temperature` | 否 | ❌ 忽略 | 本服务不支持采样温度 |

**响应**：

- `json`：`{"text": "识别全文"}`
- `text`：纯文本（`text/plain`）
- `verbose_json`：含 `segments[]`（`start`/`end`/`text` 为真值）与可选 `words[]`
  > ⚠️ `tokens`/`avg_logprob`/`compression_ratio`/`no_speech_prob`/`seek` 为**占位值**（本服务无对应数据），勿当真实置信度使用。
- `srt`/`vtt`：标准字幕文本（`text/plain`）

**示例**：
```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8765/compat/openai/v1", api_key="sk-xxx")
r = client.audio.transcriptions.create(
    model="whisper-1", file=open("a.wav", "rb"),
    response_format="verbose_json", timestamp_granularities=["word", "segment"])
print(r.text)
```
```bash
curl http://localhost:8765/compat/openai/v1/audio/transcriptions \
  -H "Authorization: Bearer sk-xxx" \
  -F file=@a.wav -F model=whisper-1 -F response_format=srt
```

## 流式转写

`POST /audio/transcriptions` 带 `stream=true` 走 Server-Sent Events，推 `transcript.text.delta` / `transcript.text.done`：

```
data: {"type": "transcript.text.delta", "delta": "你好"}

data: {"type": "transcript.text.delta", "delta": "世界"}

data: {"type": "transcript.text.done", "text": "你好世界"}
```

> 说明：本服务为整段离线解码——**先完整转写再分句吐字**，故首个 delta 的延迟≈整段转写时长（非边解码边吐 token）。`delta` 文本是最终结果的分块（无时间戳），`stream=true` 时 `response_format` 不适用。

## 翻译

```
POST /audio/translations
```

**不支持**。本服务为纯语音识别，无翻译能力。固定返回 `501`：
```json
{"error":{"message":"This service performs speech recognition only; translation is not supported.","type":"invalid_request_error","param":null,"code":"unsupported"}}
```

## 模型清单

```
GET /models
```

```json
{"object":"list","data":[{"id":"qwen3-asr-0.6b","object":"model","created":0,"owned_by":"qwen3-asr"}]}
```
`id` 反映服务实际加载的模型大小；请求侧 `model` 宽容接受任意值。

## 实时转写

```
WS /realtime
```

OpenAI Realtime transcription 会话（需 `--enable-stream`）。ws base = `ws://<host>:8765/compat/openai/v1`。

1. 服务端 → `session.created`（连接即下发）
2. 客户端 → `session.update`：配置语言/采样率（兼容 GA `session.audio.input` 与 beta 字段路径）
3. 客户端 → `input_audio_buffer.append`：`{"type":"...append","audio":"<base64 PCM16>"}`（持续）
4. 客户端 → `input_audio_buffer.commit`（或关闭连接）触发末句冲刷
5. 服务端 → 每句 `conversation.item.input_audio_transcription.completed`：`{"item_id":"item_0","transcript":"识别整句"}`

> **能力与限制**：当前实时后端为 VAD-offline，**只产整句 `…completed`，不产逐字 `…delta`**（`partial_results=false`）。逐字增量需后续 vLLM 流式后端。

---

## 错误码

OpenAI 风格 `{"error":{"message","type","param","code"}}`：

| HTTP | code | 场景 |
|------|------|------|
| 400 | `invalid_value` | response_format 非法等 |
| 401 | `invalid_api_key` | 鉴权失败 |
| 501 | `unsupported` | translations |
| 503 | `overloaded` | 任务队列已满 |
| 504 | `timeout` | 同步等待超 `--openai-sync-timeout` |
| 500 | `internal_error` | 转写失败 |

> 需对接 DashScope 生态见 [DashScope 兼容接口](dashscope.md)；与原生 v2 的取舍见[兼容接口概览](../compat.md#与原生-v2-的取舍)。
