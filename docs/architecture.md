# Qwen3-ASR Service 架构说明

**中文** | [English](architecture_EN.md)

## 项目结构

```
asr-service/
├── app/
│   ├── main.py                    # 服务入口（参数解析 + 按 serve-mode 装配）
│   ├── config.py                  # 全局配置
│   ├── api/
│   │   ├── routes.py              # 离线批处理路由（v1/v2 工厂）
│   │   ├── common_routes.py       # health / capabilities 共性路由
│   │   ├── ws_routes.py           # 实时转写 WebSocket 端点
│   │   ├── speaker_routes.py      # 声纹库管理/识别路由（/v2/speakers*）
│   │   ├── schemas.py             # 请求/响应数据模型
│   │   ├── ws_schemas.py          # 实时转写信封消息模型
│   │   └── compat/                # OpenAI / DashScope 兼容层（/compat/*，详见开发指南）
│   │       ├── openai_routes.py / openai_ws_routes.py        # OpenAI 离线 + 实时
│   │       ├── dashscope_routes.py / dashscope_ws_routes.py  # DashScope 离线 + 实时
│   │       ├── ws_bridge.py       # 实时 WS 共享骨架（adapter 驱动协议翻译）
│   │       └── mappers.py / errors.py / fetch.py / schemas.py # 映射 / 错误信封 / SSRF下载 / 模型
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
│   │   ├── task_manager.py        # 任务队列管理
│   │   ├── task_store.py          # 离线任务持久化（tasks.db）
│   │   └── stream_session.py      # 实时转写会话（在线 VAD 分段）
│   ├── web/
│   │   ├── views.py               # Web UI 路由（页面 + 文档中心）
│   │   ├── page.py                # 页面加载
│   │   ├── docs_site.py           # 文档中心（服务端 Markdown 渲染）
│   │   ├── docs_template.html     # 文档中心页面模板
│   │   ├── index.html             # 离线转写演示页（Vue 3 + Naive UI）
│   │   ├── stream.html            # 实时转写测试页（Vue 3 + Naive UI）
│   │   └── assets/                # 前端静态资源（vendored Vue/Naive UI UMD + 页面 JS + AudioWorklet）
│   └── utils/
│       ├── logger.py              # 日志配置
│       ├── arg_schema.py          # 启动参数单一 schema（argparse/配置文件共用）
│       ├── config_file.py         # config.yaml 发现/引导生成/校验/合并
│       ├── model_manager.py       # 模型下载管理
│       └── openvino_model_downloader.py  # OpenVINO 模型下载
├── models/                        # 模型存放（自动下载，不提交 Git）
├── data/                          # 任务持久化库（tasks.db，不提交 Git）
├── cache/                         # 运行时缓存（上传文件、音频切片）
├── logs/                          # 日志文件
├── scripts/                       # 开发/调研脚本
│   └── e2e/                       # 兼容接口端到端冒烟（独立 venv 一键 run.sh）
├── setup.sh / setup.bat           # 环境初始化
├── start.sh / start.bat           # 服务启动
├── config.example.yaml            # 配置模板（首启自动拷贝为 config.yaml）
└── requirements.txt               # 依赖清单

# 项目根目录
├── cli.sh / cli.bat                # 交互式管理脚本（Compose / venv / 启动服务统一入口）
├── docker/                         # Docker 资产目录（集中存放，保持根目录整洁）
│   ├── Dockerfile / Dockerfile.cpu     # GPU / CPU 镜像构建
│   ├── docker-compose.yml / *.cpu.yml  # Docker Compose 编排
│   ├── build.sh                        # 镜像构建脚本
│   └── DOCKERHUB.md                    # Docker Hub 页面文案
└── .dockerignore                   # 构建上下文过滤（须随上下文置于根）
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

**实时转写（`WS /v2/asr/stream`）：**

```
客户端音频帧(PCM16) → 在线VAD分块检测(200ms) → 语音段切分 → 内存离线解码(复用ASR引擎) → final 逐句下发
                                              (静音断句 / 12s 长句兜底切分)
```

离线与实时共用同一组模型引擎（VAD/ASR/标点），实时侧通过会话级缓冲与并发准入（会话数/解码并发上限）与离线任务争抢隔离。

## 关键设计

- **引擎模式**：每个模型（ASR/VAD/标点/对齐）封装为独立引擎，加载失败按重要性降级（VAD/ASR 失败终止启动，标点失败降级关闭）。
- **v1/v2 路由工厂**：同一组控制器函数注册到两个前缀，协议变更全部 additive，旧客户端零破坏。
- **任务队列**：单工作线程串行处理 + 线程池真超时；可选 write-through 持久化（[tasks.db](configuration.md#离线任务持久化tasksdb)），持久化故障不影响任务执行。
- **配置链**：启动参数单一 schema 同时驱动 argparse、config.yaml 校验与示例文件，消除多处默认值漂移（详见 [配置文档](configuration.md)）。
