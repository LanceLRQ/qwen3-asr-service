# S1-03 GPU 基线与 S1-02 协议实录

2026-11-20，服务启动时仓库基线为 `06d6efab95dcde2fea452253eb030d7bae33c2ab`。使用现有 standard venv 和本地权重，RTX 3090 24 GiB，驱动 595.91.07，Python 3.12.3，torch 2.5.1+cu121，transformers 4.57.6，qwen-asr 0.0.6，funasr 1.3.1。启用 CUDA ASR、0.6B 对齐、FSMN VAD 和 CAMPPlus。匿名说话人标签开启，声纹库和任务持久化关闭。完整能力声明见 responses.json 的 health、capabilities 和首条实时事件。

## 复现

从仓库根目录执行以下命令。样本仅使用本次编写的英文台词、FFmpeg 6.1.1-3ubuntu5 内置 Flite 音色、数字静音和固定种子的均匀白噪声，供项目回归评测复用。samples.json 记录逐句文本、合成音色、拼接起止秒及 WAV SHA-256。双人样本使用两个合成音色，长音频重复双人样本的三句台词十轮。

```bash
cd asr-service
B=../docs/baselines/s1-standard-gpu
sha256sum -c "$B/weights.sha256"
venv/bin/python "$B/samples.py" /tmp/s103-input >/tmp/s103-samples.json
cmp /tmp/s103-samples.json "$B/samples.json"
HF_HUB_OFFLINE=1 venv/bin/python -m app.main --config "$B/config.yaml" --no-web
```

另一终端从仓库根目录运行以下命令。等健康状态达到 ready 后执行；性能复测先预热一次，再采集。端口 18763 仅监听本地回环，本配置免鉴权。

```bash
cd asr-service
venv/bin/python ../docs/baselines/s1-standard-gpu/capture.py /tmp/s103-input /tmp/s103-responses.json
```

## 实测

| 输入 | 时长（秒） | 处理（秒） | RTF | 输出 |
| --- | ---: | ---: | ---: | --- |
| 短句 | 3.925 | 0.3 | 0.076 | 6 词全部正确 |
| 双人三句 | 9.765 | 0.5 | 0.051 | 18 词全部正确，三个段均标为 A，漏分第二个音色 |
| 静音 | 5 | 0.1 | 0.020 | 空文本、零段 |
| 白噪声 | 5 | 0.1 | 0.020 | 空文本、零段 |
| 长音频 | 88.65 | 2.2 | 0.025 | 30 句经 20 个 chunk 输出 30 段，180 个参考词中 5 个编辑错误，WER 2.78%；两个音色产生 A/B/C 三簇 |

离线串行热运行，各任务取一次。处理时间取服务端对应任务 ID 的完成日志（精度 0.1 秒），包含音频转换、VAD、ASR、对齐和说话人处理。RTF 为该值除以输入秒数。排队上界从客户端发起上传计到首次观察到 processing，包含 HTTP 开销，本轮各样本约 2–10 毫秒。每 200 毫秒采样一次 nvidia-smi，该服务进程实测最高 6828 MiB。单次采样值仅供同配置比较。

WER 将英文转小写、按字母串分词，对参考与输出词序列计算编辑距离。静音和噪声按误转写检查。逐段按顺序与对应合成片段比较，长音频平均绝对起点偏差 194 毫秒、终点偏差 154 毫秒。拼接边界包括合成器自带留白，词级精度尚缺人工标注。说话人漏分、额外分簇和长音频中 bag 被识别为 gag 均已在本基线出现。

## S1-02 响应定位

responses.json 保存服务返回的 JSON。配置 web 为 true，启动时传 --no-web 后 /web-ui 返回 404。

- 同步请求使用 OpenAI 兼容转写接口、whisper-1 别名和 en 语言提示。sync.text 为 “The blue train leaves at noon.”。
- 异步请求向 /v2/asr 上传 WAV，随后轮询 /v2/tasks/{task_id}。async.short 保存提交 ID、状态观测和终态。状态为 completed，短句文本同上，结果带 segments、words 和匿名说话人。
- 实时连接 /v2/asr/stream，收到 session.created 后发送 start、16 kHz 单声道 PCM16 小端音频和 stop。stream.events 依次为 session.created、final、session.closed。final 文本同上，partial_results 为 false。客户端 websockets 16.0 观察到关闭码 1006，应用层结束事件已到达，传输层未收到正常关闭帧。
