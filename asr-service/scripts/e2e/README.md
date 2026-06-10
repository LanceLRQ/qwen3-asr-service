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
