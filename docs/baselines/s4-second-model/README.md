# S4-03 第二模型离线 HTTP 验收

上游独立进程用 FFmpeg 的 asr 滤镜调用 PocketSphinx。使用 Ubuntu 软件包 pocketsphinx-en-us 和 libpocketsphinx3，实测版本均为 0.8.0+real5prealpha+1-15ubuntu5，ffmpeg 为 7:6.1.1-3ubuntu5。英文声学模型、发音词典及语言模型均来自该机 /usr/share/pocketsphinx/model/en-us，服务代码显式指定三个路径。模型 ID 为 `pocketsphinx-en-us`。

## 复现

在仓库根目录运行。PYTHON 指向已安装项目依赖的解释器，MODELS 指向已有 FSMN、CAM++ 和 Qwen3-ForcedAligner 0.6B 权重的项目 models 目录。系统 ffmpeg 需包含 asr 和样本生成器使用的 flite 滤镜。

```sh
export PYTHON=/mnt/data/github/qwen3-asr-service/asr-service/venv/bin/python
export MODELS=/mnt/data/github/qwen3-asr-service/asr-service/models
export PYTHONPATH="$PWD/asr-service"
BASE=docs/baselines/s4-second-model
"$PYTHON" docs/baselines/s1-standard-gpu/samples.py /tmp/s403-input >/tmp/s403-samples.json
CUDA_VISIBLE_DEVICES='' "$PYTHON" -m uvicorn upstream:app --app-dir "$BASE" --host 127.0.0.1 --port 18779 &
server_pid=$!
trap 'kill "$server_pid"' EXIT
curl --retry 10 --retry-connrefused --retry-delay 1 -fsS http://127.0.0.1:18779/v1/models
"$PYTHON" "$BASE/capture.py" "$MODELS" /tmp/s403-input/dialogue.wav /tmp/s403-result.json http://127.0.0.1:18779/v1
```

GET /v1/models 公开模型 ID。POST /v1/audio/transcriptions 接收 multipart 的 model、file（WAV）和可选 language，返回 text。省略语言时使用英语，显式语言只接受 en。探针验证 zh 返回 400。这个本机样例面向可信的离线音频块，逐请求启动解码进程，不提供流式识别、自动语种识别、鉴权或原生词时间。

## 实测

探针通过项目原有 RemoteASREngine.load 发现模型，先上传完整对话，再通过 ASRPipeline.run 运行 FSMN 切块和远端转写。上游只返回文本。对同一份 9.765 秒的 S1 对话关闭和开启 diarize、with_words，各得到三段，全文一致。关闭时各段省略两个字段；开启时由 CAM++ 聚类及独立对齐器补充下表结果。两端均在 CPU 运行，探针确认可见 CUDA 设备数为零。[完整结果与样本 SHA-256](responses.json)

| 实际分块转写 | 参考差异 | 说话人 | 词数 | 首词起至末词止（秒） |
| --- | --- | --- | ---: | --- |
| the bullet train leaves at noon | blue → bullet | A | 6 | 1.23–2.75 |
| please meet me at the station | 一致 | A | 6 | 4.09–5.77 |
| i will bring the yellow band | bag → band | A | 6 | 7.22–8.58 |

忽略大小写和标点，这个样本的分块转写有 2 个替换、0 个插入、0 个删除，词错率为 2/18（11.1%）。整段直接上传时的结果另存于 direct_text，分块会改变解码上下文。18 个词时间均通过项目 valid_words 的文本覆盖、时间顺序和分段边界检查。样本没有逐词人工时间标注，无法据此计算时间误差。CAM++ 将两种合成音色都归到 A，漏分中间一轮。此处验证离线接口和处理组件复用，质量数字仅覆盖这一份英文合成对话。
