# 基础接口（v2）

[← API v2 概览](../v2.md) ｜ **中文** | [English](basics_EN.md)

服务探活、能力声明与认证约定。所有接口以 `/v2` 为前缀，默认地址 `http://127.0.0.1:8765`。

## 目录

- [认证](#认证)
- [服务入口 `GET /`](#服务入口)
- [服务状态](#服务状态)
  - [健康检查 `GET /v2/health`](#健康检查)
  - [能力查询 `GET /v2/capabilities`](#能力查询)

---

## 认证

服务配置了 API 密钥时（启动参数 `--api-key` / 配置文件 `api_key` / 环境变量 `ASR_API_KEY`，详见[配置文档](../../configuration.md)），**离线批处理接口**要求携带 Bearer Token，否则返回 `401`：

```bash
curl -H "Authorization: Bearer sk-your-key-here" http://127.0.0.1:8765/v2/tasks
```

- `GET /health`、`GET /capabilities` 不要求认证（探活用）。
- 实时转写 WebSocket 的鉴权方式见[转写 · 鉴权](transcription.md#鉴权)。
- 说话人管理 `/v2/speakers*` **全部端点强制 Bearer 认证**（服务端未配置 `api_key` 时声纹库整体不可用），见[说话人管理](speakers.md#声纹库接口)。
- 未配置密钥时所有接口免认证。

## 服务入口

```
GET /
```

启用 Web UI（`--web`）时 `307` 重定向到 `/web-ui`；否则返回服务索引 JSON（指向 `health` / `capabilities`），不会空白或 404。

```json
{
  "service": "Qwen3-ASR Service",
  "version": "2.0.0",
  "mode": "standard",
  "health": "/v2/health",
  "capabilities": "/v2/capabilities",
  "web_ui": "未启用，启动加 --web 开启 / disabled, start with --web"
}
```

## 服务状态

### 健康检查

```
GET /v2/health
```

```json
{
  "status": "ready",
  "mode": "standard",
  "device": "cuda",
  "model_size": "0.6b",
  "align_enabled": true,
  "punc_enabled": false,
  "speaker_enabled": false,
  "speaker_db_enabled": false,
  "asr_backend": "qwen_asr",
  "vad_backend": "pytorch",
  "punc_backend": "pytorch",
  "config_file": "config.yaml",
  "capabilities": {
    "mode": "standard",
    "offline_api": true,
    "speaker_labels": false,
    "speaker_identification": false,
    "stream": {
      "enabled": true,
      "backend": "vad-offline",
      "path": "/v2/asr/stream",
      "partial_results": false,
      "word_timestamps": true,
      "speaker_labels": false
    }
  }
}
```

| 字段 | 说明 |
|------|------|
| status | 服务状态，`ready` 表示就绪（未就绪时返回 503） |
| mode | 运行模式：`standard` / `vllm` |
| device | 运行设备：`cuda` / `cpu` |
| model_size | ASR 模型大小：`0.6b` / `1.7b` |
| align_enabled | 是否启用对齐模型（单词级时间戳） |
| punc_enabled | 是否启用标点恢复 |
| speaker_enabled | 是否启用说话人分离（`enable_speaker`） |
| speaker_db_enabled | 声纹库是否可用（启用且 model_tag 一致） |
| asr_backend | ASR 后端：`qwen_asr` / `openvino` |
| vad_backend | VAD 后端：`pytorch` / `onnx` |
| punc_backend | 标点后端：`pytorch` / `onnx` / `disabled` |
| config_file | 本次生效的配置文件名（`null` = 未加载配置文件） |
| capabilities | 服务能力摘要，与 `GET /capabilities` 一致 |

> vllm 模式（占位，暂未实现）下不适用的字段为 `null`。

### 能力查询

```
GET /v2/capabilities
```

返回当前运行模式与能力声明（客户端可据此判断是否可用实时转写）：

```json
{
  "mode": "standard",
  "offline_api": true,
  "speaker_labels": true,
  "speaker_identification": false,
  "stream": {
    "enabled": true,
    "backend": "vad-offline",
    "path": "/v2/asr/stream",
    "partial_results": false,
    "word_timestamps": true,
    "speaker_labels": true,
    "save_audio": false,
    "recording_retention_hours": 72,
    "recording_download_path": null
  },
  "defaults": {
    "max_segment": 5, "max_end_silence_ms": 800, "max_segment_sec": 12,
    "speaker_threshold": 0.5, "speaker_id_threshold": 0.45, "speaker_id_margin": 0.1,
    "energy_floor_dbfs": -50.0, "snr_min_db": 6.0
  }
}
```

| 字段 | 说明 |
|------|------|
| speaker_labels | 说话人分离是否启用（离线 + 实时同一开关） |
| speaker_identification | 声纹库真名识别是否可用（登记 / identify / 转写联动） |
| stream.enabled | 实时端点是否已挂载（需 `--enable-stream`） |
| stream.backend | `vad-offline` / `vllm-native`（暂未实现） |
| stream.partial_results | 是否产生中间结果 `partial`（vad-offline 后端为 false） |
| stream.word_timestamps | `final` 是否带单词级时间戳（随对齐开关） |
| stream.speaker_labels | 实时 `final` 是否带说话人标签 |
| stream.save_audio | 是否保存实时输入音频为 WAV（默认 false） |
| stream.recording_retention_hours | 实时录音保留小时数，默认 72；0 表示不自动清理 |
| stream.recording_download_path | 开启保存时返回下载 / 删除路径模板；未开启时为 `null` |
| defaults | 可覆盖参数（实时 `start` 字段 / 离线 Form 字段）的当前生效默认值，反映实际配置；Web UI 用于数值框占位提示 |
