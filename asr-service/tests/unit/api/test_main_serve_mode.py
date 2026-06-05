"""app/main.py create_app 的 serve-mode 装配测试。

仅测 vllm 占位分支（不加载任何模型）：验证 T03 验收——vllm 模式仅挂共性接口、
mode 正确、不误挂离线接口。standard 分支需加载真实模型，留待 T10 集成测试。
隔离 setup_logger / cfg 全局副作用。
"""
import logging
import threading
import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


def _args(**over):
    base = dict(
        serve_mode="standard", device="cpu", model_size=None, enable_align=True,
        enable_punc=False, model_source="modelscope", host=None, port=None,
        web=False, max_segment=5, api_key=None, max_queue_size=None,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


@pytest.fixture
def isolated_create_app(tmp_path, monkeypatch):
    """隔离 create_app 的全局副作用：日志目录改到临时路径、root logger 与 cfg 可变项还原。"""
    import app.config as cfg
    from app.utils import logger as logger_mod

    monkeypatch.setattr(logger_mod, "LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setattr(logger_mod, "LOG_FILE", str(tmp_path / "logs" / "asr.log"))

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    keys = ("MODEL_SOURCE", "MAX_SEGMENT_DURATION", "HOST", "PORT", "API_KEY", "MAX_QUEUE_SIZE",
            "SERVE_MODE", "ENABLE_STREAM", "MAX_STREAM_SESSIONS", "STREAM_ASR_CONCURRENCY",
            "CONFIG_FILE")
    snapshot = {k: getattr(cfg, k) for k in keys}

    yield

    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    for k, v in snapshot.items():
        setattr(cfg, k, v)


def test_vllm_mode_mounts_common_only(isolated_create_app):
    from app.main import create_app

    app = create_app(_args(serve_mode="vllm", device="cpu"))
    client = TestClient(app)

    # health 反映 vllm 模式
    health = client.get("/v1/health").json()
    assert health["mode"] == "vllm"
    assert health["capabilities"]["offline_api"] is False
    assert health["capabilities"]["stream"]["backend"] == "vllm-native"

    # capabilities 在 v1/v2 都可用
    assert client.get("/v1/capabilities").json()["mode"] == "vllm"
    assert client.get("/v2/capabilities").json()["mode"] == "vllm"

    # vllm 模式不挂离线接口
    assert client.get("/v1/tasks").status_code == 404
    assert client.get("/v2/tasks").status_code == 404
    assert client.post("/v1/asr", files={"file": ("a.wav", b"x", "audio/wav")}).status_code == 404


# ─── T09: 实时配置与启动参数 ───

def test_standard_mode_with_stream_mounts_ws(isolated_create_app, monkeypatch):
    """standard + --enable-stream：WS /v2/asr/stream 挂载、capabilities 反映、session.created 可达。

    mock 重引擎/设备，避免真实模型加载；强制 GPU 分支走 QwenASREngine（被 mock）。
    """
    import app.main as main

    monkeypatch.setattr(main, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(main, "detect_device",
                        lambda: {"type": "cuda", "vram_gb": 24.0, "name": "FakeGPU"})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cuda")

    class FakeVAD:
        BACKEND = "pytorch"
        def __init__(self, *a, **k):
            self._model = MagicMock()
            self._infer_lock = threading.Lock()    # 对齐 VADEngine 接口（流式共用推理锁）
        def load(self): pass

    class FakeASR:
        def __init__(self, *a, **k): self._model = MagicMock()
        def load(self): pass
        @property
        def align_enabled(self): return True

    class FakePunc:
        BACKEND = "pytorch"
        def __init__(self, *a, **k): self._model = MagicMock()
        def load(self): pass

    class FakeTM:
        def __init__(self, *a, **k): pass
        def set_processor(self, fn): pass
        def start(self): pass
        def shutdown(self): pass

    monkeypatch.setattr(main, "VADEngine", FakeVAD)
    monkeypatch.setattr(main, "QwenASREngine", FakeASR)
    monkeypatch.setattr(main, "PuncEngine", FakePunc)
    monkeypatch.setattr(main, "TaskManager", FakeTM)

    app = main.create_app(_args(serve_mode="standard", device="auto", enable_stream=True))
    client = TestClient(app)

    # capabilities 反映实时已启用
    caps = client.get("/v1/capabilities").json()
    assert caps["mode"] == "standard"
    assert caps["offline_api"] is True
    assert caps["stream"]["enabled"] is True
    assert caps["stream"]["backend"] == "vad-offline"
    assert caps["stream"]["path"] == "/v2/asr/stream"
    assert caps["stream"]["word_timestamps"] is True   # 对齐开启

    # 离线接口仍在
    assert client.get("/v1/health").json()["mode"] == "standard"

    # 实时端点已挂载：连接即收到 session.created
    with client.websocket_connect("/v2/asr/stream") as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["backend"] == "vad-offline"
        assert created["mode"] == "standard"


def test_config_stream_defaults():
    import app.config as cfg
    assert cfg.SERVE_MODE == "standard"
    assert cfg.ENABLE_STREAM is False
    assert cfg.MAX_STREAM_SESSIONS == 16
    assert cfg.STREAM_VAD_CHUNK_MS == 200
    assert cfg.STREAM_ASR_CONCURRENCY == 1   # 模型层推理锁串行化，>1 无收益
    assert cfg.STREAM_MAX_SEGMENT_SEC == 12
    assert cfg.STREAM_MAX_SESSION_SECONDS == 3600
    assert cfg.STREAM_MAX_FRAME_BYTES == 2 * 1024 * 1024
    assert cfg.STREAM_MAX_BACKLOG_BYTES == 8 * 1024 * 1024
    assert cfg.STREAM_SAMPLE_RATE == 16000


def test_parse_args_defaults(monkeypatch):
    from app.main import parse_args
    # --no-config：隔离配置文件自动发现/引导生成，验证纯默认值与重构前一致
    monkeypatch.setattr("sys.argv", ["prog", "--no-config"])
    args = parse_args()
    assert args.serve_mode == "standard"
    assert args.enable_stream is False
    assert args.max_stream_sessions is None
    assert args.stream_asr_concurrency is None


def test_parse_args_stream_flags(monkeypatch):
    from app.main import parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--serve-mode", "standard", "--enable-stream",
        "--max-stream-sessions", "8", "--stream-asr-concurrency", "3",
    ])
    args = parse_args()
    assert args.enable_stream is True
    assert args.max_stream_sessions == 8
    assert args.stream_asr_concurrency == 3


def test_health_echoes_config_file(isolated_create_app, monkeypatch):
    """/health 回显本次生效的配置文件名（防"幽灵配置"，vllm 占位分支即可覆盖）。"""
    import app.config as cfg
    from app.main import create_app

    monkeypatch.setattr(cfg, "CONFIG_FILE", "config.yaml")
    app = create_app(_args(serve_mode="vllm", device="cpu"))
    client = TestClient(app)
    assert client.get("/v1/health").json()["config_file"] == "config.yaml"
    assert client.get("/v2/health").json()["config_file"] == "config.yaml"


def test_apply_cli_config_writes_stream(monkeypatch):
    import app.config as cfg
    from app.main import _apply_cli_config, parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--enable-stream",
        "--max-stream-sessions", "5", "--stream-asr-concurrency", "4",
    ])
    saved = {k: getattr(cfg, k) for k in ("SERVE_MODE", "ENABLE_STREAM", "MAX_STREAM_SESSIONS",
                                          "STREAM_ASR_CONCURRENCY", "MODEL_SOURCE", "MAX_SEGMENT_DURATION")}
    try:
        _apply_cli_config(parse_args())
        assert cfg.ENABLE_STREAM is True
        assert cfg.MAX_STREAM_SESSIONS == 5
        assert cfg.STREAM_ASR_CONCURRENCY == 4
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def test_log_effective_config_masks_api_key(caplog):
    from app.main import _log_effective_config
    args = _args(api_key="sk-secret-123456")
    with caplog.at_level(logging.INFO, logger="app.main"):
        _log_effective_config(args)
    text = caplog.text
    assert "生效配置" in text
    assert "serve_mode" in text and "device" in text
    assert "sk-s****" in text                  # 脱敏后前缀可辨
    assert "sk-secret-123456" not in text      # 明文绝不落日志


def test_log_effective_config_backfills_runtime_defaults(caplog):
    """host/port 未指定时回填 cfg 真实默认值（而非误导性的"未指定"）；
    model_size 未指定标注自动选择；所有 schema 参数都已声明分组（无"其他"组）。"""
    import app.config as cfg
    from app.main import _log_effective_config
    args = _args(host=None, port=None, model_size=None)
    with caplog.at_level(logging.INFO, logger="app.main"):
        _log_effective_config(args)
    text = caplog.text
    assert f"{cfg.HOST} (默认)" in text
    assert f"{cfg.PORT} (默认)" in text
    assert "(自动选择)" in text
    assert "[其他]" not in text                # 新参数必须在 ArgSpec 处声明 group
