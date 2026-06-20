# 开发指南

**中文** | [English](development_EN.md)

面向贡献者：开发环境、测试、端到端冒烟、关键代码约定。架构与处理流程见 [架构说明](architecture.md)，参数全表见 [配置文档](configuration.md)。

## 目录

- [1. 开发环境](#1-开发环境)
- [2. 依赖分层](#2-依赖分层)
- [3. 运行测试](#3-运行测试)
- [4. 端到端冒烟（E2E）](#4-端到端冒烟e2e)
- [5. 关键开发约定](#5-关键开发约定)
  - [5.1 启动参数：单一 schema](#51-启动参数单一-schema)
  - [5.2 文档](#52-文档)
  - [5.3 兼容接口扩展](#53-兼容接口扩展)
  - [5.4 实时 WebSocket](#54-实时-websocket)
- [6. 本地起服务调试](#6-本地起服务调试)
- [7. 提交约定](#7-提交约定)
- [8. 发布与镜像构建](#8-发布与镜像构建)

---

## 1. 开发环境

- **Python 3.12**（严格要求，与 `setup.ps1`/`setup.bat` 一致）。
- 建议虚拟环境：

```bash
cd asr-service
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt        # 运行期
venv/bin/python -m pip install -r requirements-test.txt   # 测试
```

> 模型首次启动按需自动下载（ModelScope/HuggingFace），见 [部署指南](deployment.md)。

## 2. 依赖分层

| 文件 | 用途 | 是否进运行期镜像 |
|------|------|----------------|
| `requirements.txt` | 运行期（torch/funasr/qwen_asr/fastapi/httpx 等） | ✅ |
| `requirements-cpu.txt` | CPU/OpenVINO 变体 | ✅（按部署选） |
| `requirements-test.txt` | 单元测试（pytest 系 + httpx） | ❌ 仅 CI/开发 |
| `scripts/e2e/requirements.txt` | 端到端冒烟独立环境（含官方 SDK） | ❌ 仅手动 E2E |

新增**运行期**依赖加到 `requirements.txt`（如兼容层 DashScope 下载用的 `httpx`）；仅测试用的加到 `requirements-test.txt`。

## 3. 运行测试

```bash
cd asr-service
venv/bin/python -m pytest tests/unit -q              # 全部单元测试
venv/bin/python -m pytest tests/unit/api -q --no-cov # 单目录、关掉覆盖率
venv/bin/python -m pytest tests/unit/api/test_compat_openai.py::test_json_default
```

**单元测试约定**（见 `tests/conftest.py`）：

- **不修改源代码**：只通过 mock / monkeypatch / 依赖注入（`init_routes`/`init_compat` 等）验证。
- **不加载真实模型、不触网、无长等待**：重模型/网络一律 mock。
- HTTP 路由用 `make_client`（TestClient + 注入假 TaskManager），任务用 `tm_factory`，音频用 `make_wav`（静音 WAV）。
- 改了 `ARG_SPECS`（启动参数）务必同步 `tests/unit/utils/test_arg_schema.py` 的 `LEGACY_DEFAULTS` 快照。

> 测试经 `TestClient`（ASGI 直连）不经真实 uvicorn HTTP/WS 栈——所以单测全绿不代表真 socket 一定通（见 §6 代理坑）。真实链路用 §4 的 E2E 验证。

## 4. 端到端冒烟（E2E）

`scripts/e2e/` 提供独立 venv 的一键冒烟，用真实客户端连服务，验证 `/compat/*` 与上游 SDK/协议契约对齐（单测 mock 覆盖不到的「SDK 实际字段/编码、WS 握手」）。

```bash
cd asr-service/scripts/e2e
./run.sh                                              # 一键 mock 测试（无需真模型）
./run.sh --base-url http://127.0.0.1:8765 --api-key sk-xxx   # 改测真实服务
./run.sh --list                                       # 列出所有 check
```

- 首次运行自动建 `.venv`（已 gitignore）并装 `requirements.txt`。
- 默认起 **mock 服务**（`mock_server.py`：复用项目 compat 代码 + 固定结果，免真模型），验证端点路由 / SDK 字段对齐 / WS 握手 / SSE / 错误码 / DashScope 下载链路；转写文本是 mock 固定值。
- 离线走官方 SDK（openai / dashscope），实时走裸 WebSocket。详见 [scripts/e2e/README.md](../asr-service/scripts/e2e/README.md)。

## 5. 关键开发约定

### 5.1 启动参数：单一 schema

所有启动参数在 `app/utils/arg_schema.py` 的 `ARG_SPECS` **一处定义**，同时驱动 argparse、配置文件校验与 `config.example.yaml`（避免 CLI / 文件 / 示例三处漂移）。新增参数：

1. 在 `ARG_SPECS` 加一条 `ArgSpec`（含 `group` 分组，勿留默认「其他」）。
2. 在 `app/main.py:_apply_cli_config` 写入对应 `cfg.*`；新配置项在 `app/config.py` 声明默认值。
3. 在 `config.example.yaml` 增注释示例。
4. 同步 `tests/unit/utils/test_arg_schema.py` 的 `LEGACY_DEFAULTS`。

> argparse 一律 `default=SUPPRESS`，实义默认值收敛到 schema——这是配置覆盖优先级（默认 < 环境变量 < 配置文件 < CLI）的根基。

### 5.2 文档

- 对外文档放 `docs/`，**双语**：中文基名 + 英文 `_EN` 后缀。
- 进 Web UI 文档中心的文档需在 `app/web/docs_site.py` 的 `_NAV_ORDER`/`_NAV_TITLES` 注册（白名单目录 `docs`/`docs/api`/`docs/api/v2` 自动扫描）。
- `docs/plan/` 是**本地调研稿**（已 gitignore，不进版本控制、`docs_site` 绝不收录）；正式对外文档放 `docs/api/` 等白名单目录。

### 5.3 兼容接口扩展

OpenAI / DashScope 兼容层在 `app/api/compat/`（对外契约见 [兼容接口](api/compat.md)）：

| 模块 | 职责 |
|------|------|
| `mappers.py` | result/final ↔ 上游格式（**纯函数**，单测主战场；注意秒/毫秒单位红线） |
| `errors.py` | OpenAI / DashScope 错误信封，按**异常类型**分派（不污染 v2 的 `{detail}`） |
| `openai_routes.py` / `dashscope_routes.py` | HTTP 控制器 + 路由工厂 |
| `openai_ws_routes.py` / `dashscope_ws_routes.py` | 实时 adapter（见 §5.4） |
| `fetch.py` | DashScope file_urls 服务端下载（SSRF 防护，`trust_env=False` 防代理绕过） |
| `__init__.py` | `init_compat` 依赖注入 |

约定：**复用优先**（鉴权用 `routes.api_key_matches`、落盘复用 `ALLOWED_EXTENSIONS`/`UPLOAD_CHUNK_SIZE`、离线复用 `TaskManager`）；映射逻辑放纯函数便于单测；不支持的上游能力**诚实降级**（忽略+日志 / 501 / 占位并文档标注），绝不静默伪造。

### 5.4 实时 WebSocket

实时兼容复用共享骨架 `app/api/compat/ws_bridge.py`（`run_compat_ws`）：鉴权 / acquire 准入 / 收发解耦 / 帧大小·积压上限 / 会话超时 / 连接复用 / release 集中一处，协议差异收进**每连接一个的 adapter**（鸭子接口：`on_open`/`classify`/`on_configured`/`translate_finals`/`translate_error`/`on_finish` + `reusable`）。新增一种上游实时协议 = 写一个 adapter + 路由工厂，不动骨架；v2 的 `ws_routes.py` 是稳定端点，**不侵入**。

## 6. 本地起服务调试

```bash
cd asr-service
venv/bin/python -m app.main --web --enable-stream \
  --enable-openai-api --enable-dashscope-api --compat-fetch-allow-private --api-key sk-xxx
```

- `--web` 开 `/web-ui`（含 `/web-ui/docs` 文档中心、`stream.html` 实时测试页）。
- 兼容实时 WS 在 standard 模式需 `--enable-stream`（vLLM 模式 `--serve-mode vllm` 下随兼容开关自动挂载，无需 `--enable-stream`）；DashScope 离线需 `--compat-fetch-allow-private`（允许下载本地音频 URL）。

> ⚠️ **HTTP 代理坑**：若环境设了 `http_proxy`/`all_proxy`，客户端访问 `127.0.0.1` 会被代理拦截（表现为 503 / WebSocket 握手失败）。本地连本地服务务必绕过：`export NO_PROXY=127.0.0.1,localhost`（E2E 工具已内置绕过）。

## 7. 提交约定

- 从默认分支切 feature 分支开发；提交信息用 Conventional Commits 前缀（`feat`/`fix`/`test`/`docs`…），正文中文。
- `git add` 与 `git commit` **分开执行**（避免 index.lock）。
- 改代码先跑相关单测；改了对外契约同步更新 `docs/api/` 文档与文档中心注册。

## 8. 发布与镜像构建

镜像发布由 GitHub Actions 自动完成：推送 `vX.Y.Z` 形式的 git tag 即触发 `.github/workflows/docker-publish.yml`，并行构建并推送镜像到阿里云容器镜像服务。

```bash
git tag v2.0.1
git push origin v2.0.1
# → registry.cn-hangzhou.aliyuncs.com/ripper/qwen3-asr-service:2.0.1
# → registry.cn-hangzhou.aliyuncs.com/ripper/qwen3-asr-service:2.0.1-cpu / 2.0.1-vllm + 对应 latest 系列
```

| 变体 | Dockerfile | 平台 | 产出 tag |
|------|-----------|------|---------|
| GPU | `docker/Dockerfile` | linux/amd64 | `X.Y.Z` + `latest` |
| CPU | `docker/Dockerfile.cpu` | linux/amd64 | `X.Y.Z-cpu` + `latest-cpu` |
| vLLM | `docker/Dockerfile.vllm` | linux/amd64 | `X.Y.Z-vllm` + `latest-vllm` |

- **版本号注入**：tag 的版本号（去掉 `v` 前缀）经 `--build-arg APP_VERSION` 写入镜像，`main.py` 通过 `os.environ.get("APP_VERSION")` 读取，体现在 FastAPI `/openapi.json` 的 `info.version`。未注入时回退 `dev`（本地手工构建）/ `2.0.0`（源码直跑）。
- **首次发版前置**：需在仓库 **Settings → Secrets and variables → Actions** 配置 `ALIYUN_REGISTRY_USERNAME` 与 `ALIYUN_REGISTRY_PASSWORD`（阿里云容器镜像服务登录账号与密码/访问凭证），否则登录步骤失败。
- **本地手工构建**：仍可用 `bash docker/build.sh` 交互式构建单个变体（已同步注入 `APP_VERSION`）；CI 无 GPU，GPU 镜像的运行验证需本地有卡环境。
