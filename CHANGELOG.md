# 更新日志（Changelog）

本项目所有重要变更记录于此。版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)，
发布版本号经由 git tag（去掉 `v` 前缀）注入镜像 `APP_VERSION`，体现在 `/openapi.json` 的 `info.version`。

## [2.4.0] - 2026-06-24

实时声纹登记 + 离线/实时回传 speaker_id（客户端跨会话记忆声纹）。

### 新增 / 改进
- **按请求回传 speaker_id**：表单 / 握手参数 `return_speaker_id`（纯请求开关，无服务端配置）——实时 `start` 开启后 `final` 结果附 `speaker_id`（需同时启用声纹识别），离线结果 `segments[].speaker_id`。回传的是声纹库 UUID，供客户端跨会话记忆同一声纹。
- **实时声纹登记**：客户端经 WebSocket `enroll` 消息显式登记当前会话说话人（`consent` 硬约束）→ `enroll.ack` 回传 `speaker_id`；服务端 `stream_speaker_auto_enroll` 自动登记开关（默认关）。`SpeakerService.enroll_cluster` 从会话质心建单模板，离线 auto / 实时共用。
- **WebUI**：实时页新增声纹登记面板（登记 + UUID 复制）；离线页新增 speaker_id 回传勾选。
- 离线两路（standard / vLLM）段级 `speaker_id` 同步贯通；`enroll` 经 `frame_q` 单一发送方，避免与 `final` 并发写 WS。

### 评审修复（多 Agent 代码评审）
- **登记输入限长**：文本帧 `enroll` 限大小（`STREAM_MAX_TEXT_BYTES`）+ `EnrollMsg.label/name` 限长，防超长串写入声纹库与队列无界增长。
- **短样本门槛**：显式 `enroll` 加 `SPEAKER_ENROLL_MIN_SEC` 时长门槛（对齐离线手动登记）拒短样本；`_spk_dur_ms` 仅累计 ≥ `min_seg` 的段（短段只挂靠不建簇），不灌水登记门槛与模板时长。
- **幂等守卫**：实时自动登记加会话级 `_auto_enrolled` 守卫，避免缓存失效重查后重复建档。
- **登记先查重**：显式 `enroll` 先 1:N 查重（`enroll_or_merge_cluster`），命中既有人则追加模板复用其 id（占位名自动改真名），避免重复模板撑裂 `id_margin` 致后续误判 unknown；`EnrollAck` 加 `matched_existing`。
- 接收侧用 `EnrollMsg` 校验（类型 / 限长），失败经队列回 `enroll_error`（保持单发送方）。

### 文档 / 测试
- 中英双语文档同步（`transcription` / `speakers` / `configuration`）。
- 新增 18 项单测；全量 **1036 通过**。

## [2.3.0] - 2026-06-21

通用音频事件标注（Audio Tagging）特性，以及原生端点语言归一化修复。

### 新增 / 改进
- **通用音频事件标注**：基于 AudioSet（PANNs 527 类 / YAMNet 521 类），并派生场景视图（静音 / 语音 / 歌唱 / 音乐 / 其它）。通过 `--enable-audio-tagging` 显式开启；未开启时零影响。
- **离线结果增强**：离线转写结果新增 `audio_events`（带 onset/offset 的事件分段）、每段主场景 `scene` 及各场景桶概率分布 `scene_scores`（如 `{speech:0.62, music:0.31}`，各桶独立置信度，体现「说话+背景音乐」并存）。`/v2/audio/tag` 的 `scene_timeline` 各段同样附 `scene_scores`。
- **段级场景重叠加权聚合**：段内各打标窗按其与该段的时间重叠比例加权后再判定/求 `scene_scores`，修复「说话结束后背景音乐恢复」被 ~1s 全局窗横跨、整窗拉高 `music` 的污染（短段尤甚）。
- **可调每桶权重** `scene_weights`（配置文件 dict，如 `{music: 0.8, speech: 1.1}`）：同时作用于场景判定与 `scene_scores`，背景音乐易盖过说话时可下调 `music`。
- **文本感知歌声修正**（离线）：PANNs 对「带伴奏的歌声」常只输出 `Music`、不给 `Singing`（演唱桶分接近零）。利用 ASR 已转写出歌词＝确有人声这一事实，对有歌词的段：`speech` 分 ≥ `--scene-speech-min`（默认 0.30）判说话，否则有伴奏判 `singing`——救回被识别成 music 的演唱段。`--no-scene-lyrics-aware` 可关闭。
- **实时场景推送**：`/v2/asr/stream` WebSocket 的 `scene` 消息（迟滞平滑，仅状态切换时发出，连续状态只发一次）；并在每条 `final` 句级结果上附 `scene` / `scene_scores`（逐句场景，与离线一致，复用窗级打标不额外占 GPU）。
- **新增标注端点**：`POST /v2/audio/tag`（仅标注，不做转写）。
- **双引擎**：PANNs（推荐，16k / 32k 变体）与 YAMNet（轻量备选，可选依赖，vLLM 模式不可用）。
- **可配置场景映射** `--scene-map-file`；新增 `THIRD_PARTY_NOTICES.md`。
- **新增调优与排错文档**（中英 `docs/troubleshooting`）：实时 / 离线断句与过滤链路差异、实时降噪门控（`stream_noise_filter` / `stream_snr_min_db` / `stream_energy_floor_dbfs`）与 `vad_speech_noise_thres` 取舍、按内容类型推荐配置、排错速查；首个实战案例＝含 BGM 的演唱段在实时被自适应 SNR 门误杀（离线无此门）。

### 修复
- **场景分类准确性（人声优先 + 预设）**：场景判定改为「人声优先」模型——主播开背景音乐说话归 `speech`、演唱归 `singing`，纯器乐才归 `music`，不再被泛化的 `Music` 标签淹没。静音判定改为内容感知，仅在能量低 **且** 无明确语音/演唱信号时才判 `silence`，修复短促/轻声台词被打标窗（≈1s）能量稀释而误判静音。新增场景预设 `--scene-preset`（`balanced` 均衡 / `live` 直播含清唱偏置 / `music` 音乐优先），打包好权重，支持部署默认 + 按请求覆盖（`/v2/asr`、`/v2/audio/tag` 表单参数 `scene_preset`）+ WebUI 下拉选择；`--scene-singing-min` / `--scene-singing-bias` 可单项微调。

### 评审修复（多 Agent 代码评审）
- **短尾窗导致 PANNs 崩溃**（必修）：末尾不足整窗的尾片（< 4960 采样 / 约 310ms）经 CNN14 五次时间池化被压成 0，触发 `RuntimeError`——离线 `try/except` 静默丢弃整条音频的标注、`/v2/audio/tag` 返回 500。`predict_window` 对短输入补零到模型最小采样后修复，并补真实 CNN14 短窗回归测试。
- **WebUI 场景徽标矛盾**（必修）：逐段徽标原从修正前的 `scene_scores` 渲染，文本感知救回的演唱段（label=`singing`、但桶分仍偏 `music`）被概率徽标顶替成「音乐」。改为恒显示权威主场景标签及其概率，其余并存桶按 ≥10% 追加显示。

### 修复（其它）
- **语言提示归一化（原生端点）**：原生离线 `/v2/asr` 与实时 `/v2/asr/stream` 现统一把上游 `language` 归一为引擎语种名——接受 ISO-639-1 码（`zh`）、规范英文名（`Chinese`）、带地区子标签（`zh-CN`），无法识别的取值降级为自动检测。修复客户端传 `zh` 时透传到引擎抛 `Unsupported language: Zh`、导致**实时逐句报 `feed_failed` 零文本 / 离线任务失败**的问题（兼容层早有此归一，原生端点此前漏做）。归一化逻辑下沉至中立的 `app/utils/language.py`，离线与实时共用，兼容层 `mappers.to_engine_language` 改为 re-export。

## [2.2.0] - 2026-06-19

句子级准确分句能力（贡献者 PR #22「准确分句 + 修复处理切块边界重复识别」），叠加维护者评审修复。

### 新增 / 改进
- **句子级准确分句**：新增 `sentence_segmenter`，按标点 + 停顿 + 说话人切换重组句子；处理切块时长与句子边界解耦——不再把固定的处理切块时长（如 5s）当作句子边界拦腰切断。
- **静音感知二次切分**：超长连续语音段在最安静处（停顿）下刀，避免把连续语句切在词中。
- **边界重复识别去除**：处理切块拦腰切断导致的边界词重复（如「面前。面前」）兜底清除。

### 修复（维护者评审）
- **句子级去重误删**：边界重复去重仅作用于 force-split 产生的人为切点（`split_after` 标记），不再误删自然重叠词（如「好好」）与 `[识别失败]` 标记相邻的真实文本。
- **时间戳跑飞**：分句段 `end` 钳制到音频总时长（standard / vLLM 两路），修复对齐器损坏/回退时间戳导致段落跨度异常（如 `0→999s`）。
- **空文本段泄漏**：显式 `max_segment` 时间切片不再因取整产生空文本 segment。
- **英文句点误切**：改用缩写白名单 + 点状缩写识别，修复 `Mr.`/`Dr.`/`etc.` 被误切、单字母句末（`Plan A.`）漏切。
- **阈值解耦**：ASR 强制二次切分阈值 `force_max` 恒为 `MAX_ASR_CHUNK_DURATION`，与显式 `max_segment` 解耦，避免显式传参时把连续语句切在词中导致边界重复。

### 测试
- 新增/更新 13 项回归测试；全量 **893 单测通过**，并经 75 分钟真实多人长音频端到端验证（默认与 `max_segment=10` 两种配置均零异常）。

## [2.1.0] - 2026-06-13

### 新增
- **vLLM 原生流式引擎**（可选，纯 GPU）：`--serve-mode vllm`，句内实时 partial→final 增量解码 + 长音频逐块转写。
- vLLM 离线转写 `/v2/asr`（对齐 standard 契约，不依赖 funasr）；离线说话人分离/识别（CAM++，Phase 2）。
- vLLM 模式接入 OpenAI / DashScope 兼容接口（Phase 3，离线 + 实时增量）。
- 管理脚本与本地启动支持 vLLM serve-mode（`venv-vllm` / 独立镜像 / 参数面板）。

### 修复 / 优化
- 长音频对齐 OOM 根治：限制对齐/ASR 批大小，新增 `--vllm-align-device` 逃生路径。
- 离线分段改标点优先，根治对齐器伪间隙致单字碎片与负时长；说话人分离 wav 单次解码复用。

## [2.0.2] - 2026-06-11

### 优化
- CI：构建打 tag 分离 + CPU 合并多架构 + 同步 Docker Hub 描述。

## [2.0.1] - 2026-06-11

### 修复
- Docker：APT 换源改为可选 `build-arg`，修复 CI 境外构建连不上镜像源。

## [2.0.0] - 2026-06-10

重大版本，聚合多项特性分支。

### 新增
- **OpenAI / DashScope 兼容接口**（PR #19）：离线转写 `/compat/openai/v1/*`、`/compat/dashscope/api/v1/*`，OpenAI `stream=true` SSE 流式，实时语音兼容（OpenAI Realtime / DashScope realtime）。
- **说话人分离 / 声纹识别**（PR #16）：聚类 + 声纹库自动登记。
- **Live Voice 实时转写**（PR #11）：WebSocket 实时语音转写。
- **远场滤波**（PR #17）：能量 VAD / 降噪。
- **Web UI 改版**（PR #15）：左右分栏、i18n、任务面板。
- **离线任务持久化**（PR #14）：结果可查、TTL 清理。
- **`--config` 配置文件**（PR #13）：YAML 四层优先级 + 引导生成。

### 修复
- 兼容接口评审修复 8 项（SSRF 代理绕过 / 鉴权去重 / 会话复用泄漏等）；上游 ISO 语言码归一为 Qwen 规范名。
- `--help` 文案随 `$LANG` 自动中英切换；CLI 安装与设备适配多项修复。

## [1.2.1] - 2026-04-22

### 修复
- shell 脚本中文文案改英文，规避编码问题。

## [1.2.0] - 2026-04-14

### 新增
- 任务管理接口 `/v1/tasks`（列表 / 详情 / 取消），原 `/v1/asr/{task_id}` 查询标记 deprecated。
- ASR 任务取消功能（API + Web UI）。
- Web UI 重构为左右分栏，HTML 抽离独立模板。

### 修复
- 统一时间格式为 ISO 字符串；取消接口迁移至 `DELETE /v1/tasks/{task_id}`。

## [1.1.0] - 2026-04-13

### 新增
- CPU / ARM64（Apple Silicon）Docker 镜像构建支持。
- 可选 OpenAI 格式 Bearer Token API 认证；`--max-queue-size` 队列长度参数。
- Windows 交互式管理脚本 `cli.bat`；英文 README + DOCKERHUB.md。

### 修复
- API Key 比较改用 `hmac.compare_digest` 防时序侧信道；过滤空白 segment；ARM64 OpenVINO INT8 不兼容自动回退 FP32。

## [1.0.1] - 2026-03-24

### 修复
- 按模型大小区分 decoder 完整性检查，避免 1.7B 本地模型误判不完整。
- 内置默认 prompt template，修复 0.6B 模型缺 `prompt_template.json` 无法启动。

## [1.0.0] - 2026-03-20

首个正式发布。

### 新增
- ASR 流水线：VAD + Qwen3-ASR + 标点恢复；批量推理按 batch 分批提升 GPU 利用率。
- OpenVINO INT8 CPU 引擎；OpenVINO 1.7B 模型支持。
- Web UI + 可配置 VAD 段时长；Docker / docker-compose 支持。
- Windows 内嵌 Python 安装/启动脚本；Ctrl+C 优雅退出。

[2.4.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.4.0
[2.3.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.3.0
[2.2.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.2.0
[2.1.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.1.0
[2.0.2]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.0.2
[2.0.1]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.0.1
[2.0.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v2.0.0
[1.2.1]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v1.2.1
[1.2.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v1.2.0
[1.1.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v1.1.0
[1.0.1]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v1.0.1
[1.0.0]: https://github.com/LanceLRQ/qwen3-asr-service/releases/tag/v1.0.0
