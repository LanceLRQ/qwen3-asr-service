# vLLM 引擎特性总览

[← 返回文档首页](../README.md) ｜ **中文** | [English](vllm-vs-standard_EN.md)

> 本文聚焦 **vLLM 引擎相对默认 standard 模式的功能差异**：梳理 **vLLM 引擎**（`--serve-mode vllm`）相对原有默认 **standard** 模式（funasr / OpenVINO）在**功能特性**上的明显区别，便于选型与升级评估。
>
> **一句话**：原服务只有 `standard` 一种运行模式；本特性新增**可选的 `vllm` 运行模式**——GPU 原生流式（逐句增量）+ 与 standard 同契约的离线接口，并为该模式补齐说话人、兼容接口能力。**两种模式互斥启动；standard 模式的行为、参数、默认值完全不变（零回归）。**

---

## 1. 核心区别：新增 vLLM 运行模式

| | 原默认（standard） | 新增（vllm） |
|---|---|---|
| 启动 | 固定 standard | `--serve-mode standard \| vllm`（二选一） |
| 推理后端 | funasr（GPU）/ OpenVINO（CPU） | vLLM 原生引擎（进程内同步 `vllm.LLM`） |
| 设备 | GPU / **CPU** 均可 | **仅 GPU（必 CUDA，非 GPU 直接退出）** |
| 运行环境 | `venv`（默认） | 独立 `venv-vllm` / 独立 Docker 镜像 |
| 进程模型 | 多 worker 可 | **uvicorn workers=1**（CUDA 上下文在独立子进程） |

> vLLM 与 standard 的 torch/CUDA 栈不可共存，故采用**独立环境与独立镜像**，互不影响。

---

## 2. 功能差异（分类）

### 2.1 实时流式：整句 → 逐句增量（质的提升）
- **standard**：实时后端为 VAD-offline，**仅在每句切分后产整句 `final`**，无逐字/中间增量（`partial_results=false`）。
- **vLLM**：原生流式解码，句内**逐步刷新 `partial` → 句末 `final`**（`partial_results=true`）——这是 vLLM 模式相对 standard 最显著的实时体验提升。

### 2.2 离线转写 `/v2/asr`
- 两模式**同契约**：`segments / full_text / words / speaker / speakers / warnings` 字段一致，前端与 SDK 零改动。
- vLLM 模式的实现差异（取舍）：
  - 分段：**标点优先**（句末标点切句 + 词级时间戳定位），无 FSMN 精分段。
  - 标点：**模型原生**提供，恒有、不可单独关（请求关闭会记入 `warnings`）。
  - **长音频逐块转写**（见 2.5）。

### 2.3 说话人分离 / 识别
- 说话人能力（`--enable-speaker` / 声纹库 `--enable-speaker-db`）**两种模式都支持**，输出字段一致。
- vLLM 模式差异：声纹引擎同为 CAM++，但**语音区间用能量 VAD 替代 FSMN-VAD**（依赖中性、不引 funasr，边界较粗）；实时流式仍不带说话人（仅离线）。

### 2.4 兼容接口（OpenAI / DashScope）
- 离线兼容（OpenAI `audio/transcriptions`、DashScope 录音文件识别）**两模式都支持**。
- **实时兼容的增量能力是 vLLM 独有的新增**：
  - standard 实时兼容只能下发**整句**（受限于 VAD-offline 不产 partial）。
  - vLLM 实时兼容产**逐字/中间增量**：DashScope 中间 `result-generated`（`sentence_end=false`，天然累计、干净直发）；OpenAI `…delta`（**best-effort**：partial 累计且可修订，仅纯追加取后缀作 delta、修订帧跳过，权威全文以 `…completed` 为准）。
  - vLLM 模式实时兼容**随兼容开关自动挂载、无需 `--enable-stream`**（与 standard 的唯一启动差异）。

### 2.5 长音频处理（vLLM 专属新增）
- 离线超过 `vllm_offline_chunk_sec`（默认 180s）的音频按**静音边界逐块转写**（块拼接=原音频）：
  - **进度平滑**：转写阶段随块上报（不再 10%→90% 直跳）。
  - **块间可取消**：长任务可中途响应取消。
  - **显存收敛**：每次只对齐 1 块，**根治长音频对齐 `CUDA out of memory`**。
- 配套旋钮：`--vllm-infer-batch-size`（一次对齐/ASR 块数，默认 4）、`--vllm-align-device cpu`（对齐器移 CPU 逃生）。

---

## 3. 能力对照速查

| 能力 | standard | vLLM |
|---|---|---|
| 运行设备 | GPU / CPU(OpenVINO) | 仅 GPU（必 CUDA） |
| 实时流式 | 可选；仅整句 final | 恒开；**逐句 partial→final 增量** |
| 离线 `/v2/asr` | ✅ | ✅（同契约） |
| 分段 | FSMN-VAD 精分段 | 标点优先（词级时间戳定位） |
| 标点 | CT-Transformer（可单独关） | 模型原生（恒有，不可单独关） |
| 说话人分离 / 识别 | ✅（FSMN-VAD + CAM++） | ✅（能量 VAD + CAM++） |
| 兼容离线（OpenAI/DashScope） | ✅ | ✅ |
| 兼容实时——整句 | ✅（需 `--enable-stream`） | ✅（随兼容开关，无需 `--enable-stream`） |
| 兼容实时——逐字/中间增量 | ❌ | ✅ DashScope 中间结果 / OpenAI delta(best-effort) |
| 长音频逐块（进度/取消/省显存） | —（FSMN 管线） | ✅ |

---

## 4. 新增配置项与启动参数

### vLLM 专属配置（`config.yaml` 键 / 对应 CLI）

| 配置键 / CLI | 默认 | 说明 |
|---|---|---|
| `gpu_memory_utilization` / `--gpu-memory-utilization` | `0.6` | vLLM 显存占用率（单流 ASR 无需 0.8） |
| `vllm_max_model_len` / `--vllm-max-model-len` | `32768` | 单序列上下文上限 |
| `vllm_chunk_size_sec` / `--vllm-chunk-size-sec` | `1.0` | 流式解码块大小（越小 partial 越细腻） |
| `vllm_max_utterance_sec` / `--vllm-max-utterance-sec` | `20` | 单句兜底切分（秒） |
| `vllm_concurrency` / `--vllm-concurrency` | `1` | 同时解码会话数（generate 串行，>1 无吞吐收益） |
| `vllm_end_silence_ms` / `--vllm-end-silence-ms` | `800` | 能量端点尾静音判停阈值 |
| `vllm_enable_align` / `--vllm-enable-align`·`--no-vllm-align` | 开 | 离线词级时间戳（加载对齐模型） |
| `vllm_align_device` / `--vllm-align-device` | `cuda` | 对齐器设备；长音频 OOM 时改 `cpu` |
| `vllm_infer_batch_size` / `--vllm-infer-batch-size` | `4` | 一次对齐/ASR 的音频块数（`-1` 易 OOM） |
| `vllm_segment_gap_ms` / `--vllm-segment-gap-ms` | `500` | 离线分段：相邻词间隙断句阈值 |

### 仅配置文件项（无 CLI）

| 配置键 | 默认 | 说明 |
|---|---|---|
| `vllm_unfixed_chunk_num` | `2` | 流式起始不取历史当前缀的块数（冷启动稳定） |
| `vllm_unfixed_token_num` | `5` | 起始块后回滚末 K token 当前缀（降抖动） |
| `vllm_energy_floor_dbfs` | `-45.0` | 流式能量端点门限（dBFS） |
| `vllm_offline_chunk_sec` | `180` | 离线逐块转写切块时长（秒） |

> 说话人（`--enable-speaker*`）、兼容接口（`--enable-openai-api` / `--enable-dashscope-api`）等开关 **standard 已有**，vLLM 模式复用，非新增。

---

## 5. 部署与运维变化

- **独立本地环境**：`bash setup.sh --vllm` 建 `venv-vllm`；`QWEN_VENV=venv-vllm bash start.sh --serve-mode vllm` 启动。
- **独立 Docker 镜像**：`docker/Dockerfile.vllm`（基于 `vllm/vllm-openai:v0.14.0` 派生）+ `docker/docker-compose.vllm.yml`（独立端口 **8766**，与 standard 的 8765 并存）；`docker/build.sh` 第 4 项构建。
- **交互式管理脚本 `manage.sh`**：参数面板新增「启动模式」（选 vllm 后面板动态变形），venv 启动自动用 `venv-vllm`、Docker 自动用 `-vllm` 镜像 tag，Compose 编排 / 镜像拉取增加 vLLM 变体，venv 菜单支持 standard/vLLM 双环境管理。
- **CI**：新增独立 vLLM 镜像构建 job（打 `:<ver>-vllm` / `:latest-vllm`，与 GPU/CPU 解耦）。

---

## 6. 对 standard 模式的影响：零回归

- vLLM 相关代码全部**依赖中性 + 惰性导入**（仅 `--serve-mode vllm` 时加载），standard / CPU 模式不引入任何 vLLM 依赖。
- `main.py` 顶层 funasr 系导入改为容错（使 vLLM 环境无 funasr 也能加载应用）。
- standard 的 CLI、配置默认值、接口契约、行为**均不变**。

---

## 7. 已知取舍与限制

- **仅 GPU**：vLLM 模式不支持 CPU，亦无 CPU 容器。
- **质量取舍**：分段（标点优先 / 词间隙）与说话人语音区间（能量 VAD）弱于 standard 的 FSMN；标点不可单独关。需要高保真分段/标点/实时说话人请用 standard。
- **OpenAI 实时 delta 为 best-effort**：协议要求增量片段而 vLLM partial 为累计可修订，修订帧跳过，权威全文以 `completed` 为准。
- **并发**：进程内同步推理，`workers=1`、`concurrency` 提高无吞吐收益。

---

## 相关文档

- [配置文档 · vLLM 原生流式模式](configuration.md#vllm-原生流式模式)
- [部署文档 · vLLM 镜像](deployment.md)
- [兼容接口](api/compat.md)
