# Qwen3-ASR Service

基于 Qwen3-ASR 的开箱即用长语音识别 API 服务。

## 特性

- **开箱即用** - 一键安装部署，自动下载模型
- **长语音支持** - 支持 1s ~ 4 小时的音频文件，自动 VAD 切片处理
- **多格式支持** - WAV / MP3 / FLAC / M4A / AAC / OGG 等
- **灵活部署** - GPU（CUDA）和 CPU（OpenVINO INT8）双模式
- **异步任务** - 提交任务后轮询结果，支持大文件处理
- **时间戳支持** - 句子级 / 单词级时间戳（GPU 模式）
- **自动标点** - 集成 CT-Transformer 标点恢复模型
- **Web UI** - 内置浏览器界面，支持音频上传、实时进度、结果播放和导出

## 系统要求

- Python 3.10+
- ffmpeg（必须）
- NVIDIA GPU + CUDA 12.1+（GPU 模式需要）
- OpenVINO >= 2024.0（CPU 模式需要，pip install 自动安装）

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

```bash
# 安装 ffmpeg (Ubuntu/Debian)
apt install ffmpeg

# 确认 GPU 环境（可选）
nvidia-smi
```

## 快速开始

### Windows 部署（Python Embeddable）

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

### Linux 部署

#### 1. 初始化环境

```bash
cd asr-service
bash setup.sh
```

### 2. 启动服务

```bash
# GPU 默认模式（自动检测显存，选择模型大小）
bash start.sh

# GPU 全功能模式（1.7B 模型 + 对齐）
bash start.sh --model-size 1.7b --enable-align

# GPU 轻量模式（0.6B 模型，关闭对齐）
bash start.sh --model-size 0.6b --no-align

# CPU 模式（OpenVINO INT8 推理，无需显卡）
bash start.sh --device cpu --model-size 0.6b

# CPU 模式 + 1.7B 模型（更高精度，需更多内存）
bash start.sh --device cpu --model-size 1.7b

# 启用 Web UI（浏览器访问 http://0.0.0.0:8765/web-ui）
bash start.sh --web

# 自定义 VAD 切片合并时长（默认 5 秒）
bash start.sh --max-segment 15

# 指定模型下载源（国内推荐 modelscope，海外用 huggingface）
bash start.sh --model-source modelscope
bash start.sh --model-source huggingface
```

服务默认监听 `http://127.0.0.1:8765`（仅本机访问）。

如需局域网访问：

```bash
bash start.sh --host 0.0.0.0
bash start.sh --host 0.0.0.0 --port 9000
```

### 3. 验证服务

```bash
curl http://127.0.0.1:8765/health
```

响应示例：

GPU 模式：

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

CPU 模式：

```json
{
  "status": "ready",
  "device": "cpu",
  "model_size": "0.6b",
  "align_enabled": false,
  "punc_enabled": true,
  "asr_backend": "openvino",
  "vad_backend": "onnx",
  "punc_backend": "onnx"
}
```

## 启动参数

| 参数 | 取值 | 默认值 | 说明 |
|------|------|--------|------|
| `--device` | `auto` / `cuda` / `cpu` | `auto` | 运行设备，auto 自动检测 |
| `--model-size` | `0.6b` / `1.7b` | 根据显存自动选择 | ASR 模型大小 |
| `--enable-align` / `--no-align` | - | `--enable-align` | 是否加载对齐模型（单词级时间戳） |
| `--use-punc` | - | 关闭 | 是否启用标点恢复 |
| `--model-source` | `modelscope` / `huggingface` | `modelscope` | 模型下载源 |
| `--host` | IP 地址 | `127.0.0.1` | 监听地址，设为 `0.0.0.0` 可局域网访问 |
| `--port` | 端口号 | `8765` | 监听端口 |
| `--web` | - | 关闭 | 启用 Web UI（访问 `/web-ui`） |
| `--max-segment` | 秒数 | `5` | VAD 切片合并最大时长 |

### 三种运行模式

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

### CPU 模式说明

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

## API 接口

### 提交 ASR 任务

```bash
curl -X POST http://127.0.0.1:8765/asr \
  -F "file=@/path/to/audio.wav"
```

带可选参数：

```bash
curl -X POST http://127.0.0.1:8765/asr \
  -F "file=@/path/to/audio.mp3" \
  -F "language=zh"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| file | 文件 | 必填 | 音频文件（WAV/MP3/FLAC/M4A/AAC/OGG 等） |
| language | string | null | 语言代码，null 为自动检测 |

响应：

```json
{"task_id": "550e8400-e29b-41d4-a716-446655440000"}
```

**限制**：文件最大 1GB，音频时长 1s ~ 4小时。

### 查询任务状态

```bash
curl http://127.0.0.1:8765/asr/{task_id}
```

响应（完成）：

```json
{
  "task_id": "550e8400-...",
  "status": "completed",
  "progress": 1.0,
  "result": {
    "segments": [
      {
        "start": 0.0,
        "end": 3.2,
        "text": "甚至出现交易几乎停滞的情况。",
        "words": [
          {"text": "甚", "start": 0.0, "end": 0.15},
          {"text": "至", "start": 0.15, "end": 0.30}
        ]
      }
    ],
    "full_text": "甚至出现交易几乎停滞的情况。",
    "language": null,
    "align_enabled": true,
    "punc_enabled": true
  },
  "error": null
}
```

- `words` 字段仅在 `align_enabled=true` 时存在
- 任务状态流转：`pending` → `processing` → `completed` / `failed`

### 健康检查

```bash
curl http://127.0.0.1:8765/health
```

## Web UI

启动时添加 `--web` 参数即可开启浏览器界面：

```bash
bash start.sh --web
```

访问 `http://<host>:<port>/web-ui`，支持以下功能：

- 拖拽或点击上传音频文件
- 实时显示识别进度
- 分段结果展示，点击片段可跳转播放对应音频位置
- 完整文本展示
- 原始 JSON 数据查看和下载

## 项目结构

```
asr-service/
├── app/
│   ├── main.py                    # 服务入口（argparse 启动参数）
│   ├── config.py                  # 全局配置
│   ├── api/
│   │   ├── routes.py              # FastAPI 路由
│   │   └── schemas.py             # 请求/响应数据模型
│   ├── engines/
│   │   ├── qwen_asr_engine.py     # Qwen3-ASR 识别引擎（GPU）
│   │   ├── openvino_asr_engine.py # OpenVINO ASR 引擎（CPU）
│   │   ├── processor_numpy.py     # 纯 NumPy Mel 提取 + BPE 解码
│   │   ├── vad_engine.py          # FSMN-VAD 语音检测引擎
│   │   └── punc_engine.py         # CT-Transformer 标点引擎
│   ├── pipeline/
│   │   ├── asr_pipeline.py        # ASR 流水线编排
│   │   └── audio_preprocessor.py  # ffmpeg 格式转换
│   ├── runtime/
│   │   ├── device.py              # 设备检测与选择
│   │   └── task_manager.py        # 任务队列管理
│   ├── web/
│   │   ├── views.py               # Web UI 路由
│   │   └── page.py                # Web UI 单页应用（HTML/CSS/JS）
│   └── utils/
│       ├── logger.py              # 日志配置
│       ├── model_manager.py       # 模型下载管理
│       └── openvino_model_downloader.py  # OpenVINO 模型下载
├── models/                        # 模型存放（自动下载，不提交 Git）
├── cache/                         # 运行时缓存（上传文件、音频切片）
├── logs/                          # 日志文件
├── setup.sh                       # 环境初始化
├── start.sh                       # 服务启动
└── requirements.txt               # 依赖清单
```

## 处理流程

**GPU 模式：**

```
音频文件 → ffmpeg转换(16kHz WAV) → VAD切片 → 段合并 → ASR识别 → [标点恢复] → 输出结果
                                   (FSMN-VAD)  (≤5s)  (Qwen3-ASR)  (CT-Transformer)
                                                           ↓
                                                    [可选] 对齐(ForcedAligner)
```

**CPU 模式（OpenVINO）：**

```
音频文件 → ffmpeg转换(16kHz WAV) → VAD切片 → 段合并 → ASR识别 → [标点恢复] → 输出结果
                                   (FSMN-VAD   (≤5s)  (OpenVINO     (CT-Transformer
                                    ONNX)                INT8)          ONNX)
                                                  ↓
                                    NumPy Mel提取 → audio_encoder
                                                 → thinker_embeddings
                                                 → decoder 自回归解码
                                                 → BPE decode
```

## 配置项

主要配置在 `app/config.py`：

| 配置 | 默认值 | 说明 |
|------|--------|------|
| HOST | 127.0.0.1 | 监听地址（可通过 `--host` 覆盖） |
| PORT | 8765 | 监听端口（可通过 `--port` 覆盖） |
| MAX_SEGMENT_DURATION | 5s | VAD 段合并 / 超长切分阈值（可通过 `--max-segment` 启动参数覆盖） |
| MAX_AUDIO_DURATION | 14400s | 最大音频时长（4 小时） |
| MAX_AUDIO_FILE_SIZE | 1024MB | 最大文件大小 |
| MIN_AUDIO_DURATION | 1.0s | 最短音频时长 |
| MAX_QUEUE_SIZE | 100 | 最大任务队列长度 |
| TASK_TIMEOUT | 1800s | 单任务超时（30 分钟） |

## 安全终止

服务支持 `Ctrl+C` 安全退出。按下后会：

1. 停止接收新请求
2. 取消正在处理的 ASR 任务（当前 chunk 完成后立即停止）
3. 关闭工作线程和线程池
4. 清理临时文件
