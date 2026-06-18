# 目标
分别考虑两种情况：
P0. 高优先级，音频内容全都是单人讲解或多人对话，需要输出良好的说话人和分句（带标点符号）
P1. 音频含杂背景音乐，需要分离背景音乐并实现P0的目标
项目的整体思路应该如何调整（注意：forcedaligner对于长音频时间戳对齐不太友好，例如超过3分钟的音频时间戳就会漂移，而要处理的音频可能时长达8小时）
如有必要可以引入其他三方库或模型
给出合理的解决方案

----------------------------------------------------------------

# 方案

整体思路要从：

> **ASR 主模型 + forced aligner 补时间戳**

调整为：

> **先建立稳定全局时间轴，再在局部小片段内做 ASR、说话人、标点、句子和局部对齐。**

尤其 8 小时音频，不应该让任何 forced aligner 直接处理长音频。Qwen3-ForcedAligner 官方说明是支持 **最长 5 分钟语音**的任意单位时间戳预测，但你已经观察到超过 3 分钟可能漂移，所以生产上应把它当成“局部细化器”，而不是“全局时间戳来源”。([GitHub][1])

---

# 一、核心架构调整

## 1. 时间轴优先，而不是文本优先

长音频系统最重要的是维护一个**不可漂移的全局时间轴**。

建议把整段音频先转成统一格式，例如：

```text
original.wav
sample_rate = 16k / 24k
channels = mono
global_start_ms = 0
duration_ms = 8h
```

然后所有中间结果都必须带绝对时间：

```json
{
  "start_ms": 1234567,
  "end_ms": 1243210,
  "source": "vad",
  "confidence": 0.92
}
```

VAD、ASR、diarization、sentence、alignment 都只是往这条时间轴上叠加信息。

---

## 2. forced aligner 只能做局部对齐

不要做：

```text
8小时音频 + 整篇 transcript -> forced aligner
```

应该做：

```text
VAD小片段 / 句子片段 / 30秒以内音频 -> forced aligner
```

建议策略：

| 层级               |             建议长度 | 作用         |
| ---------------- | ---------------: | ---------- |
| 大任务块             |         10–30 分钟 | 便于并行、断点续跑  |
| VAD 语音块          |           5–30 秒 | ASR 主输入    |
| ASR 合并块          |          15–60 秒 | 保留上下文，避免太碎 |
| forced aligner 块 | 5–30 秒优先，最多 60 秒 | 只做局部时间戳细化  |
| 最终句子             |           1–20 秒 | 用户可读输出     |

即使 Qwen3-ForcedAligner 标称支持 5 分钟，也建议在你的系统里强制限制到 **30–60 秒以内**。它的输出还要被 VAD 边界和父片段边界约束，不能让它自由漂移。

---

## 3. 说话人、分句、时间戳三者分开算，再融合

不要把“说话人识别”和“ASR 句子切分”绑定死。

推荐三条支线并行：

```text
音频 -> VAD -> speech segments
音频 -> diarization -> speaker turns
音频 -> ASR + punctuation -> text / sentences
```

最后做一次融合：

```text
sentence ∩ speaker_turns ∩ word_timestamps
```

这样如果某个模块错了，可以局部重算，而不是整条链路报废。

---

# 二、P0：纯人声讲解/多人对话场景

P0 的目标是：

```text
输入：单人讲解 / 多人对话
输出：带时间戳、带说话人、带标点、分句良好的 transcript
```

推荐流水线如下。

---

## P0 流水线

```text
原始音频
  ↓
音频标准化
  ↓
VAD 切语音
  ↓
ASR 转写
  ↓
标点恢复 / 句子切分
  ↓
说话人分离
  ↓
局部 forced alignment
  ↓
句子-说话人-时间戳融合
  ↓
最终结构化结果
```

---

## 1. 音频标准化

这一步不要做过度降噪，只做工程标准化：

```text
ffmpeg 解码
统一采样率
统一声道
响度归一化
去掉明显 DC offset
记录原始时长和采样率
```

P0 场景下不要默认做强降噪，因为降噪可能损伤声纹特征，影响说话人识别。

---

## 2. VAD：先切出可靠语音块

可以继续用 **FunASR FSMN-VAD**。FunASR 的 VAD 模型支持 streaming/offline，支持配置最大单段时长，适合作为长音频预处理模块。([Hugging Face][2])

建议参数思想：

```text
max_single_segment_time = 15s ~ 30s
speech_pad = 200ms ~ 500ms
min_speech_duration = 300ms ~ 500ms
min_silence_duration = 300ms ~ 800ms
```

注意：VAD 的输出时间戳应作为**第一层硬锚点**。后面的 ASR 和 forced alignment 只能在 VAD 片段附近微调，不能推翻全局位置。

---

## 3. ASR：Qwen3-ASR 负责主转写

Qwen3-ASR 适合作为主 ASR 模型。官方说明中，Qwen3-ASR 支持流式/离线统一推理、长音频转写，并且其工具链支持 vLLM batch inference、异步服务、流式推理和时间戳预测。([GitHub][1])

但长音频不要一次性丢给 ASR。建议：

```text
VAD segments -> merge 到 15~60 秒 ASR chunks
每个 chunk 带 1~2 秒左右 overlap
ASR 后根据 overlap 做去重
```

合并时需要遵守：

```text
不要跨越太长静音
不要跨越明显说话人切换
不要超过模型稳定长度
保留 chunk_start_ms
```

输出后统一转换成：

```json
{
  "chunk_id": "asr_000123",
  "start_ms": 3600000,
  "end_ms": 3655000,
  "text": "今天我们主要讨论三个问题...",
  "asr_confidence": 0.87
}
```

---

## 4. 标点与分句：优先保留 ASR 标点，不足时再补

如果 Qwen3-ASR 输出标点质量足够，优先使用它的原始标点。

如果模型输出无标点或标点弱，可以加 **CT-Punc / CT-Transformer**。FunASR 的 CT-Punc 定位就是 ASR 后处理中的标点恢复模块，常用于无标点文本补逗号、句号、问号。([Hugging Face][3])

分句不要只按中文逗号/句号硬切，建议结合：

```text
标点
VAD pause
最大句长
语义完整性
说话人切换点
```

规则示例：

```text
强切：句号、问号、叹号、长静音 > 800ms、说话人切换
弱切：逗号、顿号、短静音 > 400ms
保护：数字、英文缩写、专有名词、金额、日期
```

---

## 5. 说话人分离：建议升级为独立 diarization 模块

FunASR 可以通过 `spk_model="cam++"` 给句子加说话人标签，官方教程里也有 Paraformer/Fun-ASR-Nano/SenseVoice 加 `cam++` 输出 speaker label 的用法。([ModelScope][4])

但如果你要求 **P0 高优先级**，我建议不要只依赖 FunASR 内置的 `spk_model="cam++"`。更合理的是：

```text
pyannote / NeMo diarization 负责“谁在什么时候说话”
CAM++ 负责声纹 embedding、说话人库、跨文件身份匹配
```

### 推荐选择

| 场景                  | 推荐                           |
| ------------------- | ---------------------------- |
| 离线高质量会议/访谈          | pyannote speaker diarization |
| 需要本地、可离线、方便与转写时间戳融合 | pyannote community-1         |
| 流式/实时 diarization   | NeMo Sortformer              |
| 需要说话人库、注册声纹、跨文件识别   | CAM++ embedding              |
| 中文工程集成优先、快速落地       | FunASR + CAM++ 先跑通           |

pyannote 的 `community-1` diarization pipeline 输入 16kHz mono audio，输出 speaker diarization，并强调更好的 speaker assignment/counting、与转写时间戳更容易 reconciliation、支持离线使用。([Hugging Face][5])

NeMo 的 diarization 文档把系统分成端到端 diarization 和级联 diarization；Sortformer 是端到端 diarization 模型，而级联系统由 VAD、speaker embedding、clustering、TS-VAD 等模块组成，并且级联系统在说话人数和会话长度上限制更少。([NVIDIA Docs][6])

所以你的工程上可以这样设计：

```text
默认：pyannote diarization
实时：NeMo streaming Sortformer
轻量/中文快速：FunASR CAM++
说话人库：CAM++ embedding
```

---

## 6. 句子与说话人融合

最终不要让 diarization 直接决定文本，也不要让 ASR 直接决定 speaker。

融合规则：

```text
每个 sentence 有 start_ms / end_ms
每个 speaker_turn 有 start_ms / end_ms / speaker_id
计算二者重叠比例
选择重叠最多的 speaker
如果一句话跨越 speaker change，则切成两句
如果存在 overlap speech，则允许多个 speaker 标签
```

示例：

```json
{
  "start_ms": 123400,
  "end_ms": 128900,
  "speaker": "SPEAKER_01",
  "text": "这个地方我们需要重新切分，否则时间戳会漂移。",
  "confidence": {
    "asr": 0.91,
    "speaker": 0.86,
    "alignment": 0.88
  }
}
```

---

## 7. 局部 forced alignment：只做精修，不做主定位

这一点是整个方案的核心。

建议：

```text
sentence_text + sentence_audio_window -> forced aligner
```

而不是：

```text
full_text + full_audio -> forced aligner
```

具体做法：

1. 先用 VAD/ASR chunk 得到句子大致时间。
2. 对每个句子向前后扩展 300–800ms。
3. 把这个小 audio window 和句子文本送入 Qwen3-ForcedAligner。
4. 得到字/词时间戳。
5. 如果 aligner 输出超出父窗口，直接 clamp。
6. 如果出现非单调、跳变、漂移过大，丢弃这次 alignment，回退到 VAD/ASR 时间。

建议限制：

```text
preferred_align_window <= 30s
hard_max_align_window <= 60s
absolute_forbidden > 180s
```

如果要做英文或多语种，也可以引入 WhisperX 作为备用对齐链路。WhisperX 本身就是为长音频转写、VAD cut & merge、forced phoneme alignment、word-level timestamps 设计的系统；论文说明它通过 VAD 和 phoneme forced alignment 解决长音频漂移、幻觉、重复和词级时间戳问题，并实现批量推理提速。([arXiv][7])

---

# 三、P1：含背景音乐的音频

P1 的目标是：

```text
输入：语音 + 背景音乐 / BGM / 伴奏 / 环境音乐
输出：分离背景音乐后，再实现 P0 的说话人、分句、标点、时间戳
```

P1 不应该简单地在 P0 前面加一个“降噪”。背景音乐不是普通噪声，应该分成两类处理。

---

## P1 核心调整：双轨处理

建议维护至少三条音频轨：

```text
original_track    原始音频，永远保留
speech_track      分离/增强后的人声轨，供 ASR/VAD 使用
music_track       背景音乐轨，可选保存
```

不要只保留清理后的音频。因为：

```text
ASR 更适合用 speech_track
说话人识别有时更适合用 original_track
人工回听必须用 original_track
质量评估要对比 original 和 speech_track
```

---

## P1 流水线

```text
原始音频
  ↓
音乐/噪声检测
  ↓
按需做人声/背景音乐分离
  ↓
生成 speech_track + music_track
  ↓
对 speech_track 跑 VAD / ASR / 局部对齐
  ↓
对 original_track 或 speech_track 跑 diarization
  ↓
融合结果
  ↓
输出 P0 结构化 transcript + 可选背景音乐轨
```

---

## 1. 先检测，不要全量强制分离

8 小时音频如果全量跑 Demucs/UVR，成本会很高，而且分离会带来 artifacts。

建议先做轻量检测：

```text
每 10~30 秒检测一次：
speech probability
music probability
SNR
overlap speech/music ratio
```

然后分三类：

| 类型          | 处理策略                     |
| ----------- | ------------------------ |
| 纯人声         | 直接走 P0                   |
| 人声 + 轻微背景音乐 | 轻量 speech enhancement    |
| 人声 + 明显 BGM | source separation        |
| 纯音乐         | 不送 ASR，只记录 music segment |
| 音乐 + 歌声     | 谨慎处理，避免把歌词当讲话            |

---

## 2. 背景音乐分离：Demucs / UVR / audio-separator

如果是明显 BGM，建议引入音乐源分离模型。

**Demucs** 是 Meta/Facebook Research 的音乐源分离模型，可以分离 drums、bass、vocals 和 accompaniment；v4 是 Hybrid Transformer Demucs，结合 waveform/spectrogram，并使用 Transformer。([GitHub][8])

工程上更方便的选择是 `audio-separator` 或 UVR 模型生态。`audio-separator` 是 Python 包，封装了 UVR 中常用的 MDX-Net、VR Arch、Demucs、MDXC 等模型，可以输出 vocals/instrumental 等 stem，也支持 denoising、de-reverb 等处理。([PyPI][9])

推荐策略：

```text
优先：audio-separator + MDX/UVR vocal model
备选：Demucs htdemucs / htdemucs_ft
目标：得到 speech/vocal stem + music/instrumental stem
```

注意：Demucs/UVR 原本更多面向“唱歌人声 vs 伴奏”分离，拿来做“讲话声 vs 背景音乐”通常可用，但不是完美。背景歌声、合唱、电视剧对白、掌声、混响都会带来误分离。

---

## 3. 分离后再做轻量 speech enhancement

分离后可以再用 DeepFilterNet 这类模型做轻量增强。DeepFilterNet 是低复杂度 full-band speech enhancement 框架，官方说明支持 48kHz full-band audio，并提供无 Python 依赖的预编译二进制和实时降噪插件。([GitHub][10])

但不要过度增强。

建议顺序：

```text
source separation -> light enhancement -> VAD/ASR
```

不要：

```text
heavy denoise -> diarization
```

因为强降噪和分离 artifacts 会改变声纹特征，可能让说话人聚类变差。

---

## 4. P1 中 diarization 要更谨慎

P1 的说话人识别建议同时尝试两条输入：

```text
original_track diarization
speech_track diarization
```

然后根据质量选择：

| 情况              | diarization 输入                 |
| --------------- | ------------------------------ |
| 背景音乐轻微          | original_track 更好              |
| 背景音乐很大          | speech_track 更好                |
| 分离 artifacts 明显 | original_track + VAD mask      |
| 说话人库匹配          | original_track 提取 embedding 优先 |

也就是说：

```text
ASR 优先用 speech_track
speaker embedding 不一定用 speech_track
```

这是 P1 很重要的工程细节。

---

# 四、针对 8 小时长音频的关键机制

## 1. 不要线性单任务，要任务图

8 小时音频必须做成可恢复任务图：

```text
Job
 ├── decode
 ├── detect_music
 ├── source_separation_chunks
 ├── vad_chunks
 ├── asr_chunks
 ├── diarization_chunks
 ├── global_speaker_clustering
 ├── local_alignment
 ├── sentence_fusion
 └── export
```

每个节点都应支持：

```text
断点续跑
失败重试
局部重算
版本记录
质量评分
缓存复用
```

---

## 2. 长音频 diarization 不能按块独立结束

如果把 8 小时切成 30 分钟，不能简单得到：

```text
chunk_1 SPEAKER_00
chunk_2 SPEAKER_00
chunk_3 SPEAKER_00
```

然后直接认为它们是同一个人。

正确做法是：

```text
每个 chunk 内先 diarization
抽取每个 speaker turn 的 embedding
跨 chunk 做全局聚类 / speaker linking
统一重命名 speaker_id
```

也就是最终 speaker id 需要全局重排：

```text
local_speaker_id -> global_speaker_id
```

CAM++ 在这里适合作为 speaker embedding 和说话人库组件。

---

## 3. 对齐结果必须有漂移检测

每次 forced alignment 后都要检查：

```text
是否超出父音频窗口
时间戳是否单调递增
首尾是否偏离 VAD 边界过大
词间 gap 是否异常
句子持续时间是否异常
是否出现大量 token 无法对齐
```

一旦异常：

```text
重试：缩短窗口
重试：扩大前后 padding
重试：换 WhisperX / stable-ts / MFA
失败：回退到 VAD + ASR chunk 时间
```

可以给每句一个质量标记：

```json
"timestamp_quality": "aligned | estimated | fallback"
```

不要假装所有时间戳都同等可靠。

---

# 五、推荐模型/工具组合

## P0 推荐组合

| 模块    | 首选                             | 备选                                         |
| ----- | ------------------------------ | ------------------------------------------ |
| VAD   | FunASR FSMN-VAD                | Silero VAD                                 |
| ASR   | Qwen3-ASR                      | Whisper large-v3 / SenseVoice / Paraformer |
| 标点    | Qwen3-ASR 原生标点                 | CT-Punc / CT-Transformer                   |
| 局部对齐  | Qwen3-ForcedAligner，限制 30–60 秒 | WhisperX / MFA / stable-ts                 |
| 说话人分离 | pyannote community-1           | NeMo Sortformer / FunASR CAM++             |
| 说话人库  | CAM++ embedding                | pyannote embedding                         |
| 推理服务  | vLLM 跑 Qwen3-ASR               | transformers / OpenVINO / ONNXRuntime      |

---

## P1 推荐组合

| 模块          | 首选                                    | 备选                                  |
| ----------- | ------------------------------------- | ----------------------------------- |
| 音乐检测        | lightweight audio classifier / 自训练分类器 | PANNs / YAMNet 类模型                  |
| 人声/音乐分离     | audio-separator + UVR/MDX 模型          | Demucs htdemucs / htdemucs_ft       |
| 语音增强        | DeepFilterNet                         | NeMo speech enhancement / ESPnet-SE |
| ASR         | Qwen3-ASR on speech_track             | WhisperX on speech_track            |
| diarization | original_track 与 speech_track 双路评估    | pyannote / NeMo / CAM++             |
| 对齐          | 仍然只做局部 forced alignment               | 失败回退到 VAD/ASR 时间                    |

---

# 六、建议的最终输出 schema

建议最终不要只输出 SRT，而是输出结构化 JSON，再生成 SRT/VTT/TXT。

```json
{
  "audio_id": "xxx",
  "duration_ms": 28800000,
  "tracks": {
    "original": "original.wav",
    "speech": "speech.wav",
    "music": "music.wav"
  },
  "segments": [
    {
      "segment_id": "seg_000001",
      "start_ms": 123400,
      "end_ms": 128900,
      "speaker": {
        "id": "SPEAKER_01",
        "name": null,
        "confidence": 0.86
      },
      "text": "这个地方我们需要重新切分，否则时间戳会漂移。",
      "words": [
        {
          "text": "这个",
          "start_ms": 123430,
          "end_ms": 123760,
          "confidence": 0.91
        }
      ],
      "quality": {
        "asr": 0.91,
        "alignment": "aligned",
        "speaker": 0.86,
        "music_interference": 0.12
      }
    }
  ]
}
```

---

# 七、落地优先级建议

## 第一阶段：先把 P0 做稳

先不要急着做背景音乐分离。

第一阶段目标：

```text
8小时纯人声音频
能稳定切块
能稳定 ASR
能稳定分句
能输出 speaker label
时间戳不大漂移
支持断点续跑
```

最小可用链路：

```text
FFmpeg
+ FunASR FSMN-VAD
+ Qwen3-ASR
+ CT-Punc 可选
+ pyannote 或 CAM++
+ Qwen3-ForcedAligner 局部对齐
```

---

## 第二阶段：再加入 P1 双轨处理

P1 不要改变 P0 主链路，而是在 P0 前面增加：

```text
music detection
source separation
speech enhancement
track selection
```

也就是：

```text
P1 = 音频清理/分离层 + P0
```

不要把 P1 和 P0 写成两套完全不同的系统。

---

# 八、最终建议

你的项目应该从“薄封装第三方模型”升级为：

> **长音频语音文档处理运行时。**

核心价值应该放在这些地方：

```text
1. 稳定全局时间轴
2. 长音频任务切块与断点续跑
3. VAD/ASR/diarization/alignment 的结果融合
4. 局部 forced alignment 与漂移检测
5. 全局 speaker 聚类和说话人库
6. P1 的双轨音频处理：original / speech / music
7. 统一 schema、质量评分、失败回退
```

最关键的一条原则是：

> **VAD/音频切块给出全局硬时间，forced aligner 只在短窗口里做局部细化。**

这样即使音频长达 8 小时，也不会因为某个 forced aligner 的局部漂移把整篇 transcript 的时间轴拖垮。

[1]: https://github.com/QwenLM/Qwen3-ASR "GitHub - QwenLM/Qwen3-ASR: Qwen3-ASR is an open-source series of ASR models developed by the Qwen team at Alibaba Cloud, supporting stable multilingual speech/music/song recognition, language detection and timestamp prediction. · GitHub"
[2]: https://huggingface.co/funasr/fsmn-vad?utm_source=chatgpt.com "funasr/fsmn-vad"
[3]: https://huggingface.co/funasr/ct-punc?utm_source=chatgpt.com "funasr/ct-punc"
[4]: https://modelscope.github.io/FunASR/tutorial.html "FunASR Tutorial"
[5]: https://huggingface.co/pyannote/speaker-diarization-community-1 "pyannote/speaker-diarization-community-1 · Hugging Face"
[6]: https://docs.nvidia.com/nemo-framework/user-guide/latest/nemotoolkit/asr/speaker_diarization/intro.html "Speaker Diarization — NVIDIA NeMo Framework User Guide"
[7]: https://arxiv.org/abs/2303.00747?utm_source=chatgpt.com "WhisperX: Time-Accurate Speech Transcription of Long-Form Audio"
[8]: https://github.com/facebookresearch/demucs "GitHub - facebookresearch/demucs: Code for the paper Hybrid Spectrogram and Waveform Source Separation · GitHub"
[9]: https://pypi.org/project/audio-separator/ "audio-separator · PyPI"
[10]: https://github.com/rikorose/deepfilternet "GitHub - Rikorose/DeepFilterNet: Noise supression using deep filtering · GitHub"
