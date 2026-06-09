# 说话人管理（v2）

[← API v2 概览](../v2.md) ｜ **中文** | [English](speakers_EN.md)

说话人分离（匿名标签）与声纹识别（真名）的能力说明，以及声纹库 `/v2/speakers*` 的登记 / 管理 / 识别接口。

## 目录

- [说话人分离与声纹识别](#说话人分离与声纹识别)
- [声纹库接口 `/v2/speakers*`](#声纹库接口)
  - [登记说话人](#登记说话人)
  - [列表 / 详情 / 改名备注 / 删除](#列表--详情--改名备注--删除)
  - [模板管理 / 识别](#模板管理--识别)
  - [错误码](#错误码)

---

## 说话人分离与声纹识别

两层能力，均默认关闭、按需启用（配置见[配置文档](../../configuration.md)）：

| 层 | 开关 | 产出 |
|----|------|------|
| **说话人分离**（匿名） | `enable_speaker` | 离线 `segments[].speaker` 与顶层 `speakers`、实时 `final.speaker`——标签 `A`/`B`/`C`… 按首次开口顺序，**作用域为单文件/单会话**（跨任务不保证同人同标签） |
| **声纹识别**（真名） | `enable_speaker_db`（依赖上一层 + 必须配置 `api_key`） | 请求级 `identify_speakers=true` 时比对声纹库：命中输出 `speaker_name`；离线未命中且语音足量（默认 ≥10s）的说话人**自动登记**为「说话人_NN」占位名（`speaker_auto_enroll`，可关），改名见[列表 / 详情 / 改名备注 / 删除](#列表--详情--改名备注--删除) |

要点：

- **失败永远优雅降级**：分离/识别任一环节失败只丢标签/真名，转写结果不受影响。
- **实时识别"以最新 final 为准"**：早期 final 可能无 `speaker_name`（质心未稳定），后续命中后新 final 携带真名，**不回改历史消息**；实时路径不自动登记。
- `speakers[].speaker_id` 为**纯值快照**：与任务库无关联，说话人被删除后历史任务中的 id 悬空（`GET /v2/speakers/{id}` 返回 404 即"已删除"），调用方需容忍。
- 自动登记的 consent 语义：开启 `speaker_auto_enroll` 即部署方声明已获得数据主体对声纹登记的同意（与手动登记 `consent=true` 同一责任归属）。

转写如何携带 `identify_speakers`：离线见[转写 · 提交 ASR 任务](transcription.md#提交-asr-任务)，实时见[转写 · 客户端 → 服务端](transcription.md#客户端--服务端)；离线结果中的说话人字段见[任务管理 · 结果结构](tasks.md#结果结构)。

带声纹识别的离线结果示例（`result` 增量部分）：

```json
{
  "segments": [
    {"start": 0.0, "end": 3.2, "text": "大家好。", "speaker": "A", "speaker_name": "张三"},
    {"start": 3.5, "end": 6.0, "text": "我先说两句。", "speaker": "B", "speaker_name": "说话人_07"}
  ],
  "speakers": [
    {"label": "A", "speaker_id": "9f86d081884c7d659a2feaa0c55ad015", "name": "张三", "score": 0.62},
    {"label": "B", "speaker_id": "3c2a91f0a1b24e83b6f1c2d3e4f5a6b7", "name": "说话人_07", "score": null, "auto_enrolled": true},
    {"label": "C", "speaker_id": null, "name": null, "score": null}
  ]
}
```

## 声纹库接口

```
/v2/speakers*（仅 v2；全部端点强制 Bearer 认证——服务端未配置 api_key 时声纹库整体不可用）
```

> 浏览器管理页：启动加 `--web` 后访问 `/web-ui/speakers`（列表 / 改名备注 / 删除）。

声纹数据**永不自动清理**（与任务库的 7 天 TTL 不同），唯一删除途径为 DELETE 接口（硬删除 + 物理回收）。

### 登记说话人

```
POST /v2/speakers        （multipart）→ 201
```

```bash
curl -X POST http://127.0.0.1:8765/v2/speakers \
  -H "Authorization: Bearer sk-your-key" \
  -F "name=张三" -F "consent=true" -F "note=产品部" \
  -F "files=@sample1.wav" -F "files=@sample2.wav" -F "files=@sample3.wav"
```

| 参数 | 说明 |
|------|------|
| name | 显示名（必填） |
| consent | 必须为 `true`：确认已获得数据主体同意（否则 400） |
| note | 备注（可选） |
| files | ≥1 个**单人**清晰音频样本；每个样本 VAD 后有效语音 ≥3s（可配 `speaker_enroll_min_sec`），检出多说话人将 400 拒绝；建议 ≥3 个不同场景样本（不足仅提示 `quality_hint` 不阻断） |

响应：`{"speaker_id": "9f86…", "name": "张三", "templates": 3, "quality_hint": null}`

### 列表 / 详情 / 改名备注 / 删除

```
GET    /v2/speakers                 → {"total": N, "speakers": [{id,name,note,source,template_count,created_at,updated_at}]}
GET    /v2/speakers/{id}            → 详情（含模板摘要 templates: [{id,dur_sec,created_at}]，不含特征向量）
PATCH  /v2/speakers/{id}            → body {"name"?, "note"?}；改名不影响 speaker_id 与模板，后续转写立即显新名
DELETE /v2/speakers/{id}            → 硬删除（级联模板 + 物理回收 + 留存音频清理，不可恢复）；该说话人在后续转写中退回匿名
```

- `source` 字段区分 `manual`（手动登记）/ `auto`（自动登记），便于清查占位名条目。

### 模板管理 / 识别

```
POST   /v2/speakers/{id}/templates          （multipart file）→ 201，追加样本并重算质心（每人上限 16）
DELETE /v2/speakers/{id}/templates/{tid}    → {"remaining": N, "hint"?}（剩 0 模板不自动删人，hint 提示补样本或删除）
POST   /v2/speakers/identify                （multipart file）→ {"matched": bool, "speaker_id"?, "name"?, "score"?}
```

识别为 1:N 开集判定：最高相似度低于阈值（`speaker_id_threshold`，默认 0.45）或与次高差距过小（`speaker_id_margin`，默认 0.10——近邻打架宁缺勿错）时返回 `matched: false`。

### 错误码

| 状态码 | 说明 |
|--------|------|
| 400 | 质量门槛不达标（时长不足/多说话人/格式不支持）/ consent 缺失 |
| 401 | 认证失败 |
| 404 | 说话人/模板不存在 |
| 503 | `speaker_db_disabled`（模块未启用/已降级）/ `model_tag_mismatch`（库内模板与当前引擎版本不一致：登记与识别禁用，**查看与删除仍可用**） |
| 500 | 声纹库读写失败 |
