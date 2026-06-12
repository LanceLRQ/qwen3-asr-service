# DashScope 兼容接口

[← 兼容接口概览](../compat.md) ｜ **中文** | [English](dashscope_EN.md)

为已接入 **阿里云 DashScope（Paraformer）** 语音生态的客户端提供 drop-in 垫片。DashScope Paraformer **录音文件识别**（异步）：提交 → 轮询 → 取二跳结果。客户端 `dashscope.base_http_api_url = "http://<host>:8765/compat/dashscope/api/v1"`。

> 离线接口需启动时 `--enable-dashscope-api`；实时识别另需 `--enable-stream`。认证与客户端指向见[兼容接口概览](../compat.md#2-认证)。

> ⚠️ DashScope 端点只接受 `file_urls`（URL 列表，服务端下载）。如需**本地文件上传**，请改用 OpenAI 端点 [`/audio/transcriptions`](openai.md#转写)（multipart 上传）或原生 [`POST /v2/asr`](../v2/transcription.md#提交-asr-任务)。

## 目录

- [提交（异步）](#提交异步)
- [轮询](#轮询)
- [二跳转写结果](#二跳转写结果)
- [实时识别 `WS /inference`](#实时识别)
- [错误码](#错误码)

---

## 提交（异步）

`POST /services/audio/asr/transcription`，头 `X-DashScope-Async: enable`（必填，缺失 → 400）。

```json
{ "model":"paraformer-v2",
  "input":{"file_urls":["https://example.com/a.wav"]},
  "parameters":{"language_hints":["zh"],"diarization_enabled":false} }
```

| 参数 | 支持 | 映射 |
|------|------|------|
| `input.file_urls[]` | ✅ | 服务端下载（SSRF 防护）；每 URL 一个子任务；单请求 ≤16 个 |
| `parameters.language_hints[0]` | ✅ | → 识别语言 |
| `parameters.diarization_enabled` | ✅ | → 说话人分离 |
| `parameters.speaker_count` | ❌ 忽略 | 说话人数上限为服务级配置（`--speaker-max`），不支持按请求覆盖 |
| `parameters.channel_id` | ❌ | 单声道，固定 0 |
| `disfluency_removal_enabled` / `special_word_filter` / `timestamp_alignment_enabled` | ❌ 忽略 | 无对应能力 |

> ⚠️ `file_urls` 必须本服务**可访问**；默认禁止私网/回环地址（SSRF 防护），可用 `--compat-fetch-allow-private` 放开（仅内网可信环境）。

响应：`{"output":{"task_status":"PENDING","task_id":"<id>"},"request_id":"<rid>"}`

## 轮询

`POST|GET /tasks/{task_id}`：

```json
{ "output":{"task_id":"<id>","task_status":"SUCCEEDED",
    "results":[{"file_url":"https://…/a.wav",
                "transcription_url":".../tasks/<id>/transcription/0",
                "subtask_status":"SUCCEEDED"}],
    "task_metrics":{"TOTAL":1,"SUCCEEDED":1,"FAILED":0}} }
```
`task_status`：`PENDING`/`RUNNING`/`SUCCEEDED`/`FAILED`（多 file_urls 时聚合）。结果在 `transcription_url`（二跳）。

> 反代/容器部署时用 `--compat-external-base-url` 指定 `transcription_url` 外部基址；未配置时按 `X-Forwarded-Proto/Host` 或请求地址推导。
> 任务注册表仅内存保存（带 TTL）：服务重启后未取结果的 task_id 将查询不到（404），需重新提交。

## 二跳转写结果

`GET /tasks/{task_id}/transcription/{idx}`，时间单位**毫秒**：

```json
{ "file_url":"https://…/a.wav",
  "transcripts":[{"channel_id":0,"content_duration_in_milliseconds":8470,"text":"识别全文",
    "sentences":[{"begin_time":0,"end_time":3200,"text":"…","sentence_id":1,"speaker_id":0,
                  "words":[{"begin_time":0,"end_time":200,"text":"你","punctuation":""}]}]}] }
```

**示例**：
```python
import dashscope
dashscope.base_http_api_url = "http://localhost:8765/compat/dashscope/api/v1"
dashscope.api_key = "sk-xxx"
from dashscope.audio.asr import Transcription
task = Transcription.async_call(model="paraformer-v2",
        file_urls=["https://example.com/a.wav"], language_hints=["zh"])
result = Transcription.wait(task=task.output.task_id)   # SDK 内部轮询 + 取 transcription_url
print(result.output)
```

## 实时识别

```
WS /inference
```

Paraformer realtime（standard 模式需 `--enable-stream`；vLLM 模式 `--serve-mode vllm` 下随 `--enable-dashscope-api` 自动挂载，无需 `--enable-stream`）。ws base = `ws://<host>:8765/compat/dashscope/api-ws/v1`。`header/payload` 信封：

1. 客户端 → `run-task`：`{"header":{"action":"run-task","task_id":"<uuid>","streaming":"duplex"},"payload":{"parameters":{"format":"pcm","sample_rate":16000,"language_hints":["zh"]}}}`
2. 服务端 → `task-started`
3. 客户端 → 二进制 PCM 帧（~100ms/帧）
4. 服务端 → 每句 `result-generated`：`payload.output.sentence` 含 `begin_time`/`end_time`(ms)/`text`/`sentence_end:true`/`words[]`
5. 客户端 → `finish-task` → 服务端 → `task-finished`
6. 同一连接可再发 `run-task` 起新任务（连接复用）

> **能力与限制**：取决于运行模式——**standard**（VAD-offline）只产整句 `result-generated`（`sentence_end:true`），不产中间结果（`sentence_end:false`）；**vLLM**（`--serve-mode vllm`，原生流式）在句内逐步下发中间 `result-generated`（`sentence_end:false`，累计文本，`begin_time`/`end_time`=null、不带 `words`），句末再发 `sentence_end:true` 整句。DashScope 中间结果语义本就累计，与 vLLM partial 天然契合、干净直发（非 best-effort）。

---

## 错误码

DashScope 风格 `{"code","message","request_id"}`：

| HTTP | code | 场景 |
|------|------|------|
| 400 | `InvalidParameter` | 缺 X-DashScope-Async、file_urls 为空/超 16 个 |
| 401 | `InvalidApiKey` | 鉴权失败 |
| 404 | `UNKNOWN_TASK` | task_id 不存在/已过期/服务重启后丢失 |
| 子任务 | `FAILED` + code | 下载失败/SSRF 拒绝/转写失败/队列繁忙（`Throttling`）（在 `results[].subtask_status`）|

> 需本地文件上传/HTTP 流式见 [OpenAI 兼容接口](openai.md)；与原生 v2 的取舍见[兼容接口概览](../compat.md#与原生-v2-的取舍)。
