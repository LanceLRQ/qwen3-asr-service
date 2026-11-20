# 兼容接口端到端测试（E2E）

用**独立 venv**（与项目主环境隔离）一键起 mock 服务 + 跑真实客户端冒烟，验证 `/compat/*`
端点与上游 SDK/协议契约对齐——覆盖单测 mock 覆盖不到的「SDK 实际字段/编码、WS 握手」。

- 离线走 **官方 SDK**（openai / dashscope）
- 实时走 **裸 WebSocket**（websockets）
- mock 服务复用项目 compat 代码 + 固定转写结果，**不需真模型**

## 一键跑（默认 mock 服务）

```bash
cd asr-service/scripts/e2e
./run.sh
```

首次运行自动创建 `.venv`（已 gitignore）并装 `requirements.txt`，随后起 mock 服务并跑全部
check。验证的是协议契约/字段对齐/WS 握手/SSE/错误码/DashScope 下载链路（转写文本是 mock 固定值）。

## 测真实运行的服务

先启动带兼容接口的服务（需真模型，验证真实转写）：

```bash
python -m app.main --enable-openai-api --enable-dashscope-api --enable-stream \
  --compat-fetch-allow-private --api-key sk-xxx
```

再对它跑（自带 `--base-url` 时不起 mock）：

```bash
./run.sh --base-url http://127.0.0.1:8765 --api-key sk-xxx
```

## 常用

```bash
./run.sh --list                                   # 列出所有 check
./run.sh --only openai-realtime,dashscope-realtime
./run.sh --skip dashscope-offline
./run.sh --audio /path/to/speech.wav
```

退出码：全部通过 0、有失败 1、前置缺失 2。

## 说明

- `--compat-fetch-allow-private`：DashScope 离线需要——客户端会起本地 HTTP server 把音频
  暴露成 `http://127.0.0.1:PORT/a.wav`，服务端需允许下载回环地址（mock 服务已默认开启）。
- 主项目 venv 若 WebSocket 握手失败（迁移/版本不配套），这个独立 venv 正是为绕开它而设。

## 三条最小集成示例

以下命令从 `asr-service/` 执行。使用 standard 服务环境启动时显式指定 `--no-web`，即使配置文件为 `web: true` 也会关闭 UI。同步接口需要 OpenAI 兼容开关，实时接口需要流式开关。

```bash
python -m app.main --no-web --enable-openai-api --enable-stream --api-key demo-key
```

另开终端运行以下示例。默认输入是仓库的 16 kHz 中文语音 WAV，可用 `--audio /path/to/speech.wav` 指定一段短语音。HTTP 请求使用 Bearer 密钥，WebSocket 通过查询参数 token 鉴权。服务未配置密钥时可省略客户端的 `--api-key`。

```bash
args=(--base-url http://127.0.0.1:8765 --api-key demo-key)
scripts/e2e/run.sh "${args[@]}" --only openai-transcribe-json
scripts/e2e/run.sh "${args[@]}" --only v2-task
scripts/e2e/run.sh "${args[@]}" --only v2-stream
```

| 示例 | 请求与预期结果 | 常见失败 |
| --- | --- | --- |
| 同步 | OpenAI SDK 向 `/compat/openai/v1/audio/transcriptions` 提交 WAV，直接读取 JSON 的 `text`。说话人分离等原生选项请使用 v2 接口。 | 密钥无效返回 401，兼容路由未启用返回 404。 |
| 异步 | multipart 上传到 `POST /v2/asr`，用返回的 task_id 轮询 `GET /v2/tasks/{task_id}`。completed 后读取 result.full_text 和 result.segments。 | failed 时读取 error，cancelled 或 not_found 时停止轮询。等待超时后可凭 task_id 继续查询。 |
| 实时 | 连接 `/v2/asr/stream` 后读取 session.created，发送携带 audio_fs 的 start，再按采样率发送单声道 PCM16 小端二进制帧。发送 stop 后读完逐句 final，收到 session.closed 即退出连接。 | 密钥错误会拒绝连接。音频帧过大或积压超限时服务返回 error。 |

使用远端增量时，网关在启用实时的基础上设置 `--remote-stream-url`。客户端按 `seg_id` 用最新的 `partial.text` 替换当前句，收到 `final` 后定稿。三种客户端的可运行示例见[远端增量验收](../../../docs/baselines/s4-upstream/README.md)。

要取消排队中或执行中的任务，向同一任务地址发送带鉴权的 DELETE 请求。启用 `enable_task_store` 后可跨重启查询已完成的结果；重启时未完成的任务标记为 failed。对仅存在于持久化库的历史任务发 DELETE 会删除记录。[任务生命周期](../../../docs/api/v2/tasks.md)

本地 mock 复用上述三个客户端和服务端路由，返回固定文本“你好世界”。以下命令验证协议，识别质量需用真实模型及语音另行验收。

```bash
E2E_API_KEY=demo-key scripts/e2e/run.sh --api-key demo-key --only openai-transcribe-json,v2-task,v2-stream
```
