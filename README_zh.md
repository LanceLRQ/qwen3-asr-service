# Qwen3-ASR Service

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![GitHub stars](https://img.shields.io/github/stars/LanceLRQ/qwen3-asr-service?style=flat-square&logo=github)](https://github.com/LanceLRQ/qwen3-asr-service/stargazers)
[![Docker Pulls](https://img.shields.io/docker/pulls/lancelrq/qwen3-asr-service?style=flat-square&logo=docker)](https://hub.docker.com/r/lancelrq/qwen3-asr-service)
[![Docker Image Size](https://img.shields.io/docker/image-size/lancelrq/qwen3-asr-service/latest?style=flat-square&logo=docker)](https://hub.docker.com/r/lancelrq/qwen3-asr-service)
[![Docker Build](https://img.shields.io/github/actions/workflow/status/LanceLRQ/qwen3-asr-service/docker-publish.yml?style=flat-square&logo=githubactions&logoColor=white&label=Docker%20Build)](https://github.com/LanceLRQ/qwen3-asr-service/actions/workflows/docker-publish.yml)
[![Powered by Qwen3-ASR](https://img.shields.io/badge/Powered%20by-Qwen3--ASR-ff6a00?style=flat-square)](https://github.com/QwenLM)

**中文** | [English](README.md)

基于 Qwen3-ASR 的简单、快速、高效语音识别 API 服务。开箱即用，离线长音频与实时流式转写一体，支持说话人分离 / 声纹库识别，并提供多功能、美观实用的 Web UI。Linux / macOS / Windows 多平台兼容，支持 Docker 容器部署，支持 GPU（CUDA）和 CPU（OpenVINO INT8）双模式推理。

## 特性

- ⚡ **启动快、转写快** - 服务启动迅速；长音频转写耗时远低于音频时长，GPU 模式尤为显著，CPU 模式经 OpenVINO INT8 量化同样高效
- **实时转写** - WebSocket 流式端点，麦克风/推流音频逐句返回结果
- **vLLM 原生流式引擎** *（v2.1.0 新增，可选）* - 独立的纯 GPU 运行模式（`--serve-mode vllm`），句内实时 partial→final 增量解码 + 长音频逐块转写，详见 [vLLM 与 standard 差异](docs/vllm-vs-standard.md)
- **说话人分离** - 离线/实时转写标注匿名说话人标签 A/B/C…（CAM++ 声纹模型，CPU 推理）
- **声纹库识别** - 登记说话人后转写直接显示真名；未知说话人自动登记占位名，Web 管理页一键改名（speakers.db，强制鉴权）
- **远场过滤 / 参数可调** - 实时段级能量/SNR 门控减少远场与环境音误触发；说话人、断句、输出等参数支持按请求/会话动态覆盖
- **OpenAI / DashScope 兼容接口** - 改 base_url 即对接 OpenAI / 阿里云 DashScope 生态（离线 + 实时），无需改动业务代码
- **异步任务 + 持久化** - 提交后轮询结果，任务结果跨重启可查（tasks.db）
- **Web UI** - 现代化界面（Vue 3 + Naive UI，暗色主题）：离线转写、实时转写、说话人管理、任务历史自动刷新与离线文档中心
- **灵活配置** - YAML 配置文件 / 命令行参数 / 环境变量四层优先级
- **开箱即用** - 一键安装部署，自动下载模型，首次启动自动生成配置文件
- **长语音支持** - 1s ~ 4 小时音频文件，自动 VAD 切片处理
- **多格式支持** - WAV / MP3 / FLAC / M4A / AAC / OGG 等
- **时间戳支持** - 句子级 / 单词级时间戳（GPU 模式）
- **准确分句** *（v2.2.0 新增）* - 按标点 + 停顿 + 说话人切换重组句子，与处理切块时长解耦（不再按固定时长拦腰切句）
- **自动标点** - 集成 CT-Transformer 标点恢复模型
- **API 认证** - 可选的 Bearer Token 认证
- **交互式管理** - CLI 管理脚本，支持 Docker / venv 双模式一键管理

## 快速开始

> 依赖：Python 3.10+、ffmpeg；GPU 模式需 NVIDIA GPU + CUDA 12.1+（详见[部署指南](docs/deployment.md)）。

**推荐**：在仓库根目录运行交互式管理脚本（Docker / venv 统一入口，向导式安装与启停）：

```bash
bash manage.sh          # Linux / macOS；Windows 用 PowerShell 运行 .\manage.ps1
```

> 也可手动分步执行（进入应用目录）：
> ```bash
> cd asr-service
> bash setup.sh        # 初始化环境
> bash start.sh        # 启动服务（自动检测设备、下载模型、生成 config.yaml）
> ```

```bash
# 验证
curl http://127.0.0.1:8765/v2/health
```

> ⚠️ **从 v1 升级**：若此前已安装过 v1 的虚拟环境，v2 新增了依赖（说话人分离、声纹库、文档中心等），需更新依赖后再启动。重新执行 `bash setup.sh`，提示是否重建 venv 时选 `N` 保留原环境即可——脚本仍会安装/更新 `requirements.txt` 中的新依赖。

浏览器访问 `http://127.0.0.1:8765/web-ui` 即可上传音频体验（自动生成的配置中 Web UI、实时转写、任务持久化默认开启）。

Docker 方式：

```bash
docker run -d --gpus all -p 8765:8765 \
  -v ./asr-service/models:/app/models \
  --name qwen3-asr-service \
  lancelrq/qwen3-asr-service:latest --web
```

> Windows 部署、CPU/ARM64 模式、docker-compose、局域网访问、API 认证等：见 [部署指南](docs/deployment.md)。

## 预览

| 离线转写 | 实时转写 |
| :---: | :---: |
| ![离线转写](docs/images/offline.webp) | ![实时转写](docs/images/online.webp) |

## 文档导航

| 文档 | 内容 |
|------|------|
| [部署指南](docs/deployment.md) | 系统要求、Linux / Windows / Docker 部署、三种运行模式、Web UI、安全终止 |
| [配置文档](docs/configuration.md) | 启动参数全表、config.yaml 配置文件、环境变量、任务持久化、内置常量 |
| [vLLM 与 standard 差异](docs/vllm-vs-standard.md) | vLLM 引擎相对默认 standard 模式的功能差异（选型与升级参考） |
| [API 文档 v2（默认）](docs/api/v2.md) | 离线批处理、健康检查 / 能力查询、实时转写 WebSocket 协议 |
| [API 文档 v1（兼容）](docs/api/v1.md) | 旧客户端兼容说明与版本演进约定 |
| [兼容接口](docs/api/compat.md) | OpenAI / 阿里云 DashScope drop-in 兼容（离线 + 实时），改 base_url 即接入 |
| [架构说明](docs/architecture.md) | 项目结构、处理流程、关键设计 |
| [开发指南](docs/development.md) | 开发环境、测试、端到端冒烟、单一 schema / 文档 / 兼容层扩展约定 |
