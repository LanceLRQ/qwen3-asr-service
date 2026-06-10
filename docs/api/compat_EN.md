# Compatibility APIs (OpenAI / Alibaba Cloud DashScope)

[← API v2 Overview](v2_EN.md) ｜ [中文](compat.md) | **English**

Drop-in compatibility shims for clients already built against the **OpenAI** or **Alibaba Cloud DashScope (Paraformer)** speech ecosystems: just point the SDK's base url at this service's `/compat/...` prefix — no business-code changes. The compat layer is an adapter shim that reuses the existing transcription pipeline and task queue, fully isolated from the native `/v1` and `/v2`.

> Compat APIs are **off by default**; enable them explicitly at startup:
> ```bash
> # offline + realtime (realtime also needs --enable-stream)
> python -m app.main --enable-openai-api --enable-dashscope-api --enable-stream --api-key sk-xxx
> ```

**Design principle**: honest degradation — capabilities this service lacks (translation, temperature, prompt, hotwords, per-token deltas, etc.) are **explicitly ignored/warned or rejected, never silently faked**.

## Documentation map

The compat APIs are split by upstream ecosystem into two sub-documents:

| Sub-document | Contents |
|--------------|----------|
| [OpenAI Compatibility API](compat/openai_EN.md) | Transcription `POST /audio/transcriptions`, SSE streaming, translation (501), models, realtime `WS /realtime`, OpenAI error codes |
| [DashScope Compatibility API](compat/dashscope_EN.md) | Recorded-file recognition (submit / poll / second-hop), realtime `WS /inference`, DashScope error codes |

This page covers cross-ecosystem common ground: client pointing, authentication, capability cheatsheet, and compat-vs-native tradeoffs.

## 1. Pointing clients at this service

Both upstream SDKs support overriding the base url — point it at this service's `/compat/...` prefix:

| Upstream | Setting | Target |
|------|--------|------|
| OpenAI Python SDK | `OpenAI(base_url=...)` | `http://<host>:8765/compat/openai/v1` |
| OpenAI realtime | ws base | `ws://<host>:8765/compat/openai/v1` |
| DashScope SDK | `dashscope.base_http_api_url` | `http://<host>:8765/compat/dashscope/api/v1` |
| DashScope realtime | `dashscope.base_websocket_api_url` | `ws://<host>:8765/compat/dashscope/api-ws/v1` |

Sub-paths after the prefix are preserved verbatim (hardcoded by the SDKs), so compat paths never collide with `/v1` or `/v2`.

## 2. Authentication

When the service is started with `--api-key`, all compat endpoints require `Authorization: Bearer <api-key>` (matching both SDKs' default behaviour). Realtime WS also accepts the query param `?token=<api-key>`. Without an api-key, requests pass through (not recommended in production).

## Capabilities & limits

| Capability | OpenAI compat | DashScope compat |
|------|------------|---------------|
| Offline transcription | ✅ transcriptions | ✅ recorded-file recognition |
| Local file upload | ✅ multipart | ❌ file_urls only (URLs) |
| Word-level timestamps | ✅ verbose_json + word | ✅ words[] |
| Speaker diarization | ➖ (no OpenAI field) | ✅ diarization_enabled |
| Translation | ❌ 501 | ➖ n/a |
| HTTP streaming | ✅ stream=true (SSE) | ➖ n/a |
| Realtime whole-sentence | ✅ completed (needs --enable-stream) | ✅ result-generated (needs --enable-stream) |
| Realtime per-token delta | ❌ needs vLLM | ❌ needs vLLM |
| Confidence/logprob | ➖ placeholder | ➖ not provided |
| Prompt/temperature/hotwords/disfluency | ❌ ignored | ❌ ignored |

## Compat vs native v2

- Need to **integrate an existing OpenAI/DashScope ecosystem** (SDKs, off-the-shelf clients) → use the compat APIs.
- Need this service's **full feature set** (voiceprint DB, task list/cancel, unified realtime envelope, per-request parameter overrides) → use native [API v2](v2_EN.md).
- Very long audio: the OpenAI sync endpoint is bounded by `--openai-sync-timeout`; for very long clips prefer the DashScope async compat or native v2 async.
