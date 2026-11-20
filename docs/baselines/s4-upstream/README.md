# 独立 Qwen 上游的实时协议探测

2026-11-20 使用 RTX 3090、qwen-asr 0.0.6、vLLM 0.14.0 与 Qwen3-ASR 1.7B 重测 [S2 的独立上游](../s2-qwen-http/README.md)。本轮在回环地址启动同一服务，省略鉴权参数，排除 S2 观测到的鉴权中间件异常。

从仓库根目录运行以下命令。使用 S1 样本生成器生成 short.wav，并按 S2 说明在 18764 端口启动独立上游。

```sh
asr-service/venv/bin/python docs/baselines/s4-upstream/probe.py /tmp/s205-input/short.wav
```

| 检查 | 实测 |
| --- | --- |
| GET /v1/models | 列表包含 qwen3-asr |
| POST /v1/audio/transcriptions | 返回 language English 前缀和正文 “The blue train leaves at noon.” |
| WebSocket /v1/realtime | 握手返回 HTTP 403，会话未建立 |
| 已部署服务的路由 | OpenAPI 没有 realtime HTTP 路由；安装版本的服务端也未注册实时 WebSocket |

S4-02 暂缓。此版本已验证的是接收完整音频文件的 HTTP 接口。要启用远端 partial，仍需要能持续接收音频并在句中返回识别结果的上游端点。
