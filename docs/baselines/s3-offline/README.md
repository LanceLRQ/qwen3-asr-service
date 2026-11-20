# S3 离线核心与独立词时间验收

2026-11-20 在 RTX 3090 上使用 Qwen3-ASR 1.7B。标准模式对照 [S1](../s1-standard-gpu/README.md)，远端模式对照 [S2](../s2-remote/README.md)，原生 vLLM 对照同机在 S2 提交 `1f79657` 上的运行。各组合先用短句预热，再串行提交样本。客户端耗时含上传、排队和轮询，每个样本测一次，仅用于发现明显变化。[逐样本时间、段边界和文本对照](metrics.json)

## 复现

使用 [S1 样本生成器](../s1-standard-gpu/samples.py) 生成音频，并对照同目录的 samples.json 校验 SHA-256。开启服务后，读取 `GET /v2/health` 的 capabilities，以 `POST /v2/asr` 上传 file，同时传入 language=en、diarize=true、with_words=true，再从 `GET /v2/tasks/{task_id}` 读取终态。用短句把后两个选项均设为 false，再检查返回中省略词时间与说话人。

| 服务组合 | 基础配置与附加参数 | 有声样本预期 |
| --- | --- | --- |
| standard | S1 配置 | 词时间和说话人 |
| vLLM | S1 配置，vLLM 环境，`--serve-mode vllm --gpu-memory-utilization 0.50 --vllm-max-model-len 4096` | 词时间和说话人 |
| 远端纯转写 | S2 配置，`--no-speaker` | 两项均提示不可用 |
| 远端独立对齐 | S2 配置，`--no-speaker --remote-enable-align` | 词时间，提示说话人不可用 |
| 远端说话人 | S2 配置 | 说话人，提示词时间不可用 |
| 远端对齐和说话人 | S2 配置，`--remote-enable-align` | 词时间和说话人 |

远端测试使用独立上游进程。服务进程的模型目录仅提供 vad、speaker 和 align，启用离线模型加载。四种远端组合均测试三句英文对话；远端说话人、远端对齐和说话人、standard 与 vLLM 另测短句、静音、噪声和 88.65 秒长音频。本地两个模式另通过原生 WebSocket 发送短句并结束会话。

## 实测结果

standard、原生 vLLM 和沿用 S2 配置的远端说话人模式，五份样本的全文、分段边界和说话人标签分别与各自基线一致。原生 vLLM 在静音和噪声样本上仍产生文字；同机重构前也出现了相同转写。standard 和远端模式在这两个样本上均返回空段列表。

独立对齐为英文对话的三句各返回 6 个词，时间范围分别为 1.23–2.75、4.09–5.77、7.14–8.58 秒。88.65 秒长音频返回 180 个词，远端同时开启说话人和对齐时耗时 6.68 秒，仅开启说话人时为 6.18 秒。长音频开启对齐后，全文与 standard 完全一致，与 S2 的差异仅为句间空格；分段起止相对 S2 估计边界的平均绝对差分别为 0.430 和 0.366 秒。两种本地模式的实时短句均收到 final 与 session.closed。
