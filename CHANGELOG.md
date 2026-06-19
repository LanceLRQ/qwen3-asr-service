# 更新日志（Changelog）

本项目所有重要变更记录于此。版本遵循 [语义化版本](https://semver.org/lang/zh-CN/)，
发布版本号经由 git tag（去掉 `v` 前缀）注入镜像 `APP_VERSION`，体现在 `/openapi.json` 的 `info.version`。

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
