# Qwen3-ASR Service Architecture

[中文](architecture.md) | **English**

## Project Structure

```
asr-service/
├── app/
│   ├── main.py                    # Service entry (argument parsing + serve-mode assembly)
│   ├── config.py                  # Global configuration
│   ├── api/
│   │   ├── routes.py              # Offline batch routes (v1/v2 factory)
│   │   ├── common_routes.py       # health / capabilities shared routes
│   │   ├── ws_routes.py           # Real-time transcription WebSocket endpoint
│   │   ├── schemas.py             # Request/response data models
│   │   └── ws_schemas.py          # Real-time envelope message models
│   ├── engines/
│   │   ├── qwen_asr_engine.py     # Qwen3-ASR recognition engine (GPU)
│   │   ├── openvino_asr_engine.py # OpenVINO ASR engine (CPU)
│   │   ├── processor_numpy.py     # Pure NumPy Mel extraction + BPE decoding
│   │   ├── vad_engine.py          # FSMN-VAD voice activity detection engine
│   │   └── punc_engine.py         # CT-Transformer punctuation engine
│   ├── pipeline/
│   │   ├── asr_pipeline.py        # ASR pipeline orchestration
│   │   └── audio_preprocessor.py  # ffmpeg format conversion
│   ├── runtime/
│   │   ├── device.py              # Device detection and selection
│   │   ├── task_manager.py        # Task queue management
│   │   ├── task_store.py          # Offline task persistence (tasks.db)
│   │   └── stream_session.py      # Real-time session (online VAD segmentation)
│   ├── web/
│   │   ├── views.py               # Web UI routes
│   │   ├── page.py                # Page loading
│   │   ├── index.html             # Offline transcription demo page
│   │   └── stream.html            # Real-time transcription test page
│   └── utils/
│       ├── logger.py              # Logging configuration
│       ├── arg_schema.py          # Single startup-parameter schema (argparse/config file)
│       ├── config_file.py         # config.yaml discovery/bootstrap/validation/merge
│       ├── model_manager.py       # Model download management
│       └── openvino_model_downloader.py  # OpenVINO model download
├── models/                        # Model storage (auto-downloaded, not committed)
├── data/                          # Task persistence database (tasks.db, not committed)
├── cache/                         # Runtime cache (uploads, audio segments)
├── logs/                          # Log files
├── setup.sh / setup.bat           # Environment initialization
├── start.sh / start.bat           # Service startup
├── cli.sh / cli.bat               # Interactive CLI management script
└── requirements.txt               # Dependencies

# Project root
├── Dockerfile                     # Docker image build
├── docker-compose.yml             # Docker Compose orchestration
└── build.sh                       # Image build script
```

## Processing Pipeline

**GPU mode:**

```
Audio File → ffmpeg convert (16kHz WAV) → VAD segmentation → Segment merge → ASR recognition → [Punctuation] → Output
                                          (FSMN-VAD)         (≤5s)          (Qwen3-ASR)       (CT-Transformer)
                                                                                ↓
                                                                     [Optional] Alignment (ForcedAligner)
```

**CPU mode (OpenVINO):**

```
Audio File → ffmpeg convert (16kHz WAV) → VAD segmentation → Segment merge → ASR recognition → [Punctuation] → Output
                                          (FSMN-VAD          (≤5s)          (OpenVINO          (CT-Transformer
                                           ONNX)                              INT8)               ONNX)
                                                                ↓
                                              NumPy Mel extraction → audio_encoder
                                                                   → thinker_embeddings
                                                                   → decoder autoregressive decoding
                                                                   → BPE decode
```

**Real-time transcription (Route B, `WS /v2/asr/stream`):**

```
Client audio frames (PCM16) → online VAD chunking (200ms) → speech segmentation → in-memory offline decoding (shared ASR engine) → per-sentence final results
                                                            (silence-based splits / 12s long-sentence fallback)
```

Offline and real-time share the same model engines (VAD/ASR/punctuation); the real-time side is isolated from offline contention via session-level buffering and admission control (session count / decoding concurrency caps).

## Key Design Decisions

- **Engine pattern**: each model (ASR/VAD/punctuation/alignment) is wrapped in an independent engine; load failures degrade by importance (VAD/ASR failures abort startup, punctuation failure degrades to disabled).
- **v1/v2 route factories**: the same controller functions are registered under both prefixes; all protocol changes are additive, so old clients never break.
- **Task queue**: a single worker thread processes serially with a thread pool providing true timeouts; optional write-through persistence ([tasks.db](configuration_EN.md#offline-task-persistence-tasksdb)) — persistence failures never affect task execution.
- **Configuration chain**: a single startup-parameter schema drives argparse, config.yaml validation and the example file simultaneously, eliminating default-value drift (see the [configuration reference](configuration_EN.md)).
