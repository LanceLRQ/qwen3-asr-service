# Qwen3-ASR Service 配置文档

**中文** | [English](configuration_EN.md)

服务配置共四层，优先级从低到高：

```
内置默认值  <  环境变量  <  配置文件 config.yaml  <  命令行显式参数
```

同一参数高层覆盖低层；命令行**显式传入**的值永远最高（包括显式传默认值，如 `--device auto`）。

## 目录

- [启动参数（完整表）](#启动参数完整表)
- [配置文件（config.yaml）](#配置文件configyaml)
- [环境变量](#环境变量)
- [离线任务持久化（tasks.db）](#离线任务持久化tasksdb)
- [说话人分离与声纹库（speakers.db）](#说话人分离与声纹库speakersdb)
- [vLLM 原生流式模式](#vllm-原生流式模式)
- [内置常量（app/config.py）](#内置常量appconfigpy)

---

## 启动参数（完整表）

所有参数通过 `bash start.sh <参数>` 透传给服务；同名配置文件键 = 长参数横线转下划线（如 `--model-size` → `model_size`，唯一例外：`--use-punc` → `use_punc`）。

### 基础

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--serve-mode` | `standard` / `vllm` | `standard` | 运行模式；`vllm`=vLLM 原生流式（GPU 专用，partial→final 实时 + 离线 `/v2/asr`） |
| `--device` | `auto` / `cuda` / `cpu` | `auto` | 运行设备，`auto` 自动检测（≥6GB 显存选 1.7B，4–6GB 选 0.6B，<4GB 关对齐，无 GPU 回退 CPU/OpenVINO） |
| `--model-size` | `0.6b` / `1.7b` | 按显存自动选择 | ASR 模型大小 |
| `--enable-align` / `--no-align` | - | 开启 | 对齐模型（单词级时间戳）；CPU 模式强制关闭 |
| `--use-punc` / `--no-punc` | - | 关闭 | 标点恢复 |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | 模型下载源（国内推荐 modelscope） |

### 服务

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--host` | IP 地址 | `127.0.0.1` | 监听地址，`0.0.0.0` 可局域网访问 |
| `--port` | 端口号 | `8765` | 监听端口 |
| `--web` / `--no-web` | - | 关闭 | Web UI（`/web-ui` 离线演示页、`/web-ui/stream` 实时测试页、`/web-ui/docs` 文档中心） |
| `--api-key` | 字符串 | 无 | API 密钥，设置后启用 Bearer Token 认证（覆盖 `ASR_API_KEY` 环境变量） |
| `--max-segment` | 秒数 | `5` | VAD 切片合并最大时长 |
| `--max-queue-size` | 数字 | `100` | 离线任务队列最大长度 |

### 实时转写

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--enable-stream` / `--no-stream` | - | 关闭（example 生成的配置中开启） | 挂载实时端点 `WS /v2/asr/stream`（standard 模式） |
| `--max-stream-sessions` | 数字 | `16` | 实时最大并发会话数（超额连接以 1013 关闭） |
| `--stream-asr-concurrency` | 数字 | `1` | 实时 ASR 解码并发上限（模型层有推理锁，>1 无收益） |

### 智能远场过滤

减少远场声音与环境音造成的误触发。`--vad-speech-noise-thres` 调高 VAD 灵敏度（离线+实时统一）；`--stream-noise-filter` 开启实时段级能量/SNR 门控（仅实时，默认关）。

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--vad-speech-noise-thres` | 浮点 | `0.6` | FSMN-VAD 语音/噪声判决阈值（离线+实时统一）；调高更激进过滤远场/弱帧，建议 `0.6`–`0.8` |
| `--stream-noise-filter` / `--no-stream-noise-filter` | - | 关闭 | 实时段级能量/SNR 门控总开关（opt-in） |
| `--stream-energy-floor-dbfs` | 浮点 | `-50.0` | 绝对能量门（dBFS，满量程参考）：段响度低于此丢弃 |
| `--stream-snr-min-db` | 浮点 | `6.0` | 自适应信噪比门（dB）：段相对会话噪声底不足此值丢弃；`<=0` 关闭该门 |

### 任务持久化

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--enable-task-store` / `--no-task-store` | - | 关闭（example 生成的配置中开启） | 离线任务持久化（结果跨重启可查） |
| `--task-db-path` | 路径 | `data/tasks.db` | 任务库路径（相对服务根目录） |
| `--task-retention-days` | 天数 | `7` | 过期任务清理窗口，启动时执行；`0` = 永不清理 |

### 说话人分离

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--enable-speaker` / `--no-speaker` | - | 关闭 | 说话人分离：离线 `segments[].speaker` / 实时 `final.speaker`（匿名 A/B/C…）；CAM++ 模型 28MB 首次自动下载，CPU 推理不占显存 |
| `--speaker-threshold` | 0–1 | `0.5` | 实时在线归簇余弦阈值（推荐区间 0.35–0.65；调高更易分人、调低更易并人） |
| `--speaker-max` | 数字 | `8` | 说话人数上限（实时硬上限；离线谱聚类簇数搜索上界） |
| `--speaker-min-seg-ms` | 毫秒 | `1500` | 实时短段门槛：短于此的段不建新簇/不更新质心（声纹特征在 ≥1.5s 才稳定） |
| `--speaker-max-windows` | 数字 | `4000` | 离线滑窗数上限，超出均匀抽稀（超长音频聚类内存防护） |

### 声纹库

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--enable-speaker-db` / `--no-speaker-db` | - | 关闭 | 声纹库（登记 + 真名识别）：依赖 `enable_speaker` 且**必须配置 `api_key`**（声纹属生物识别信息，不允许无鉴权访问，否则模块自动降级关闭） |
| `--speaker-db-path` | 路径 | `data/speakers.db` | 声纹库路径（相对服务根目录）；**数据永不自动清理** |
| `--speaker-id-threshold` | 0–1 | `0.45` | 1:N 开集识别阈，最高相似度低于此判 unknown |
| `--speaker-id-margin` | 0–1 | `0.10` | top1-top2 margin，差距小于此判 unknown（近邻打架宁缺勿错） |
| `--speaker-enroll-min-sec` | 秒 | `3.0` | 手动登记单样本最短有效语音（VAD 后） |
| `--speaker-auto-enroll` / `--no-speaker-auto-enroll` | - | 开启 | 离线识别未命中的说话人自动以「说话人_NN」登记（**开启 = 部署方声明已获数据主体同意**） |
| `--speaker-auto-enroll-min-sec` | 秒 | `10.0` | 自动登记的簇最短语音总时长（严于手动登记，降低噪声建档），离线/实时自动登记共用 |
| `--stream-speaker-auto-enroll` / `--no-stream-speaker-auto-enroll` | - | 关闭 | 实时识别未命中的说话人自动登记（默认关；**开启 = 部署方声明已获同意**）。无论开关，客户端始终可经 WS `enroll` 消息显式登记，详见 [转写 API](api/v2/transcription.md#客户端--服务端) |
| `--speaker-store-audio` / `--no-speaker-store-audio` | - | 关闭 | 留存登记样本音频到 `data/speaker_audio/`（扩大合规面，默认关） |

> **回传声纹 uuid**：请求级开关（无需服务端配置）——实时 `start` 带 `return_speaker_id:true` → `final.speaker_id`；离线表单 `return_speaker_id=true` → `segments[].speaker_id`。供客户端跨会话记忆声纹。

### 音频标注（通用音频事件标注 + 派生场景）

开启后复用同一路音频额外输出 **AudioSet 通用事件标注**（PANNs 527 类 / YAMNet 521 类）与
**派生场景**（`silence`/`speech`/`singing`/`music`/`other`）：离线结果加 `audio_events` 事件段、
每段主场景 `segments[].scene` 与各桶概率分布 `segments[].scene_scores`；实时流推 `scene` 消息、
并在每条 `final` 句级结果附 `scene`/`scene_scores`；另有 `POST /v2/audio/tag` 只打标不转写。
全程 opt-in + 惰性加载 + 失败降级，关闭时零侵入。

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--enable-audio-tagging` / `--no-audio-tagging` | - | 关闭 | 总开关；开启后离线/实时附带音频事件标注与场景 |
| `--audio-tagging-engine` | `panns` \| `yamnet` | `panns` | 引擎：**panns**（推荐，权重 ~320MB 首次自动下载）/ **yamnet**（轻量备选，需 `pip install -r requirements-yamnet.txt`，仅 standard 模式 CPU 可用） |
| `--audio-tagging-panns-variant` | `16k` \| `32k` | `16k` | PANNs 变体：16k 原生（Zenodo 直链）/ 32k（HF `nicofarr` + 重采样） |
| `--audio-tagging-topk` | 数字 | `5` | 对外返回的 top-K 标签数 |
| `--audio-tagging-interval-ms` | 毫秒 | `960` | 推理窗步长（降频省算力） |
| `--scene-enable` / `--no-scene` | - | 开启 | 输出派生场景；关闭则只给原始 `audio_events` 标签 |
| `--scene-preset` | `balanced` \| `live` \| `music` | `balanced` | 场景判定预设（打包权重）：**balanced** 均衡人声优先 / **live** 直播（人声优先+清唱偏置）/ **music** 音乐优先。可经 WebUI 下拉、离线 `/v2/asr`·`/v2/audio/tag` 表单 `scene_preset`、实时 `start` 消息按请求/会话覆盖 |
| `--scene-map-file` | 路径 | （内置 5 桶） | 自定义场景映射 yaml/json：`{桶: [AudioSet 类名, ...]}`；加载失败回退内置默认 |
| `--scene-enter-sec` | 秒 | `2.0` | 迟滞（流式连续 `scene` 消息）：连续 N 秒判定才进入某场景 |
| `--scene-exit-sec` | 秒 | `2.0` | 迟滞（流式连续 `scene` 消息）：连续 M 秒判定才退出当前场景 |
| `--scene-silence-dbfs` | dBFS | `-50.0` | 静音判定能量底；仅在**无明确语音/演唱信号**时据此判 `silence` |
| `--scene-singing-min` | 数字 | 随预设 | 演唱判定阈值（覆盖预设；留空=随预设） |
| `--scene-singing-bias` | 数字 | 随预设 | 清唱偏置：演唱与说话竞争时给演唱加的分（覆盖预设） |
| `--scene-lyrics-aware` / `--no-scene-lyrics-aware` | - | 开启 | 离线/实时逐句：用转写歌词作人声证据修正歌声（带伴奏歌声 PANNs 常只给 `music`） |
| `--scene-speech-min` | 数字 | `0.30` | 文本感知判别阈：有歌词段 `speech` 分≥此值判说话，否则有伴奏判演唱 |
| `scene_weights`（仅配置文件 dict） | `{桶: 乘数}` | （全 1.0） | 每桶权重乘数，如 `{music: 0.8, speech: 1.1}`；同时作用于场景判定与 `scene_scores` |

> **场景判定模型**：`scene` 是**持续的主导内容状态**（互斥）；掌声/笑声/狗叫等**瞬时事件**不进
> `scene`，统一进 `audio_events`。判定为**人声优先**——说话/演唱只要达阈值就压过背景音乐，纯器乐
> 才归 `music`（`music` 预设回退为按桶分 argmax）。段级按窗与段的**时间重叠加权**聚合，规避
> 「说话结束后 BGM 恢复」被跨界窗污染。`scene_scores` 为各桶**独立置信度**（不归一到 1，体现
> 「说话+背景音乐」并存）。
>
> **演唱识别局限**：PANNs 对**带伴奏的歌声**常只输出 `Music`、不给 `Singing`（演唱桶分接近零），
> 调阈值/权重救不回；故离线与实时逐句借 **ASR 已转写歌词＝确有人声** 这一事实，按 `--scene-speech-min`
> 区分说话/演唱（`--no-scene-lyrics-aware` 可关）。实时**连续 `scene` 消息**无逐段文本，仍按模型分
> 判定，对带伴奏歌声可能仍判 `music`；逐句场景请用 `final.scene`。
>
> YAMNet 为非推荐轻量备选（精度低于 PANNs、vLLM 模式不可用），限制详见 README。

### vLLM 原生流式（仅 `--serve-mode vllm`）

仅 vllm 模式生效；要求 CUDA GPU，须独立环境/镜像（见下方 [vLLM 原生流式模式](#vllm-原生流式模式)）。

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--gpu-memory-utilization` | 0–1 | `0.6` | vLLM 显存占用率（×总显存为预算；单流 ASR 无需 0.8） |
| `--vllm-max-model-len` | 数字 | `32768` | 最大上下文长度；过大会抬高 KV cache 下限，致低占用率起不来 |
| `--vllm-chunk-size-sec` | 浮点 | `1.0` | 流式解码块大小（秒），越小 partial 越细腻（范围 0.5–5） |
| `--vllm-max-utterance-sec` | 数字 | `20` | 单句兜底切分（秒），约束上下文/显存增长 |
| `--vllm-concurrency` | 数字 | `1` | 同时解码会话数（generate 串行，>1 无吞吐收益） |
| `--vllm-end-silence-ms` | 毫秒 | `800` | 能量端点尾静音判停阈值 |
| `--vllm-enable-align` / `--no-vllm-align` | - | 开启 | 离线 `/v2/asr` 词级时间戳：加载对齐模型（关闭省显存、无 words） |
| `--vllm-align-device` | `cuda` / `cpu` | `cuda` | 对齐器加载设备；其显存**在 `gpu_memory_utilization` 预算之外**，长音频对齐 OOM 时改 `cpu`（float32，慢但无 GPU 争用） |
| `--vllm-infer-batch-size` | 数字 | `4` | 一次对齐/ASR 的音频块数（块 ≤180s）；`-1`=全部一次（长音频对齐前向激活叠加易 OOM），调小省显存、长音频仍 OOM 可降到 `1` |
| `--vllm-segment-gap-ms` | 毫秒 | `500` | 离线分段：相邻词间隙 > 此值断句（无 FSMN，以词间隙替代） |

**仅配置文件可设（无对应 CLI，写入 `config.yaml`）：**

| 配置键 | 默认值 | 说明 |
|--------|--------|------|
| `vllm_unfixed_chunk_num` | `2` | 流式起始不拿历史当前缀的块数（冷启动稳定） |
| `vllm_unfixed_token_num` | `5` | 起始块之后回滚末 K token 当前缀（降抖动） |
| `vllm_energy_floor_dbfs` | `-45.0` | 流式能量端点门限（dBFS），高于此判为语音/句开始 |
| `vllm_offline_chunk_sec` | `180` | 离线逐块转写切块时长（秒），调小=进度更细、峰值显存更省（详见下方[长音频与进度](#vllm-原生流式模式)） |

### 配置文件元参数

| 参数 | 说明 |
|------|------|
| `--config <PATH>` | 显式指定 YAML 配置文件（文件不存在则启动报错） |
| `--no-config` | 跳过配置文件加载与引导生成（纯默认值 + 环境变量 + 命令行，排障用） |
| `--update-config` | **仅更新本地配置后退出，不启动服务**：把 `config.example.yaml` 里 `config.yaml` 缺失的**推荐项**追加进去（只补不覆盖、保留既有值） |
| `--all` | 配合 `--update-config`：连**高级/可选项**一并补入（按注释态写入，即 `# 键: 默认值`，随时可取消注释）；默认只补推荐项 |

## 配置文件（config.yaml）

启动参数可通过 YAML 配置文件统一管理，不必每次写一长串命令行。

### 自动发现与引导生成

```bash
# 默认行为：自动加载 asr-service/config.yaml（支持 config.yml 别名）；
# 首次启动若不存在，会自动从 config.example.yaml 拷贝生成一份可编辑的 config.yaml
bash start.sh

# 显式指定配置文件
bash start.sh --config /path/to/my-config.yaml

# 命令行参数临时覆盖配置文件（只影响本次启动，不改文件）
bash start.sh --device cpu

# 跳过配置文件
bash start.sh --no-config
```

- 扫描目录为服务根目录（`asr-service/`），`config.yaml` 优先于 `config.yml`（并存时告警并取 `.yaml`）。
- **删除 `config.yaml` 后重启 = 重置配置**（重新由 example 生成默认配置）。
- 引导生成的 `config.yaml` 权限为 `600`（该文件可能写入 `api_key`）。
- **同步缺失项（`--update-config`）**：`--update-config` 是一个**独立的维护命令**——只更新配置文件，完成后**直接退出，不启动服务**。它把 `config.example.yaml` 里目标配置**缺失的项**追加进去，**只补不覆盖**：既有值与你已注释/声明的键一律不动。追加的行**去掉行内注释、不加额外标记**，保持 `config.yaml` 简洁。
  - **默认只补推荐项**：即 example 里**激活（未注释）**的项，以 `键: 默认值` 形式补入。
  - **`--all` 连高级项一起补**：example 里**注释掉的高级/可选项**也补，但保持注释态（`# 键: 默认值`，禁用 + 默认值引用，随时可取消注释启用）——避免把「启用时的推荐值」误当默认值写成激活态。
  - 目标文件优先级：`--config` 指定文件 > 自动发现的 `config.yaml`/`config.yml`；本地都不存在时由 example 引导生成。`--update-config` 与 `--no-config` 互斥。

  ```bash
  # 升级后补齐新配置项（更新即退出，不起服务）
  bash start.sh --update-config           # 只补推荐项
  bash start.sh --update-config --all     # 连高级/可选项也补（注释态）
  ```

### 格式与校验

- 仅支持 YAML，顶层为扁平键值映射；全部可配键见 [`asr-service/config.example.yaml`](../asr-service/config.example.yaml)。
- **启动时硬校验**：未知键（带近似拼写提示）、空值、类型错误、取值越界、重复键均直接报错退出，防止拼写错误静默生效；多处错误一次性全部报出。
- 布尔开关在配置文件设 `true` 后，命令行可用反向参数覆盖（`--no-punc` / `--no-web` / `--no-stream` / `--no-align` / `--no-task-store` / `--no-speaker` / `--no-speaker-db` / `--no-speaker-auto-enroll` / `--no-stream-speaker-auto-enroll` / `--no-speaker-store-audio`）。

### 安全

- `config.yaml` / `config.yml` 已加入 `.gitignore`，请勿提交（可能含 `api_key`）。
- `GET /health` 的 `config_file` 字段回显本次生效的配置文件名，便于确认加载来源（防"幽灵配置"）。

## 环境变量

| 变量 | 对应配置键 | 说明 |
|------|-----------|------|
| `ASR_API_KEY` | `api_key` | API 密钥；优先级低于配置文件与命令行（配置文件中 `api_key: ""` 也会覆盖它——想用环境变量请删除该行） |
| `MODEL_SOURCE` | `model_source` | 模型下载源 |

空值环境变量视为未设置。

## 离线任务持久化（tasks.db）

默认（内置默认值）任务只存在内存：终态结果保留 1 小时，重启后全部丢失。开启任务持久化后，任务元数据与最终结果写入 `asr-service/data/tasks.db`（SQLite），跨重启可查。

```yaml
# config.yaml（由 config.example.yaml 生成的配置默认已开启）
enable_task_store: true
# task_db_path: data/tasks.db
# task_retention_days: 7    # 过期清理窗口（天）；0 = 永不清理
```

### 行为说明

- **结果可查，不做断点续跑**：重启时上次未完成（`pending` / `processing`）的任务标记为 `failed`（`error: "service restarted"`），不会自动重跑。
- **过期清理仅在服务启动时执行**：终态超过 `task_retention_days` 天的记录被删除并回收空间。
- 历史任务的查询与删除接口见 [API 文档 · 任务持久化对 API 的影响](api/v2/tasks.md#任务持久化对-api-的影响)。
- 只保存文本结果与元数据，**不留存音频原件**；持久化写入失败只告警，不影响任务执行。
- 删除 `data/tasks.db` = 清空历史记录，不影响服务功能。对内容留存有更严格要求时，调小 `task_retention_days` 或关闭开关。

## 说话人分离与声纹库（speakers.db）

```yaml
# config.yaml：开启分离（匿名标签）
enable_speaker: true

# 进一步开启声纹库（真名识别）——必须同时配置 api_key
enable_speaker_db: true
api_key: "sk-your-key"
```

### 行为说明

- **分离**：单文件/单会话作用域的匿名标签 `A`/`B`/`C`…，跨任务不保证同人同标签；任何环节失败只丢标签，转写不受影响。
- **声纹库降级矩阵**：以下任一条件不满足时模块自动降级关闭（ERROR 日志 + `/v2/speakers*` 返回 503，服务正常启动）：① `enable_speaker` 开启且引擎加载成功；② `api_key` 非空；③ 建库成功。库内模板与当前引擎 `model_tag` 不一致时仅禁用登记/识别，查看与删除保留（被遗忘权）。
- **数据永不自动清理**：`speakers.db` 无 TTL（与 tasks.db 的 7 天清理不同）——声纹是长期积累资产，越用识别越准；唯一删除途径为 `DELETE /v2/speakers/{id}`（硬删除 + 物理回收）或删除库文件。
- **自动登记**：开启 `identify_speakers` 的离线转写中，未命中库且语音足量（默认 ≥10s）的说话人自动登记为「说话人_NN」，在 `/web-ui/speakers` 或 `PATCH /v2/speakers/{id}` 改名后，后续转写直接显示真名；实时路径不自动登记（在线聚类漂移易重复建档）；已命中库的说话人也不会自动追加模板（防样本投毒），补充样本需手动调用 `POST /v2/speakers/{id}/templates`。
- **合规**：登记接口强制 `consent=true` 双保险（接口 + 库约束）；开启自动登记即部署方声明已获数据主体同意；默认不留存音频；审计日志随库落盘。**备份 = 拷贝 `data/speakers.db` 单文件**（建议随常规备份计划），删库即彻底清除全部声纹数据。

## vLLM 原生流式模式

`--serve-mode vllm` 启用 vLLM 原生流式引擎，提供**逐句渐进（partial→final）**的实时转写，并提供与 `standard` 同契约的**离线 `/v2/asr`**；与默认 `standard` 模式（在线 VAD + 离线解码，按句 final）**互斥启动**。

**能力差异**

| 维度 | standard（默认） | vllm |
|------|-----------------|------|
| 接口 | 离线 v1/v2 + 实时 WS（`--enable-stream`） | **离线 v1/v2 + 实时 WS `/v2/asr/stream`（恒开）** + `/health` `/capabilities` |
| 增量结果（实时） | 无（按段 final） | **有 partial→final** |
| 词级时间戳 | 支持 | **离线支持**（ForcedAligner，默认开）；实时不支持 |
| 说话人分离/识别 | 支持 | **离线支持**（CAM++，需 `--enable-speaker`；滑窗用能量 VAD）；实时不支持 |
| 标点 | CT-Transformer（可关） | 模型原生（**不可单独关**） |
| 离线分段边界 | FSMN-VAD | 标点优先 / 整文兜底（边界较粗） |
| 设备 | GPU / CPU | **仅 CUDA GPU**（直接禁用 CPU，无 CPU 容器） |
| 吞吐 | 多会话并发 | 单流串行（generate 串行，吞吐 ≈ standard） |
| 依赖 / 镜像 | funasr + OpenVINO… | 独立 vLLM 环境（不含 funasr/OpenVINO），独立镜像 |

**为何独立环境**：vLLM 强绑定特定 torch/CUDA（与 standard 的 torch 不可共存），故须独立 `venv-vllm` 或独立镜像（`docker/Dockerfile.vllm`，基于 vLLM 官方镜像派生）。详见[部署文档](deployment.md)。

**离线转写（`/v2/asr`）**：vllm 模式离线复用与 standard **完全一致的异步任务契约**（`POST /v2/asr` 返回 `task_id` → 轮询 `GET /v2/tasks/{id}`、持久化、取消），ASR 走 vLLM 批量 `transcribe`。与 standard 的差异均为**质量差异、不破坏 result 结构**，并以 `result.warnings` 标注：
- **分段**：模型原生标点优先断句（句末 `。！？；` 切句，超 `--max-segment` 的长句在逗号处细切），词级时间戳仅用于定位 start/end；无对齐器时退化为词间隙（`--vllm-segment-gap-ms`，默认 500ms）/ 整文单段。边界精度低于 FSMN-VAD。
- **标点**：Qwen3-ASR 模型原生输出（已含标点），无法单独关闭；请求 `with_punc=false` 仅记入 `warnings`。
- **词级时间戳**：`--vllm-enable-align`（默认开）经 ForcedAligner 产出，与 standard 同款；`--no-vllm-align` 可关以省显存。
  - ⚠️ **长音频对齐 OOM**：对齐器是主进程内的独立 transformers 模型，其显存**不计入 `gpu_memory_utilization`**（该参数只约束 vLLM EngineCore 子进程）。`transcribe` 内部按 ≤180s 切块，但默认（`max_inference_batch_size=-1`）会把一个文件的**全部块一次性**喂对齐器前向——长音频（如 30 分钟≈10 块）激活叠加即 `CUDA out of memory`（短音频只 1 块、不受影响）。对策（按推荐序）：① **`--vllm-infer-batch-size`**（默认已改为 `4`）逐批对齐，峰值显存随批大小线性下降，仍在 GPU、最快；长音频仍 OOM 则降到 `1`；② `--vllm-align-device cpu` 把对齐器移到 CPU（无 GPU 争用，稳但慢）；③ 降 `--gpu-memory-utilization` 留更多 GPU 余量；④ `--no-vllm-align` 放弃词级时间戳。
- **长音频与进度**：离线对超过 `vllm_offline_chunk_sec`（默认 180s）的音频按静音边界**逐块转写**，转写进度（0.1→0.85）随块实时更新、并可在块间响应取消（短音频整段直转）。块由 qwen_asr 同款切法产生、拼接=原音频，质量与整段一致；调小 `vllm_offline_chunk_sec` 可让进度更细、峰值显存更省。
- **说话人分离/识别**：`--enable-speaker`（+ 声纹库 `--enable-speaker-db`）后离线 `segments[].speaker` / `speaker_name` / `speakers` 字段与 standard 一致；引擎为 CAM++（CPU、torch，非 funasr），**滑窗语音区间用能量 VAD 替代 FSMN-VAD**（边界较粗）。未开启时请求 `diarize`/`identify_speakers` 记入 `warnings`。需额外依赖 `scipy`/`scikit-learn`/`modelscope`（或预挂 CAM++ 模型目录），见 [requirements-vllm.txt](../asr-service/requirements-vllm.txt)。实时流式仍无说话人。
> 需要 FSMN 精分段 / CT-Transformer 标点 / 实时说话人的高保真，请用 `standard` 模式。

**兼容接口（`/compat/*`）**：vllm 模式同样支持 OpenAI / DashScope 兼容接口，开关与 standard 一致（`--enable-openai-api` / `--enable-dashscope-api`）；接口文档见 [开发文档](development.md)。与 standard 的差异：
- **离线兼容**（OpenAI `audio/transcriptions`·`models`、DashScope 录音文件识别）复用 vLLM 离线 pipeline，故分段/标点/说话人质量同上节所述；`audio/translations` 维持 501（服务仅 ASR，与 standard 天然对齐）。
- **实时兼容**（OpenAI `WS /realtime`、DashScope `WS …/inference`）随兼容开关一并挂载（vLLM 流式恒开，**无需** `--enable-stream`）；vLLM 的逐字 partial 增量已经兼容协议下发（`capabilities.compat.realtime_partial=true`）：DashScope 走中间 `result-generated`（`sentence_end=false`，与其累计语义天然契合）；OpenAI 走 `…transcription.delta`，因 OpenAI 要增量片段而 vLLM partial 为累计且可修订，故为 **best-effort**（仅纯追加帧取新增后缀作 delta，修订帧跳过；权威全文始终以 `…completed` 为准）。
- DashScope file_urls 服务端下载需 `httpx`（已含于 requirements-vllm），SSRF 守卫与 `--compat-fetch-*` 参数沿用 standard。
- `/capabilities` 的 `compat` 段如实反映已挂端点：`{openai, dashscope, realtime, realtime_partial}`。

**启动**

```bash
# 本地：先装独立 venv-vllm（仅 requirements-vllm.txt，自带 vllm/torch），再启动
bash asr-service/setup.sh --vllm                                          # 建 venv-vllm 并装依赖
QWEN_VENV=venv-vllm bash asr-service/start.sh --serve-mode vllm --model-size 0.6b --web
# 或直接用 venv-vllm 解释器：
asr-service/venv-vllm/bin/python -m app.main --serve-mode vllm --model-size 0.6b --web

# Docker（独立镜像，独立端口 8766）
docker compose -f docker/docker-compose.vllm.yml up -d

# 交互式：bash manage.sh → Venv 方式（启动模式选 vllm）/ Compose 方式（切 vLLM 编排）
```

**注意**

- **进程模型**：vLLM 引擎在独立 EngineCore 子进程持有 GPU；服务**固定单 worker**（uvicorn 默认 workers=1，禁用多 worker，否则各 worker 重复加载模型必爆显存）。
- **优雅停止**：进程退出时 EngineCore 子进程可能不随父进程立即回收，Docker 中由容器停止统一收割；手动运行建议 `pkill -f "serve-mode vllm"` 后以 `nvidia-smi` 确认显存释放。
- **模型**：加载 HF 全精度 `models/asr/0.6b` 或 `1.7b`（非 OpenVINO 量化变体）；Web 演示页（`--web` → `/web-ui/stream`）已内置 partial→final 实时渲染。

## 内置常量（app/config.py）

不走启动参数/配置文件的内置限制（修改需直接编辑 `app/config.py`）：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| MAX_AUDIO_DURATION | 14400s | 最大音频时长（4 小时） |
| MAX_AUDIO_FILE_SIZE | 1024MB | 最大上传文件大小 |
| MIN_AUDIO_DURATION | 1.0s | 最短音频时长 |
| TASK_TIMEOUT | 1800s | 单任务超时（30 分钟） |
| TASK_RESULT_TTL | 3600s | 内存中终态任务保留时长（持久化历史不受此限） |
| STREAM_MAX_SESSION_SECONDS | 3600s | 实时单会话最长时长 |
| STREAM_MAX_FRAME_BYTES | 2MB | 实时单条二进制帧上限 |
| STREAM_MAX_BACKLOG_BYTES | 8MB | 实时处理积压上限（超限断开） |
