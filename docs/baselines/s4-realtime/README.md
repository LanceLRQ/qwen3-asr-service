# S4-04 远端整句实时验收

验收对象为 S4-01 提交 `9960a98`。18771 网关使用本地 PyTorch FSMN VAD 分句，将音频发给 18764 的真实 Qwen3-ASR 服务。三个协议共用一个会话名额，关闭 speaker 和网关鉴权。全部场景经过 18767 转发代理，仅慢响应场景注入延迟。[网关健康快照](health.json) · [逐事件记录](results.json)

## 功能结果

2026-11-20（上海时间），三种协议的两句自动断句、末句冲刷、断连恢复和慢响应共 12/12 项通过。每个两句用例实收两个有序 final，观测窗口内丢句 0、重复句 0。三个协议均无 partial，首个 partial 延迟不适用。远端句中增量的后续实测见 [S4-02 验收](../s4-upstream/README.md)。

另做 3 次慢响应期间断连。原生、OpenAI、DashScope 在中断后分别用 1、2、1 ms 重新握手，此时旧响应距代理转发仍分别有 1274、1266、1258 ms。新会话均成功转写，所有最终用例均未发生握手 403。这验证了网关释放槽位；上游推理资源何时回收未测量。[断连与代理时间记录](cancel.json)

## 性能结果

每个协议、每个场景各跑一次。表中延迟从合成音频片段末尾算到客户端读到 final，包含 VAD 所需的尾静音。墙钟 RTF 为首帧发送至最后结果或结束确认的耗时除以含静音的音频时长，包含客户端按实时速度发送音频的时间。

| 协议 | 正常两句 final（ms） | stop 末句（ms） | 慢响应两句 final（ms） | 两句墙钟 RTF（正常 / 慢） | 慢响应最大发送滞后（ms） |
|---|---:|---:|---:|---:|---:|
| 原生 | 881 / 892 | 200 | 2861 / 2896 | 1.001 / 1.118 | 2 |
| OpenAI | 855 / 898 | 209 | 2867 / 2929 | 1.000 / 1.119 | 3 |
| DashScope | 888 / 917 | 201 | 2860 / 2891 | 1.001 / 1.117 | 1 |

## 测试方法

- 两句：以每帧 100 ms 的实时速率发送 16 kHz 单声道 PCM。两个 FFmpeg flite 样本分别说 “The blue train leaves at noon” 和 “Please meet me at the station”。句间、句后各填充 2 s 静音，结束命令前必须收到两条 final，按顺序检查关键词 blue/train 和 station。
- stop：单独发送第一句，不追加静音。原生发 stop 并等 session.closed，OpenAI 发 input_audio_buffer.commit 并等 completed，DashScope 发 finish-task 并等 task-finished。OpenAI 的 commit 保留会话，额外观察 500 ms 检查重复 final。
- 断连：单句发送完立即中断 TCP，再用新会话转写并冲刷一句。记录从中断到新握手完成的时间。握手遇到 HTTP 403 时每 100 ms 重试，限时 5 s；若触发，原始记录包含次数。
- 慢响应：继续实时发送两句，每次真实上游转写的响应都额外延迟 2 s。需观察到两条代理记录和两个有序 final。记录客户端最大发送滞后和句末到 final 的时间。发送滞后衡量客户端是否跟上音频时钟；服务端队列峰值未采样。
- 原生积压超限（主进程独立验证）：同配置 18770 网关接收突发的 2 MiB PCM 帧，触发 8 MiB 积压守卫，依次返回 fatal backlog_overflow 和 session.closed，随后唯一槽位可重新连接。该项的输入速率和判据与 2 s 慢响应项不同。

## 复现

需要 Python 3.11+、websockets 15.0.1、带 flite 的 FFmpeg 和 Node.js。以下命令均在本目录执行。启动同配置的独占网关时设置上游地址为 `http://127.0.0.1:18767/v1`，代理转发到本机 18764。标记文件使用当前运行独占的路径，开始前应不存在。单独终端运行代理：

```sh
node slow-proxy.mjs /tmp/s404-delay > /tmp/s404-proxy.log
```

网关 ready 后运行：

```sh
python3 -m venv /tmp/s404-client
/tmp/s404-client/bin/pip install websockets==15.0.1
/tmp/s404-client/bin/python matrix.py --base ws://127.0.0.1:18771 \
  --slow-base ws://127.0.0.1:18771 --marker /tmp/s404-delay \
  --proxy-log /tmp/s404-proxy.log --out results.json
```

每行结果保留事件、相对于本次连接的毫秒时间、验收状态及失败原因。任一项失败时脚本退出码为 1。每个断连用例包含两次连接，事件时间分别从各自握手完成时起算。

复现慢响应期间断连时，在上述矩阵命令中增加 `--cancel-only` 并改用 `--out cancel.json`。脚本会在首句后发送 1.6 s 静音触发 VAD，断开连接后关闭延迟标记，立即重连，并核对新握手与旧响应的代理时间戳。

## 离线兼容对照

在修正无标点英文跨块拼接后，重新通过原生 HTTP 任务入口采集 standard 和远端同时开启对齐、说话人两种配置。沿用 [S3 基线](../s3-offline/README.md) 的短句、对话、静音、噪声和长音频，10 份任务的业务结果逐字段与 S3 一致，包含全文、分段、词时间和说话人。另以 CPU 服务运行 PocketSphinx，同一任务入口返回三段、18 个词时间，全文保留跨块单词间空格。[第二模型实测](../s4-second-model/README.md)
