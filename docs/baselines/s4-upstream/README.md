# S4-02 实时增量上游与三协议网关

## 早期整文件 HTTP 服务探测

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

18764 的整文件服务无法提供句中结果。后续验收改用独立的官方 streaming demo。

## 官方 demo 直连

18842 运行 qwen-asr-demo-streaming，Qwen3-ASR 1.7B 使用 vLLM 0.14.0 和 CUDA，chunk-size-sec 为 0.5。模型证据包含监听进程、启动参数、引擎初始化摘录和两份实测权重 SHA-256。[模型证据](demo-model.json)

对 S1 英文短句裁去尾随静音，向 /api/start 创建的会话逐批提交 16 kHz float32 PCM，最后调用 /api/finish。累计输入 1.5 秒时返回 “The blue.”，2.0 秒时修订为 “The blue train leaves.”，finish 返回 “The blue train leaves at noon.”。直连脚本按响应推进，音频时长和墙钟延迟分别记录。[直连时序](demo-stream.json)

### 已验证的上游协议

上游未提供独立的协议版本号，本次固定为 qwen-asr 0.0.6 的 streaming demo。会话级解码参数由上游启动命令设置，语言自动识别。[官方启动说明](https://github.com/QwenLM/Qwen3-ASR#streaming-demo)

| 请求 | 输入与响应 |
| --- | --- |
| `POST /api/start` | 空请求体，返回 `session_id`。网关为每个语音段建立一个会话。 |
| `POST /api/chunk?session_id=…` | `application/octet-stream`，只传本次新增的 16 kHz 单声道 float32 小端样本。响应里的 `text` 是可修订的累计文本，`language` 可在首批结果中为空。 |
| `POST /api/finish?session_id=…` | 空请求体，上游冲刷尾音、返回权威全文并删除会话。网关用 `seg_id` 关联原生 partial 和 final。 |

上游没有请求序号或幂等重放约定。网关串行提交音频，请求失败即终止该会话。客户端断开时，网关等待在途请求，并按已知的会话 ID 尝试一次结束操作。若 start 响应丢失，网关无法取得 ID，回收依赖上游在后续访问时清理超过 10 分钟未活动的会话。

## 网关复现

在仓库根目录运行，先启动配置好的网关及上游。18843 网关的离线 HTTP 指向 18841 的 PocketSphinx，实时增量指向 18842 的 Qwen streaming demo。三个客户端协议均使用网关端口。

```sh
PYTHON="$PWD/asr-service/venv/bin/python"
BASE="$PWD/docs/baselines/s4-upstream"
"$PYTHON" docs/baselines/s1-standard-gpu/samples.py /tmp/s402-samples >/tmp/s402-samples.json
"$PYTHON" "$BASE/demo_stream.py" /tmp/s402-samples/short.wav --out /tmp/s402-direct.json
"$PYTHON" "$BASE/streaming.py" --out /tmp/s402-gateway.json
```

音频用 FFmpeg flite 的 slt 音色生成，发送速率为 16 kHz、单声道、16 位 PCM。每帧 100 毫秒，两句间发送 2 秒静音，末句裁去尾随静音后立即 stop、commit 或 finish-task。探针要求每句在最后一帧音频送完前收到非空 partial 或 delta，并检查两次 final 的顺序和末词 noon、bag。事件 ms 为连接尝试起的单调墙钟毫秒，audio_ms 为接收事件时客户端已发送的累计音频毫秒。

slow_input 将每 100 毫秒音频的发送间隔拉长到 160 毫秒，测试客户端慢发送。disconnect 在收到真实 partial 后直接中断传输；reconnect 新建会话，再次识别两句。上游延迟注入与背压由独立实验验证。

## 最终网关实测

预热模型后，三协议各运行正常两句含尾音、慢输入、断连、重连四个用例，12/12 通过。下表前两列时间取正常速率用例，握手数据取断连后的新会话。[逐事件网关响应](gateway.json)

| 协议 | 首句开始至首条增量（ms） | 末句结束控制至 final（ms） | 重连 403 次数 | 断连至新握手成功（ms） |
| --- | ---: | ---: | ---: | ---: |
| native | 651 | 43 | 1 | 103 |
| openai | 650 | 43 | 1 | 126 |
| dashscope | 651 | 44 | 0 | 24 |

OpenAI 的每句 delta 与 completed 共用 item_id，两句使用不同 ID。正常、慢输入及重连会话均在末句 completed 后继续读取至少 500 毫秒，未收到重复 completed。原生收到 session.closed 和正常关闭码 1000，DashScope 收到 task-finished。原生四项用相同音频在预热后单独复跑，逐会话时间见记录。

重启独立模型进程后，另用相同音频复测冷启动。首句从第 24 毫秒开始推送，第 3,739 毫秒结束，首条 partial 在第 3,925 毫秒到达，比句末晚 186 毫秒，未达到句中可见的判据。对首句有时延要求的部署可在接流量前用一段有声样本走完 start、chunk、finish，再测首条 partial。上述预热延迟仅代表本机回环、单会话的观测。[冷启动事件](cold-start.json)

## 延迟上游与背压（主进程独立实验）

独立网关 18845 的队列阈值设为 **12,800 字节**，产品默认阈值为 **8 MiB（8,388,608 字节）**。代理 18844 将真实 demo 的 chunk 响应延迟 2,000 毫秒。客户端每帧发送 100 毫秒 PCM（3,200 字节），首帧后等待 300 毫秒，再连续发送五帧。

从首次连接尝试起，第 328 毫秒收到致命错误 backlog_overflow。清理期间第二次握手返回 HTTP 403，网关等待旧会话的 chunk 和 finish 均返回 HTTP 200 后释放槽位。第 2,137 毫秒，新会话完成 start、stop 和 session.closed。该恢复会话未发送音频。[代理事件与网关日志摘录](pressure.json)
