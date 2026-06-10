# 兼容接口（OpenAI / 阿里云 DashScope）

[← API v2 概览](v2.md) ｜ **中文** | [English](compat_EN.md)

为已接入 **OpenAI** 或 **阿里云 DashScope（Paraformer）** 语音生态的客户端提供 drop-in 兼容垫片：改写 SDK 的 base url 指向本服务的 `/compat/...` 前缀即可对接，无需改动业务代码。兼容层是适配垫片，复用现有转写管线与任务队列，与原生 `/v1`、`/v2` 完全隔离。

> 兼容接口默认**关闭**，需启动时显式开启：
> ```bash
> # 离线 + 实时（实时需同时 --enable-stream）
> python -m app.main --enable-openai-api --enable-dashscope-api --enable-stream --api-key sk-xxx
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
| DashScope 实时 | `dashscope.base_websocket_api_url` | `ws://<host>:8765/compat/dashscope/api-ws/v1` |

前缀之后的上游子路径逐段原样保留（SDK 硬编码），故兼容路径与 `/v1`、`/v2` 零碰撞。

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
| 实时整句 | ✅ completed（需 --enable-stream）| ✅ result-generated（需 --enable-stream）|
| 实时逐字增量 | ❌ 需 vLLM | ❌ 需 vLLM |
| 置信度/logprob | ➖ 占位 | ➖ 不提供 |
| 引导/温度/热词/顺滑 | ❌ 忽略 | ❌ 忽略 |

## 与原生 v2 的取舍

- 需要**对接已有 OpenAI/DashScope 生态**（SDK、现成客户端）→ 用兼容接口。
- 需要本服务**全部能力**（声纹库、任务列表/取消、统一实时信封、按请求覆盖参数）→ 用原生 [API v2](v2.md)。
- 超长音频：OpenAI 同步端点受 `--openai-sync-timeout` 限制，超长建议走 DashScope 异步兼容或原生 v2 异步。
