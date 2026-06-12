# Qwen3-ASR Service 部署指南

**中文** | [English](deployment_EN.md)

## 目录

- [系统要求](#系统要求)
- [Linux 部署](#linux-部署)
- [Windows 部署（Python Embeddable）](#windows-部署python-embeddable)
- [启用 API 认证](#启用-api-认证)
- [Docker 部署](#docker-部署)
- [交互式管理脚本](#交互式管理脚本)
- [验证服务](#验证服务)
- [三种运行模式](#三种运行模式)
- [CPU 模式说明](#cpu-模式说明)
- [Web UI](#web-ui)
- [安全终止](#安全终止)

---

## 系统要求

- Python 3.10+
- ffmpeg（必须）
- NVIDIA GPU + CUDA 12.1+（GPU 模式需要）
- OpenVINO >= 2024.0（CPU 模式需要，pip install 自动安装）

```bash
# 安装 ffmpeg (Ubuntu/Debian)
apt install ffmpeg

# 确认 GPU 环境（可选）
nvidia-smi
```

### GPU 模式 PyTorch 版本要求

| CUDA 版本 | PyTorch 版本 |
|-----------|-------------|
| CUDA 12.4 | `torch==2.6.0+cu124` |
| CUDA 12.1 | `torch==2.5.1+cu121` |

安装示例（CUDA 12.4）：

```bash
pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124
```

> 注意：`qwen-asr` 需要 PyTorch 2.6+ 或 2.5.1+cu121，`funasr==1.3.1` 才能正常工作。

## Linux 部署

### 1. 初始化环境

```bash
cd asr-service
bash setup.sh
```

### 2. 启动服务

```bash
# 默认模式（自动检测显存选择模型；首次启动自动生成 config.yaml 并下载模型）
bash start.sh

# GPU 全功能模式（1.7B 模型 + 对齐）
bash start.sh --model-size 1.7b --enable-align

# GPU 轻量模式（0.6B 模型，关闭对齐）
bash start.sh --model-size 0.6b --no-align

# CPU 模式（OpenVINO INT8 推理，无需显卡）
bash start.sh --device cpu --model-size 0.6b

# CPU 模式 + 1.7B 模型（更高精度，需更多内存）
bash start.sh --device cpu --model-size 1.7b

# 自定义 VAD 切片合并时长（默认 5 秒）
bash start.sh --max-segment 15

# 指定模型下载源（国内推荐 modelscope，海外用 huggingface）
bash start.sh --model-source modelscope
bash start.sh --model-source huggingface
```

服务默认监听 `http://127.0.0.1:8765`（仅本机访问）。如需局域网访问：

```bash
bash start.sh --host 0.0.0.0
bash start.sh --host 0.0.0.0 --port 9000
```

> 启动参数也可以写进 `config.yaml` 统一管理（首次启动已自动生成），全部参数与优先级规则见 [配置文档](configuration.md)。

## Windows 部署（Python Embeddable）

Windows 可使用 Python Embeddable Package 实现独立便携部署：

1. 下载 [Python 3.12 Embeddable Package](https://www.python.org/downloads/windows/) 放入 `bin/` 目录
2. 下载 [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) 并将 `ffmpeg.exe` 放入 `bin/` 目录
3. 运行初始化脚本（PowerShell）：
   ```powershell
   cd asr-service
   .\setup.ps1
   ```
4. 启动服务：
   ```powershell
   .\start.ps1 --device cuda --model-size 0.6b --host 0.0.0.0
   ```

> 💡 推荐使用 PowerShell 脚本（`.ps1`）。同名的 `.bat`（`setup.bat` / `start.bat`）仅作旧版 cmd 兼容保留，新环境下不保证可正常运行；如遇问题请改用 `.ps1`。首次运行若提示执行策略限制，可用 `powershell -ExecutionPolicy Bypass -File .\setup.ps1`。

## 启用 API 认证

设置 API 密钥后，所有接口（除 `/health`、`/capabilities` 外）需要携带 Bearer Token：

```bash
# 通过启动参数设置
bash start.sh --api-key sk-your-key-here

# 或通过环境变量设置
export ASR_API_KEY=sk-your-key-here
bash start.sh

# 或写入 config.yaml 的 api_key 键（注意该文件勿提交版本库）
```

调用方式见 [API 文档 · 认证](api/v2/basics.md#认证)。

## Docker 部署

### 使用预构建镜像

```bash
# 拉取镜像
docker pull lancelrq/qwen3-asr-service:latest

# 启动容器（GPU 模式）
docker run -d --gpus all \
  -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  -v ./asr-service/logs:/app/logs \
  -v ./asr-service/data:/app/data \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest \
  --model-size 0.6b --device auto --web
```

`/app/data` 存放任务持久化库 `tasks.db` 与声纹库 `speakers.db`，挂出以跨容器重建保留（compose 已默认挂载）。CPU / ARM64 镜像（`latest-cpu` / `latest-arm64`）与更多数据卷说明见 [Docker Hub 页面](https://hub.docker.com/r/lancelrq/qwen3-asr-service)。

### 使用 docker-compose

```bash
# 直接启动（使用 docker/docker-compose.yml 中的默认配置）
docker compose -f docker/docker-compose.yml up -d

# 停止
docker compose -f docker/docker-compose.yml down
```

`docker/docker-compose.yml` 中可配置启动参数、API 密钥、端口映射等，详见文件内注释。CPU 版编排见 `docker/docker-compose.cpu.yml`。

### vLLM 原生流式镜像（独立）

vLLM 模式（路线 A，逐句 partial→final 渐进流式 + 与 standard 同契约的离线 `/v2/asr`）为 **GPU 专用的独立镜像**，基于 vLLM 官方镜像 `vllm/vllm-openai` 派生，不与默认镜像合并——standard 用户不下载 vLLM 重型 CUDA kernels，vllm 用户不下载 OpenVINO/funasr。能力差异（含离线分段/标点/说话人的取舍）与参数见 [配置文档：vLLM 原生流式模式](configuration.md#vllm-原生流式模式路线-a)。

```bash
# 启动（独立端口 8766，与 standard asr 8765 并存）
docker compose -f docker/docker-compose.vllm.yml up -d

# 停止
docker compose -f docker/docker-compose.vllm.yml down

# 本地构建 vLLM 镜像（build.sh 选项 4）
bash docker/build.sh   # 选择 "4) vLLM"
```

> vLLM 引擎在独立 EngineCore 子进程持有 GPU，服务固定单 worker（容器内 PID 1 收割子进程）。模型用 HF 全精度 `models/asr/0.6b`/`1.7b`（与 standard 共用 `models/` 挂载）。
>
> **离线词级时间戳 / 说话人所需模型**：`--vllm-enable-align`（默认开）需对齐器 `models/asr/aligner`（Qwen3-ForcedAligner，HF 直拉）；`--enable-speaker` 需声纹模型 `models/speaker/campplus`（CAM++，仅 ModelScope 提供）——`requirements-vllm.txt` 已含 `modelscope` 兜底自动下载，生产建议预挂该目录以免运行时联网。说话人聚类依赖 `scipy`/`scikit-learn`（同已含）。
>
> ⚠️ **长音频对齐 OOM**：ForcedAligner 在主进程加载，显存**不计入 `--gpu-memory-utilization`**（该参数只约束 vLLM EngineCore 子进程）。`transcribe` 内部按 ≤180s 切块，但默认把一个文件的**全部块一次性**喂对齐器前向——长音频（如 30 分钟）激活叠加报 `CUDA out of memory`（短音频只 1 块、不受影响）。对策（按推荐序）：① `--vllm-infer-batch-size`（默认已改为 4）逐批对齐、峰值显存随批大小下降，长音频仍 OOM 则降到 1；② `--vllm-align-device cpu`（对齐器移 CPU，无 GPU 争用，较慢）；③ 降 `--gpu-memory-utilization` 留更多余量；④ `--no-vllm-align`。详见[配置文档](configuration.md#vllm-原生流式模式路线-a)。

> **兼容接口（OpenAI/DashScope）与离线说话人**：vLLM 模式同样支持 `--enable-openai-api`/`--enable-dashscope-api`（离线 + 实时 WS）与 `--enable-speaker`/`--enable-speaker-db`（离线说话人分离/识别，CAM++，语音区间用能量 VAD，弱于 standard 的 FSMN）。与 standard 的关键差异——vLLM 流式恒开，**实时兼容随兼容开关自动挂载、无需 `--enable-stream`**，且实时支持逐字增量（DashScope 中间结果干净直发 / OpenAI delta best-effort）。本地启动用独立环境（非默认 venv）：`venv-vllm/bin/python -m app.main --serve-mode vllm …`（Docker 已由上面的 compose 封装；vLLM 必 CUDA，非 GPU 设备直接退出）。能力与协议详见[兼容接口文档](api/compat.md)。

#### vLLM 启动日志说明（常见现象，非故障）

vLLM 模式启动/退出时，日志里会出现两条看似报错、实则**无害**的信息，可放心忽略：

1. **`ERROR … repo_utils.py … Error retrieving safetensors: Repo id must be in the form …`（会重试 2 次）**
   vLLM 推断模型精度时，对**本地模型目录**有一处上游怪癖：未先判本地路径，就把绝对路径当成 HF 仓库名去查 safetensors 元数据，被仓库名格式校验拒绝。该异常被 vLLM 内部捕获后即回落到从模型配置读取精度，**模型仍按 bfloat16 正常加载**，对功能与显存均无影响。`HF_HUB_OFFLINE=1` 也压不掉这条日志（格式校验发生在离线判断之前），无需理会。

2. **退出（`Ctrl+C` / `docker stop`）时 `Engine core proc EngineCore_DP0 died unexpectedly`**
   vLLM 把 CUDA 上下文放在独立 `EngineCore` 子进程，关闭时子进程随主进程退出，client 监控线程据此打印此行——是**正常的关闭现象**，不是崩溃。

**判断服务是否真正就绪**，以下面两个信号为准（而非有无上述 ERROR）：

```
INFO: Application startup complete.
INFO: Uvicorn running on http://<host>:<port>
```

```bash
curl http://127.0.0.1:8765/v2/health   # 返回 {"status":"ready","mode":"vllm",...} 即就绪
```

> 模型加载（torch.compile + 权重）约需数十秒，请等到上述 `Uvicorn running` 再判断，勿在加载途中误判失败。本地（非容器）`Ctrl+C` 退出后，若 `nvidia-smi` 显存未回落，说明 `EngineCore` 子进程残留，执行 `pkill -KILL -f EngineCore` 清理即可（容器部署由 PID 1 自动收割，无需手动处理）。

### 本地构建镜像

```bash
bash docker/build.sh
```

## 交互式管理脚本

项目在仓库根目录提供交互式管理脚本，统一管理 Docker 和本地 venv 两种运行方式：

```bash
# Linux / macOS
bash manage.sh

# Windows
.\manage.ps1
```

管理脚本支持：

- **Docker Compose 启动（config.yaml 驱动，推荐）**：首次使用自动从 `config.example.yaml` 生成 `config.yaml`，可直接编辑配置后启动/停止/重启容器、查看日志、切换 GPU/CPU/**vLLM** 编排
- Docker 管理（拉取镜像支持 GPU/CPU/**vLLM** 变体、构建镜像、参数向导启动/停止容器、查看日志）
- 虚拟环境管理（standard `venv` 与 **vLLM `venv-vllm`** 双环境的安装/卸载/查看信息）
- 启动服务（交互式配置参数，含 **`serve-mode` 选择 standard/vllm**，vLLM 自动用 venv-vllm 环境/`-vllm` 镜像，支持保存配置）

## 验证服务

```bash
curl http://127.0.0.1:8765/v2/health
```

响应示例（GPU 模式）：

```json
{
  "status": "ready",
  "device": "cuda",
  "model_size": "0.6b",
  "align_enabled": true,
  "punc_enabled": true,
  "asr_backend": "qwen_asr",
  "vad_backend": "pytorch",
  "punc_backend": "pytorch"
}
```

CPU 模式下 `asr_backend` 为 `openvino`、`vad_backend`/`punc_backend` 为 `onnx`。完整字段说明见 [API 文档 · 健康检查](api/v2/basics.md#健康检查)。

## 三种运行模式

| | GPU 全功能 | GPU 轻量 | CPU (OpenVINO) |
|--|-----------|---------|---------|
| ASR | Qwen3-ASR + CUDA | Qwen3-ASR + CUDA | **OpenVINO INT8** |
| 推理框架 | PyTorch (transformers) | PyTorch (transformers) | **OpenVINO (纯 NumPy 预处理)** |
| 对齐 | ForcedAligner | **关闭** | **强制关闭** |
| VAD | FSMN-VAD (PyTorch) | FSMN-VAD (PyTorch) | FSMN-VAD (**ONNX**) |
| 标点 | CT-Transformer (PyTorch) | CT-Transformer (PyTorch) | CT-Transformer (**ONNX**) |
| 时间戳 | 单词级 | 句子级 | 句子级 |
| 显存需求 | ~6-8GB | ~2-3GB | 无需 GPU，内存 ~4-6GB |
| 模型来源 | ModelScope / HuggingFace | ModelScope / HuggingFace | **HuggingFace** |

> `--device auto` 时，服务根据显存自动选择：>=6GB 用 1.7B，4-6GB 用 0.6B，<4GB 强制关闭对齐，无 GPU 回退 CPU（OpenVINO）。

## CPU 模式说明

CPU 模式使用 OpenVINO 推理引擎替代 PyTorch，核心特点：

- **INT8 量化模型**：相比 FP32 大幅减少内存占用和计算量
- **纯 NumPy 预处理**：Mel 特征提取和 BPE 解码完全由 NumPy 实现，不依赖 torch/transformers 做推理
- **首次编译耗时**：OpenVINO 模型编译约 10-30 秒，仅在启动时执行一次
- **模型自动下载**：首次启动自动从 HuggingFace 下载 OpenVINO 格式模型

CPU 模式使用的 OpenVINO 模型：

| 模型大小 | HuggingFace 仓库 | 量化方式 |
|---------|-----------------|---------|
| 0.6B | `dseditor/Qwen3-ASR-0.6B-INT8_ASYM-OpenVINO` | INT8 非对称 |
| 1.7B | `dseditor/Qwen3-ASR-1.7B-INT8_OpenVINO` | INT8 |

## Web UI

启动时开启 `--web`（example 生成的配置中默认开启）即可使用浏览器界面，访问 `http://<host>:<port>/web-ui`：

- 拖拽或点击上传音频文件
- 实时显示识别进度，识别中可一键取消
- 分段结果展示，点击片段可跳转播放对应音频位置
- 完整文本展示、原始 JSON 数据查看和下载
- **任务列表自动刷新**：进行中任务每 3 秒、空闲每 30 秒自动更新（后台标签页暂停）；含持久化历史任务的查看与删除
- 暗色主题：跟随系统，支持手动切换

界面基于 Vue 3 + Naive UI 构建（库文件随仓库分发，**无需 node/npm，无构建步骤**，clone 即用，离线可用）。

配合 `--enable-stream` 启动时，`/web-ui/stream` 提供实时转写测试页（麦克风采集 / 音频文件模拟推流，可查看协议日志与诊断指标）。文件模拟推流的 ffmpeg-wasm 转码器需外网加载，失败时自动回退浏览器原生解码。

`/web-ui/docs` 是内置文档中心，离线渲染本仓库全部使用文档（部署 / 配置 / API / 架构，中英双语），无外网也可查阅。

> 服务还自带 Swagger UI 交互式接口调试页，地址 `http://<host>:<port>/docs`（FastAPI 自动生成，不受 `--web` 开关控制）。注意：该链接仅在服务运行时有效（在 GitHub 上浏览本文档时无法跳转），且其页面静态资源从公网 CDN 加载，离线环境下无法显示。

## 安全终止

服务支持 `Ctrl+C` 安全退出。按下后会：

1. 停止接收新请求
2. 取消正在处理的 ASR 任务（当前 chunk 完成后立即停止）
3. 关闭工作线程和线程池
4. 清理临时文件
