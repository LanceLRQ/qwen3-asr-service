# Development Guide

[中文](development.md) | **English**

For contributors: dev environment, testing, end-to-end smoke, key code conventions. For architecture and pipeline see [Architecture](architecture_EN.md); for the full parameter table see [Configuration](configuration_EN.md).

## Table of Contents

- [1. Dev environment](#1-dev-environment)
- [2. Dependency layers](#2-dependency-layers)
- [3. Running tests](#3-running-tests)
- [4. End-to-end smoke (E2E)](#4-end-to-end-smoke-e2e)
- [5. Key conventions](#5-key-conventions)
  - [5.1 Startup params: single schema](#51-startup-params-single-schema)
  - [5.2 Documentation](#52-documentation)
  - [5.3 Compatibility-layer extension](#53-compatibility-layer-extension)
  - [5.4 Realtime WebSocket](#54-realtime-websocket)
- [6. Running the service locally](#6-running-the-service-locally)
- [7. Commit conventions](#7-commit-conventions)
- [8. Release & image build](#8-release--image-build)

---

## 1. Dev environment

- **Python 3.12** (strict, matching `setup.ps1`/`setup.bat`).
- Recommended virtualenv:

```bash
cd asr-service
python3.12 -m venv venv
venv/bin/python -m pip install -r requirements.txt        # runtime
venv/bin/python -m pip install -r requirements-test.txt   # tests
```

> Models auto-download on first start (ModelScope/HuggingFace); see [Deployment](deployment_EN.md).

## 2. Dependency layers

| File | Purpose | In runtime image |
|------|------|----------------|
| `requirements.txt` | Runtime (torch/funasr/qwen_asr/fastapi/httpx …) | ✅ |
| `requirements-cpu.txt` | CPU/OpenVINO variant | ✅ (per deployment) |
| `requirements-test.txt` | Unit tests (pytest stack + httpx) | ❌ CI/dev only |
| `scripts/e2e/requirements.txt` | E2E smoke isolated env (incl. official SDKs) | ❌ manual E2E only |

Add **runtime** deps to `requirements.txt` (e.g. `httpx` for the compat DashScope download); test-only deps to `requirements-test.txt`.

## 3. Running tests

```bash
cd asr-service
venv/bin/python -m pytest tests/unit -q              # all unit tests
venv/bin/python -m pytest tests/unit/api -q --no-cov # one dir, no coverage
venv/bin/python -m pytest tests/unit/api/test_compat_openai.py::test_json_default
```

**Unit-test conventions** (see `tests/conftest.py`):

- **Do not modify source**: verify only via mock / monkeypatch / dependency injection (`init_routes`/`init_compat` …).
- **No real models, no network, no long waits**: mock heavy model/network calls.
- HTTP routes use `make_client` (TestClient + injected fake TaskManager); tasks use `tm_factory`; audio uses `make_wav` (silent WAV).
- When you change `ARG_SPECS` (startup params), sync the `LEGACY_DEFAULTS` snapshot in `tests/unit/utils/test_arg_schema.py`.

> Tests go through `TestClient` (direct ASGI), not the real uvicorn HTTP/WS stack — so green unit tests don't guarantee real sockets work (see the proxy gotcha in §6). Validate the real path with the E2E in §4.

## 4. End-to-end smoke (E2E)

`scripts/e2e/` provides an isolated-venv one-shot smoke that drives a running service with real clients, validating `/compat/*` against upstream SDK/protocol contracts (the "actual SDK fields/encoding, WS handshake" that unit mocks can't cover).

```bash
cd asr-service/scripts/e2e
./run.sh                                              # one-shot mock test (no real model)
./run.sh --base-url http://127.0.0.1:8765 --api-key sk-xxx   # test a real service
./run.sh --list                                       # list all checks
```

- First run auto-creates `.venv` (gitignored) and installs `requirements.txt`.
- By default starts a **mock service** (`mock_server.py`: reuses project compat code + fixed results, no real model), validating endpoint routing / SDK field alignment / WS handshake / SSE / error codes / DashScope download path; transcript text is a fixed mock value.
- Offline uses official SDKs (openai / dashscope), realtime uses raw WebSocket. See [scripts/e2e/README.md](../asr-service/scripts/e2e/README.md).

## 5. Key conventions

### 5.1 Startup params: single schema

All startup params are defined **once** in `ARG_SPECS` (`app/utils/arg_schema.py`), driving argparse, config-file validation and `config.example.yaml` together (no drift across CLI / file / example). To add a param:

1. Add an `ArgSpec` to `ARG_SPECS` (with a `group`, don't leave the default "其他/Other").
2. Write the corresponding `cfg.*` in `app/main.py:_apply_cli_config`; declare the default in `app/config.py`.
3. Add a commented example to `config.example.yaml`.
4. Sync `LEGACY_DEFAULTS` in `tests/unit/utils/test_arg_schema.py`.

> argparse uses `default=SUPPRESS` everywhere; real defaults live in the schema — this is the basis of override precedence (defaults < env < config file < CLI).

### 5.2 Documentation

- Public docs live under `docs/`, **bilingual**: Chinese base name + English `_EN` suffix.
- Docs shown in the Web UI doc center must be registered in `_NAV_ORDER`/`_NAV_TITLES` of `app/web/docs_site.py` (whitelisted dirs `docs`/`docs/api`/`docs/api/v2` are auto-scanned).
- `docs/plan/` is **local research material** (gitignored, never versioned, never indexed by `docs_site`); put public docs under whitelisted dirs like `docs/api/`.

### 5.3 Compatibility-layer extension

The OpenAI / DashScope compat layer lives in `app/api/compat/` (public contract in [Compatibility APIs](api/compat_EN.md)):

| Module | Responsibility |
|------|------|
| `mappers.py` | result/final ↔ upstream formats (**pure functions**, main unit-test target; mind the sec/ms unit red line) |
| `errors.py` | OpenAI / DashScope error envelopes, dispatched by **exception type** (doesn't pollute v2's `{detail}`) |
| `openai_routes.py` / `dashscope_routes.py` | HTTP controllers + router factories |
| `openai_ws_routes.py` / `dashscope_ws_routes.py` | realtime adapters (see §5.4) |
| `fetch.py` | DashScope file_urls server-side download (SSRF guard, `trust_env=False` to prevent proxy bypass) |
| `__init__.py` | `init_compat` dependency injection |

Conventions: **reuse first** (auth via `routes.api_key_matches`, upload via `ALLOWED_EXTENSIONS`/`UPLOAD_CHUNK_SIZE`, offline via `TaskManager`); put mapping logic in pure functions for unit tests; **honestly degrade** unsupported upstream capabilities (ignore+log / 501 / placeholder with a doc note) — never silently fake.

### 5.4 Realtime WebSocket

Realtime compat reuses the shared skeleton `app/api/compat/ws_bridge.py` (`run_compat_ws`): auth / acquire admission / decoupled receive-consume / frame-size·backlog caps / session timeout / connection reuse / release are centralized; protocol differences go into a **per-connection adapter** (duck interface: `on_open`/`classify`/`on_configured`/`translate_finals`/`translate_error`/`on_finish` + `reusable`). Adding a new upstream realtime protocol = one adapter + router factory, leaving the skeleton untouched; v2's `ws_routes.py` is a stable endpoint and is **not modified**.

## 6. Running the service locally

```bash
cd asr-service
venv/bin/python -m app.main --web --enable-stream \
  --enable-openai-api --enable-dashscope-api --compat-fetch-allow-private --api-key sk-xxx
```

- `--web` enables `/web-ui` (incl. `/web-ui/docs` doc center and the `stream.html` realtime test page).
- Compat realtime WS needs `--enable-stream` in standard mode (under vLLM mode `--serve-mode vllm` it auto-mounts with the compat switches, no `--enable-stream`); DashScope offline needs `--compat-fetch-allow-private` (allow downloading local audio URLs).

> ⚠️ **HTTP proxy gotcha**: if the environment sets `http_proxy`/`all_proxy`, clients reaching `127.0.0.1` get intercepted by the proxy (shows up as 503 / WebSocket handshake failure). When connecting to a local service, bypass it: `export NO_PROXY=127.0.0.1,localhost` (the E2E tool does this automatically).

## 7. Commit conventions

- Branch off the default branch for feature work; use Conventional Commits prefixes (`feat`/`fix`/`test`/`docs`…), Chinese body.
- Run `git add` and `git commit` **separately** (avoid index.lock).
- Run relevant unit tests before committing; when changing a public contract, update `docs/api/` docs and the doc-center registration.

## 8. Release & image build

Image publishing is automated via GitHub Actions: pushing a `vX.Y.Z` git tag triggers `.github/workflows/docker-publish.yml`, which builds and pushes images to Alibaba Cloud Container Registry.

```bash
git tag v2.0.1
git push origin v2.0.1
# → registry.cn-hangzhou.aliyuncs.com/ripper/qwen3-asr-service:2.0.1
# → registry.cn-hangzhou.aliyuncs.com/ripper/qwen3-asr-service:2.0.1-cpu / 2.0.1-vllm + matching latest tags
```

| Variant | Dockerfile | Platform | Tags produced |
|---------|-----------|----------|---------------|
| GPU | `docker/Dockerfile` | linux/amd64 | `X.Y.Z` + `latest` |
| CPU | `docker/Dockerfile.cpu` | linux/amd64 | `X.Y.Z-cpu` + `latest-cpu` |
| vLLM | `docker/Dockerfile.vllm` | linux/amd64 | `X.Y.Z-vllm` + `latest-vllm` |

- **Version injection**: the tag's version (with the `v` prefix stripped) is passed via `--build-arg APP_VERSION` into the image; `main.py` reads it through `os.environ.get("APP_VERSION")`, surfacing as `info.version` in FastAPI `/openapi.json`. Falls back to `dev` (local manual build) / `2.0.0` (running source directly) when unset.
- **First-release prerequisite**: configure `ALIYUN_REGISTRY_USERNAME` and `ALIYUN_REGISTRY_PASSWORD` (Alibaba Cloud Container Registry username and password/access credential) under repo **Settings → Secrets and variables → Actions**, otherwise the login step fails.
- **Local manual build**: `bash docker/build.sh` still builds a single variant interactively (now also injects `APP_VERSION`); CI has no GPU, so runtime verification of the GPU image requires a local machine with a card.
