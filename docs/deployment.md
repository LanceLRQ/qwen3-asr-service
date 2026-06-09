# Qwen3-ASR Service 部署指南

**中文** | [English](deployment_EN.md)

## 目录

- [系统要求](#系统要求)
- [Linux 部署](#linux-部署)
- [Windows 部署（Python Embeddable）](#windows-部署python-embeddable)
- [启用 API 认证](#启用-api-认证)
- [Docker 部署](#docker-部署)
- [交互式 CLI 管理](#交互式-cli-管理)
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
3. 运行初始化脚本：
   ```cmd
   cd asr-service
   setup.bat
   ```
4. 启动服务：
   ```cmd
   start.bat --device cuda --model-size 0.6b --host 0.0.0.0
   ```

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
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest \
  --model-size 0.6b --device auto --web
```

CPU / ARM64 镜像（`latest-cpu` / `latest-arm64`）与数据卷说明见 [Docker Hub 页面](https://hub.docker.com/r/lancelrq/qwen3-asr-service)。启用任务持久化时建议同时挂载 `/app/data`。

### 使用 docker-compose

```bash
# 直接启动（使用 docker/docker-compose.yml 中的默认配置）
docker compose -f docker/docker-compose.yml up -d

# 停止
docker compose -f docker/docker-compose.yml down
```

`docker/docker-compose.yml` 中可配置启动参数、API 密钥、端口映射等，详见文件内注释。CPU 版编排见 `docker/docker-compose.cpu.yml`。

### 本地构建镜像

```bash
bash docker/build.sh
```

## 交互式 CLI 管理

项目在仓库根目录提供交互式管理脚本，统一管理 Docker 和本地 venv 两种运行方式：

```bash
# Linux / macOS
bash cli.sh

# Windows
cli.bat
```

CLI 管理脚本支持：

- **Docker Compose 启动（config.yaml 驱动，推荐）**：首次使用自动从 `config.example.yaml` 生成 `config.yaml`，可直接编辑配置后启动/停止/重启容器、查看日志、切换 GPU/CPU 编排
- Docker 管理（拉取/构建镜像、参数向导启动/停止容器、查看日志）
- 虚拟环境管理（安装/卸载/查看信息）
- 启动服务（交互式配置参数，支持保存配置）

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
