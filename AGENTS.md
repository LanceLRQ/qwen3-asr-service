# Repository Guidelines

## Project Structure & Module Organization

This repository contains a Python ASR API service. Core source code lives in `asr-service/app`, organized by API routes, compatibility adapters, engines, runtime services, pipeline logic, and utilities. Tests live in `asr-service/tests`, split into `unit` and `integration`. Public documentation is under `docs`; keep Chinese docs as the base filename and English docs with `_EN` suffix. Docker assets are in `docker`, and root `manage.sh` / `manage.ps1` provide interactive service management.

## Build, Test, and Development Commands

- `bash manage.sh`: recommended Linux/macOS entry for guided setup, start, stop, and Docker/venv management.
- `cd asr-service && bash setup.sh`: create/update the standard virtual environment and install runtime dependencies.
- `cd asr-service && bash start.sh --web`: start the local service and Web UI.
- `PYTHONPATH=asr-service .venv/bin/python -m pytest asr-service/tests/unit -q`: run unit tests with coverage from `pytest.ini`.
- `cd asr-service/scripts/e2e && ./run.sh --list`: list available E2E smoke checks.

## Coding Style & Naming Conventions

Use Python 3.12 and follow PEP 8 with 4-space indentation. Always use the repository virtual environment for Python commands, scripts, and tests; run `.venv/bin/python` / `.venv/bin/python -m pytest` from the repository root instead of system `python`, `python3`, or `pytest`. Prefer small, pure functions for mapping and parsing logic, especially in `app/api/compat` and `app/utils`. Keep startup parameters centralized in `app/utils/arg_schema.py`; update `app/main.py`, `config.example.yaml`, and related tests when adding options. Name test files `test_*.py` and test functions `test_*`.

## Testing Guidelines

Unit tests use pytest, pytest-asyncio, pytest-mock, and pytest-cov. Avoid real models, network calls, and long waits in unit tests; use mocks, monkeypatching, and dependency injection. Route tests should use fixtures from `tests/conftest.py`. For public API or WebSocket changes, run focused unit tests plus the E2E smoke script against a mock or real service.

## Commit & Pull Request Guidelines

Git history follows Conventional Commits, for example `fix(pipeline): ...`, `docs(readme): ...`, and `test(api): ...`. Use concise subjects and include Chinese detail when helpful. Pull requests should describe the change, list verification commands, link related issues, and include screenshots for Web UI changes. Update `docs/api` and doc-center registration when public contracts change.

## Security & Configuration Tips

Do not commit local `config.yaml`, API keys, generated databases, downloaded models, or private audio samples. Prefer documented config flags and environment variables over hard-coded paths or secrets.
