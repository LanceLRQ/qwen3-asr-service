# S2-01 独立 Qwen HTTP 服务协议实测

2026-11-20 在 RTX 3090 上启动独立的 qwen-asr-serve 进程，用另一个 Python 环境的 HTTP 客户端提交 S1 的 3.925 秒英文短句。模型为 Qwen3-ASR 1.7B，权重摘要见 [S1 权重清单](../s1-standard-gpu/weights.sha256)。服务依赖为 qwen-asr 0.0.6、vLLM 0.14.0、torch 2.9.1、transformers 4.57.6。此次验证走本机回环 HTTP，跨主机网络耗时未经测量。

## 接入契约

S2-02 可优先实现此版本的 multipart 文件转写。以 Bearer 鉴权向 `POST /v1/audio/transcriptions` 提交 `model=qwen3-asr` 和 `file=short.wav`，接收 JSON。实测原始响应如下：

```json
{"text":"language English<asr_text>The blue train leaves at noon.","usage":{"type":"duration","seconds":4}}
```

适配器需要从 text 中分离模型识别的语言和正文。usage.seconds 是整秒用量，此样本实际为 3.925 秒。音频边界应取自输入样本。以下结论限于本部署组合。

| 探测项 | 观测 |
| --- | --- |
| 模型发现与鉴权 | 带密钥访问 GET /v1/models 返回别名 qwen3-asr；缺少密钥的聊天请求返回 401，未知模型返回 404。 |
| 语言 | multipart 省略 language、传 en、传 fr 均返回同一英文正文和 English 标记；zz 返回 400。不应对调用方承诺强制指定输出语言。 |
| 输入限制 | 默认 multipart 文件上限为 25 MiB，超过 1 字节返回 400。遗漏 file 也返回 400。 |
| 词级时间戳 | 请求 verbose_json 和 word 粒度返回 400，错误指出该模型不支持 verbose_json。普通成功响应仅含 text 和 usage。 |
| 聊天接口 | POST /v1/chat/completions 接受 audio_url 内嵌的 WAV data URI，正文位于 choices[0].message.content，也带语言前缀。顶层 language 被服务日志标为忽略。本配置上下文 4096 tokens，请求 max_tokens=4097 返回 400。 |
| 实时输入 | 对 /v1/realtime 发起带鉴权的 WebSocket 握手返回 500，服务日志显示鉴权中间件读取 WebSocket scope 的 method 失败。本组合未验证可用的远端增量音频协议。 |

## 复现

在两个终端进入 asr-service 目录，并为两个终端的 ASR_PROBE_KEY 环境变量设置相同的临时值。第一终端启动独立模型进程：

```bash
HF_HUB_OFFLINE=1 VLLM_NO_USAGE_STATS=1 venv-vllm/bin/qwen-asr-serve models/asr/1.7b \
  --host 127.0.0.1 --port 18764 --served-model-name qwen3-asr --api-key "${ASR_PROBE_KEY:?}" \
  --enforce-eager --gpu-memory-utilization 0.65 --max-model-len 4096 --max-num-seqs 1
```

第二终端生成固定样本，再执行探测：

```bash
venv/bin/python ../docs/baselines/s1-standard-gpu/samples.py /tmp/s201-audio > /tmp/s201-samples.json
cmp /tmp/s201-samples.json ../docs/baselines/s1-standard-gpu/samples.json
venv/bin/python ../docs/baselines/s2-qwen-http/probe.py /tmp/s201-audio/short.wav > /tmp/s201-responses.json
```

[探测脚本](probe.py) 保存请求构造方式；[响应记录](responses.json) 保存状态码、响应体和样本哈希。缺文件错误省略服务端绝对路径及后续栈信息。请求 ID 与时间由服务端动态生成。
