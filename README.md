# Qwen3-ASR Service

**中文** | [English](README_EN.md)

基于 Qwen3-ASR 的开箱即用长语音识别 API 服务，支持 GPU（CUDA）与 CPU（OpenVINO INT8）双模式推理。

## 特性

- ⚡ **启动快、转写快** - 服务启动迅速；长音频转写耗时远低于音频时长，GPU 模式尤为显著，CPU 模式经 OpenVINO INT8 量化同样高效
- **开箱即用** - 一键安装部署，自动下载模型，首次启动自动生成配置文件
- **长语音支持** - 1s ~ 4 小时音频文件，自动 VAD 切片处理
- **实时转写** - WebSocket 流式端点，麦克风/推流音频逐句返回结果
- **异步任务 + 持久化** - 提交后轮询结果，任务结果跨重启可查（tasks.db）
- **多格式支持** - WAV / MP3 / FLAC / M4A / AAC / OGG 等
- **时间戳支持** - 句子级 / 单词级时间戳（GPU 模式）
- **自动标点** - 集成 CT-Transformer 标点恢复模型
- **Web UI** - 现代化界面（Vue 3 + Naive UI，暗色主题）：离线转写、实时转写、任务历史自动刷新与离线文档中心
- **API 认证** - 可选的 Bearer Token 认证
- **灵活配置** - YAML 配置文件 / 命令行参数 / 环境变量四层优先级
- **交互式管理** - CLI 管理脚本，支持 Docker / venv 双模式一键管理

## 快速开始

> 依赖：Python 3.10+、ffmpeg；GPU 模式需 NVIDIA GPU + CUDA 12.1+（详见[部署指南](docs/deployment.md)）。

```bash
cd asr-service
bash setup.sh        # 初始化环境
bash start.sh        # 启动服务（自动检测设备、下载模型、生成 config.yaml）

# 验证
curl http://127.0.0.1:8765/v2/health
```

浏览器访问 `http://127.0.0.1:8765/web-ui` 即可上传音频体验（自动生成的配置中 Web UI、实时转写、任务持久化默认开启）。

Docker 方式：

```bash
docker run -d --gpus all -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest --web
```

> Windows 部署、CPU/ARM64 模式、docker-compose、局域网访问、API 认证等：见 [部署指南](docs/deployment.md)。

## 文档导航

| 文档 | 内容 |
|------|------|
| [部署指南](docs/deployment.md) | 系统要求、Linux / Windows / Docker 部署、三种运行模式、Web UI、安全终止 |
| [配置文档](docs/configuration.md) | 启动参数全表、config.yaml 配置文件、环境变量、任务持久化、内置常量 |
| [API 文档 v2（默认）](docs/api/v2.md) | 离线批处理、健康检查 / 能力查询、实时转写 WebSocket 协议 |
| [API 文档 v1（兼容）](docs/api/v1.md) | 旧客户端兼容说明与版本演进约定 |
| [架构说明](docs/architecture.md) | 项目结构、处理流程、关键设计 |

---

如果这个项目对你有帮助，欢迎给 [GitHub 仓库](https://github.com/LanceLRQ/qwen3-asr-service) 和 [Docker Hub](https://hub.docker.com/r/lancelrq/qwen3-asr-service) 点个 ⭐，你的支持是项目持续更新的动力！
