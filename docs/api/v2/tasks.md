# 任务管理（v2）

[← API v2 概览](../v2.md) ｜ **中文** | [English](tasks_EN.md)

离线转写任务的生命周期管理与结果查询。任务由[`POST /v2/asr`](transcription.md#提交-asr-任务) 创建，本节负责列出、查询结果、取消删除，以及任务持久化对这些接口的影响。

## 目录

- [获取任务列表 `GET /v2/tasks`](#获取任务列表)
- [查询任务详情 `GET /v2/tasks/{task_id}`](#查询任务详情)
  - [结果结构](#结果结构)
  - [音频标注增量字段](#音频标注增量字段)
- [取消 / 删除任务 `DELETE /v2/tasks/{task_id}`](#取消--删除任务)
- [任务持久化对 API 的影响](#任务持久化对-api-的影响)

---

## 获取任务列表

```
GET /v2/tasks
```

```bash
# 全部活动任务
curl http://127.0.0.1:8765/v2/tasks

# 按状态筛选
curl http://127.0.0.1:8765/v2/tasks?status=processing

# 合并历史任务（需开启任务持久化 enable_task_store）
curl "http://127.0.0.1:8765/v2/tasks?history=true&limit=20"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| status | string | null | 筛选状态：`pending` / `processing` / `completed` / `failed` / `cancelled` |
| history | bool | false | 合并持久化库中的历史任务（未开启 `enable_task_store` 时无效果） |
| limit | int | 50 | `history=true` 时返回的最大条数 |

响应（按创建时间倒序，不含识别结果）：

```json
{
  "total": 2,
  "tasks": [
    {
      "task_id": "550e8400-...",
      "status": "completed",
      "progress": 1.0,
      "language": null,
      "wav_name": "meeting.mp3",
      "created_at": "2026-06-04T10:30:00",
      "finished_at": "2026-06-04T10:31:00",
      "error": null
    },
    {
      "task_id": "660e8400-...",
      "status": "processing",
      "progress": 0.45,
      "language": "zh",
      "wav_name": "interview.wav",
      "created_at": "2026-06-04T10:31:00",
      "finished_at": null,
      "error": null
    }
  ]
}
```

## 查询任务详情

```
GET /v2/tasks/{task_id}
```

响应（完成）：

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
  "error": null,
  "wav_name": "meeting.mp3",
  "created_at": "2026-06-04T10:30:00",
  "finished_at": "2026-06-04T10:31:00"
}
```

- 任务状态流转：`pending` → `processing` → `completed` / `failed` / `cancelled`。
- 任务不存在时返回 200，`status` 为 `not_found`。
- 开启任务持久化后，内存中已过期/重启前的历史任务会从持久化库兜底返回（含 `result`）。

### 结果结构

`result` 为离线转写产出，字段如下：

| 字段 | 说明 |
|------|------|
| `segments[]` | 句级结果数组，每段含 `start` / `end`（秒）、`text` |
| `segments[].words` | 单词级时间戳，**仅 `align_enabled=true` 时存在** |
| `full_text` | 全文拼接 |
| `language` | 识别语言（`null` = 自动检测未回填） |
| `align_enabled` | 是否启用了对齐（决定 `words` 是否存在） |
| `punc_enabled` | 是否启用了标点恢复 |

开启说话人分离（`enable_speaker`）后 `result` 增量字段（关闭时不出现，语义详见[说话人管理](speakers.md#说话人分离与声纹识别)）：

- `segments[].speaker`：匿名标签 `A`/`B`/`C`…（按首次开口顺序）；
- `segments[].speaker_name`：声纹命中时的真名（仅 `identify_speakers=true` 且命中）；
- 顶层 `speakers`：说话人列表——纯分离时为 `["A","B"]`；声纹识别时升级为映射表
  `[{"label","speaker_id","name","score","auto_enrolled"?}]`（未命中条目 `speaker_id`/`name` 为 `null`）。

### 音频标注增量字段

开启音频标注（`--enable-audio-tagging`）后 `result` 新增以下**可选**字段（未开启时不出现）：

- 顶层 `audio_events`：按起止时间聚合的事件段数组（**非逐窗**），每段
  `{"label","start_ms","end_ms","confidence"}`——`label` 为 AudioSet 类别，`start_ms`/`end_ms` 为毫秒，`confidence` 为该段内最大概率；
- `segments[].scene`：该段主场景字符串，取值 `silence`/`speech`/`singing`/`music`/`other`（或自定义桶），仅在场景识别（`scene_enable`）开启时存在；
- `segments[].scene_scores`：该段各场景桶的平均概率分布 `{bucket: prob}`，**各桶独立置信度**（不归一到 1，能体现「说话+背景音乐」并存，如 `{"speech":0.62,"music":0.31}`）。
- 提交时可加表单参数 `scene_preset`（`balanced`/`live`/`music`）按请求覆盖场景判定预设。

```json
"segments": [{"start": 0.31, "end": 11.76, "text": "...", "scene": "speech",
              "scene_scores": {"speech": 0.62, "music": 0.31, "singing": 0.03}}],
"audio_events": [{"label": "Speech", "start_ms": 0, "end_ms": 25920, "confidence": 0.897}]
```

## 取消 / 删除任务

```
DELETE /v2/tasks/{task_id}
```

响应：

```json
{"task_id": "550e8400-...", "status": "cancelled", "message": "任务已取消"}
```

| 任务状态 | 行为 | 返回 `status` |
|---------|------|--------------|
| `pending` | 立即取消 | `cancelled` |
| `processing` | 在当前 chunk 处理完成后停止，返回已识别的部分结果 | `cancelled` |
| `completed` / `failed` / `cancelled` | 不改变状态 | `already_completed` / `already_failed` / `already_cancelled` |
| 仅存在于持久化库的历史任务 | **删除该条记录**（需开启 `enable_task_store`） | `deleted` |
| 不存在 | - | `not_found` |

## 任务持久化对 API 的影响

开启 `enable_task_store` 后（见[配置文档](../../configuration.md#离线任务持久化tasksdb)）：

- **结果跨重启可查**：`GET /tasks/{id}` 对重启前完成的任务仍返回完整 `result`（内存未命中时查持久化库）。
- **重启收口**：服务重启时，上次未完成（`pending` / `processing`）的任务被标记为 `failed`，`error` 为 `"service restarted"`，**不会自动重跑**。
- **历史查询**：`GET /tasks?history=true&limit=N` 合并返回库内历史任务。
- **历史删除**：`DELETE /tasks/{id}` 对仅存在于库内的历史任务执行记录删除（返回 `deleted`）。
- **过期清理**：终态超过 `task_retention_days`（默认 7 天）的记录在服务启动时被清理。

未开启时（内置默认）：任务仅存于内存，终态结果保留 1 小时后清理，重启即丢失。
