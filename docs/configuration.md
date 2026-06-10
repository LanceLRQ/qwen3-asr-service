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
- [内置常量（app/config.py）](#内置常量appconfigpy)

---

## 启动参数（完整表）

所有参数通过 `bash start.sh <参数>` 透传给服务；同名配置文件键 = 长参数横线转下划线（如 `--model-size` → `model_size`，唯一例外：`--use-punc` → `use_punc`）。

### 基础

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--serve-mode` | `standard` / `vllm` | `standard` | 运行模式；`vllm` 为占位，暂未实现（仅提供 /health /capabilities） |
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
| `--speaker-auto-enroll-min-sec` | 秒 | `10.0` | 自动登记的簇最短语音总时长（严于手动登记，降低噪声建档） |
| `--speaker-store-audio` / `--no-speaker-store-audio` | - | 关闭 | 留存登记样本音频到 `data/speaker_audio/`（扩大合规面，默认关） |

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
- 布尔开关在配置文件设 `true` 后，命令行可用反向参数覆盖（`--no-punc` / `--no-web` / `--no-stream` / `--no-align` / `--no-task-store` / `--no-speaker` / `--no-speaker-db` / `--no-speaker-auto-enroll` / `--no-speaker-store-audio`）。

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
