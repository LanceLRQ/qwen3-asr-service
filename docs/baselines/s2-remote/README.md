# 独立 Qwen 服务的离线转写

`serve_mode: remote` 在服务端执行音频转换、VAD 切块、分句及启用的辅助处理，每个语音块通过 multipart 上传给独立模型服务。原生异步任务、OpenAI 兼容同步接口沿用现有入口。选择此模式时，客户端服务只加载本地辅助模型，ASR 权重由上游进程加载。

## 部署与回退

1. 准备 [已验证的独立 Qwen 服务](../s2-qwen-http/README.md)。本次使用同一 RTX 3090 上的两个进程，分别使用 venv-vllm 和 standard venv。上游 `--gpu-memory-utilization 0.50`，配置的模型别名为 `qwen3-asr`。上游与本服务均绑定回环地址。
2. 将 [示例配置](config.yaml) 的 `remote_base_url` 改为从本服务可达的上游 API 地址，保留末尾的 `/v1`。按上游配置填写 `remote_model`。启动时先用 `GET /v1/models` 校验别名，再通过 `POST /v1/audio/transcriptions` 发起转写。每次请求使用 `remote_timeout`（秒）约束网络等待。
3. 在服务进程的环境中设置上游凭据 `ASR_REMOTE_API_KEY`。调用本服务的入站密钥仍使用 `api_key` / `ASR_API_KEY`。验证配置默认在 `127.0.0.1:18765` 接受请求。

从仓库根目录运行：

```bash
cd asr-service
HF_HUB_OFFLINE=1 venv/bin/python -m app.main --config ../docs/baselines/s2-remote/config.yaml
```

使用另一终端执行 [S1 接入示例](../../../asr-service/scripts/e2e/README.md#三条最小集成示例) 的离线命令，目标地址改为 `http://127.0.0.1:18765`。也可用以下命令重跑本次采集；指标统计依赖上游 `/metrics`，请保持其间无其他请求。

```bash
cd asr-service
venv/bin/python ../docs/baselines/s1-standard-gpu/samples.py /tmp/s205-input > /tmp/s205-samples.json
cmp /tmp/s205-samples.json ../docs/baselines/s1-standard-gpu/samples.json
curl -fsS -F model=whisper-1 -F file=@/tmp/s205-input/short.wav \
  http://127.0.0.1:18765/compat/openai/v1/audio/transcriptions
venv/bin/python ../docs/baselines/s2-remote/capture.py /tmp/s205-input > /tmp/s205-results.json
```

要回退，停止本服务，恢复原来的 standard 配置并用 standard venv 重启。S1 验证配置及启动命令见[本地基线](../s1-standard-gpu/README.md)。同卡部署时，先停止专用的上游测试进程，释放本地模型所需显存。生产环境原有的认证、任务存储和端口设置应沿用原配置。

## 能力与结果解释

本次开启本地 FSMN VAD 和 CAM++ 说话人分离；声纹库、标点恢复器、音频标注和任务持久化关闭。上游可能在文本中输出模型自身的标点。显式请求词级时间戳时，原生任务在 warnings 中返回 with_words，segments 中省略 words。单块的起止时间来自原音频的全局 VAD 时间轴，块内分句边界由字符比例估计。说话人标签取决于本地聚类以及这些段边界。当前远端模式提供离线入口；原生实时与兼容实时路由均未挂载。

## 2026-11-20 真机结果

同一块 RTX 3090、同一份 Qwen3-ASR 1.7B 权重和 S1 的五份合成音频。S1 使用 Transformers 和本地对齐器，本次上游使用 vLLM 0.14.0、qwen-asr 0.0.6，本项目服务使用 standard venv、本地 FSMN VAD 和 CPU 上的 CAM++。各任务串行运行，预热短句后每个样本采集一次。原生异步五个任务均为 completed；兼容同步返回 “The blue train leaves at noon.”，兼容模型列表返回上游别名 qwen3-asr。[原始结果及时间观测](responses.json)

| 样本 | 远端块数 / 输出段数 | 任务处理时间：S1 / S2（秒） | 上游 HTTP 处理 / 模型推理（秒） | 文本及说话人 |
| --- | ---: | ---: | ---: | --- |
| 短句 3.925 秒 | 1 / 1 | 0.3 / 0.3 | 0.171 / 0.168 | 6 词正确，A |
| 双人 9.765 秒 | 3 / 3 | 0.5 / 0.8 | 0.513 / 0.503 | 18 词正确，两种音色仍全部标 A |
| 静音 5 秒 | 0 / 0 | 0.1 / 0.1 | 0 / 0 | 空文本 |
| 白噪声 5 秒 | 0 / 0 | 0.1 / 0.1 | 0 / 0 | 空文本 |
| 长音频 88.65 秒 | 20 / 30 | 2.2 / 6.3 | 4.543 / 4.477 | 180 个参考词、5 个编辑错误，WER 2.78%；两种音色分为 A/B/C |

时间边界按输出顺序与 S1 对应段比较。短句起点、终点分别相差 320、280 毫秒；双人样本的平均绝对差为 267、283 毫秒，长音频为 429、365 毫秒。三个样本的逐段说话人标签与 S1 一致。合成片段的边界包含前后留白，此次比较只衡量输出差异。

**耗时口径。** 任务处理取 worker 完成日志（0.1 秒精度）。上传到本服务并收到 task_id 用时 1.5–11.4 毫秒；从上传开始到首次观察到 processing 为 2.1–12.5 毫秒，这是本地排队时间的上界。上游 HTTP 处理、上传字节数、推理及上游引擎排队来自独占服务的 Prometheus 计数器差值。以长音频为例，20 个请求上传 2,281,698 字节，上游引擎排队合计约 0.16 毫秒。总处理与上游 HTTP 处理相差约 1.8 秒，包含本地转换、VAD、分句、说话人处理和 HTTP 客户端通信；本轮采样无法进一步拆分本地辅助计算与传输耗时。这组数据用于同配置回归，尚未测量跨主机链路或并发吞吐。

**失败和取消。** 连接失败、网络等待超时、鉴权拒绝、上游限流或错误格式会结束任务；同步调用通过兼容错误信封返回失败。服务不会重发失败的块。取消在途请求时，当前请求可能继续运行至响应或超时，本地丢弃取消后返回的结果并停止发送后续块。

**回退实测。** 使用 S1 配置重新启动 standard，本地健康检查返回 mode=standard、asr_backend=qwen_asr、align_enabled=true。同一短句通过兼容接口再次得到完整英文转写。
