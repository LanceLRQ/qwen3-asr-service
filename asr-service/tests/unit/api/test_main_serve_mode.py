"""app/main.py create_app 的 serve-mode 装配测试。

仅测 vllm 占位分支（不加载任何模型）：验证 T03 验收——vllm 模式仅挂共性接口、
mode 正确、不误挂离线接口。standard 分支需加载真实模型，留待 T10 集成测试。
隔离 setup_logger / cfg 全局副作用。
"""
import logging
import sys
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
            "STREAM_SAVE_AUDIO", "STREAM_RECORDING_RETENTION_HOURS",
            "CONFIG_FILE", "ENABLE_SPEAKER", "SPEAKER_THRESHOLD", "SPEAKER_MAX",
            "SPEAKER_MIN_SEG_MS", "SPEAKER_MAX_WINDOWS",
            "ENABLE_SPEAKER_DB", "SPEAKER_DB_PATH", "SPEAKER_ID_THRESHOLD", "SPEAKER_ID_MARGIN",
            "SPEAKER_ENROLL_MIN_SEC", "SPEAKER_AUTO_ENROLL", "SPEAKER_AUTO_ENROLL_MIN_SEC",
            "SPEAKER_STORE_AUDIO")
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


def _mock_vllm_engine(monkeypatch):
    """mock vllm 引擎/设备/离线任务层，避免真实 GPU / vLLM 加载 / worker 线程。"""
    import app.main as main
    monkeypatch.setattr(main, "detect_device",
                        lambda: {"type": "cuda", "vram_gb": 24.0, "name": "FakeGPU"})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cuda")

    class FakeVLLMEngine:
        def __init__(self, *a, **k): pass
        def load(self): pass
        @property
        def align_enabled(self): return True       # Phase 1：离线 transcribe 出词级时间戳

    monkeypatch.setattr("app.engines.vllm_asr_engine.VLLMASREngine", FakeVLLMEngine)

    # 离线任务层在 _assemble_vllm 内惰性导入（venv-vllm 顶层为 None）；patch 真实路径
    # 拦截，避免真实 TaskManager worker 线程
    class FakeTM:
        def __init__(self, *a, **k): pass
        def set_processor(self, fn): pass
        def start(self): pass
        def shutdown(self): pass
        def list_tasks(self, status=None): return []
        def get_task(self, task_id): return None

    monkeypatch.setattr("app.runtime.task_manager.TaskManager", FakeTM)


def test_vllm_mode_mounts_stream_and_common(isolated_create_app, monkeypatch):
    import app.main as main
    _mock_vllm_engine(monkeypatch)

    app = main.create_app(_args(serve_mode="vllm", device="auto"))
    client = TestClient(app)

    # health 反映 vllm 模式 + 流式已启用（路线 A，含 partial）+ 离线已挂（Phase 1）
    health = client.get("/v1/health").json()
    assert health["mode"] == "vllm"
    assert health["capabilities"]["offline_api"] is True       # Phase 1：离线已接入
    assert health["align_enabled"] is True                     # 对齐器（词级时间戳）
    assert health["punc_enabled"] is True                      # 模型原生标点
    stream = health["capabilities"]["stream"]
    assert stream["backend"] == "vllm-native"
    assert stream["enabled"] is True
    assert stream["partial_results"] is True
    assert stream["word_timestamps"] is False
    assert stream["path"] == "/v2/asr/stream"
    assert stream["save_audio"] is False
    assert stream["recording_retention_hours"] == 72
    assert stream["recording_download_path"] is None

    # capabilities 在 v1/v2 都可用
    assert client.get("/v1/capabilities").json()["mode"] == "vllm"
    assert client.get("/v2/capabilities").json()["mode"] == "vllm"

    # vllm 模式现挂离线接口（与 standard 同一套契约）：/tasks 可达（非 404）
    assert client.get("/v1/tasks").status_code == 200
    assert client.get("/v2/tasks").status_code == 200

    # 实时端点已挂载：连接即收到 session.created（vllm-native，含 partial）
    with client.websocket_connect("/v2/asr/stream") as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["mode"] == "vllm"
        assert created["backend"] == "vllm-native"
        assert created["capabilities"]["partial_results"] is True


def test_vllm_mode_requires_cuda(isolated_create_app, monkeypatch):
    """vllm 模式非 CUDA 设备明确退出（不静默降级到 CPU）。"""
    import app.main as main
    monkeypatch.setattr(main, "detect_device", lambda: {"type": "cpu", "vram_gb": None, "name": "cpu"})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cpu")
    with pytest.raises(SystemExit):
        main.create_app(_args(serve_mode="vllm", device="cpu"))


def test_vllm_mode_web_mounts_demo(isolated_create_app, monkeypatch):
    """vllm + --web：挂载 Web UI，实时演示页（内置 partial 渲染）可达。"""
    import app.config as cfg
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    saved_web = cfg.ENABLE_WEB
    try:
        app = main.create_app(_args(serve_mode="vllm", device="auto", web=True))
        client = TestClient(app)
        assert client.get("/web-ui/stream").status_code == 200
        assert cfg.ENABLE_WEB is True
    finally:
        cfg.ENABLE_WEB = saved_web


# ─── T09: 实时配置与启动参数 ───

def test_standard_mode_with_stream_mounts_ws(isolated_create_app, monkeypatch):
    """standard + --enable-stream：WS /v2/asr/stream 挂载、capabilities 反映、session.created 可达。

    mock 重引擎/设备，避免真实模型加载；强制 GPU 分支走 QwenASREngine（被 mock）。
    """
    import app.main as main

    _mock_standard_engines(monkeypatch, align_enabled=True)

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
    assert caps["stream"]["save_audio"] is False
    assert caps["stream"]["recording_retention_hours"] == 72
    assert caps["stream"]["recording_download_path"] is None

    # 离线接口仍在
    assert client.get("/v1/health").json()["mode"] == "standard"

    # 实时端点已挂载：连接即收到 session.created
    with client.websocket_connect("/v2/asr/stream") as ws:
        created = ws.receive_json()
        assert created["type"] == "session.created"
        assert created["mode"] == "standard"
        assert created["backend"] == "vad-offline"


def test_standard_mode_stream_save_audio_capability(isolated_create_app, monkeypatch):
    """开启实时录音保存时，capabilities 返回下载/删除路径模板。"""
    import app.main as main

    _mock_standard_engines(monkeypatch)

    app = main.create_app(_args(
        device="auto",
        enable_stream=True,
        stream_save_audio=True,
        stream_recording_retention_hours=48,
    ))
    client = TestClient(app)

    stream = client.get("/v2/capabilities").json()["stream"]
    assert stream["save_audio"] is True
    assert stream["recording_retention_hours"] == 48
    assert stream["recording_download_path"] == "/v2/stream-recordings/{recording_id}"


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
    assert cfg.STREAM_SAVE_AUDIO is False
    assert cfg.STREAM_RECORDING_RETENTION_HOURS == 72


def test_parse_args_defaults(monkeypatch):
    from app.main import parse_args
    # --no-config：隔离配置文件自动发现/引导生成，验证纯默认值与重构前一致
    monkeypatch.setattr("sys.argv", ["prog", "--no-config"])
    args = parse_args()
    assert args.serve_mode == "standard"
    assert args.enable_stream is False
    assert args.max_stream_sessions is None
    assert args.stream_asr_concurrency is None
    assert args.stream_save_audio is False
    assert args.stream_recording_retention_hours == 72


def test_parse_args_stream_flags(monkeypatch):
    from app.main import parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--serve-mode", "standard", "--enable-stream",
        "--max-stream-sessions", "8", "--stream-asr-concurrency", "3",
        "--stream-save-audio", "--stream-recording-retention-hours", "48",
    ])
    args = parse_args()
    assert args.enable_stream is True
    assert args.max_stream_sessions == 8
    assert args.stream_asr_concurrency == 3
    assert args.stream_save_audio is True
    assert args.stream_recording_retention_hours == 48


def test_health_echoes_config_file(isolated_create_app, monkeypatch):
    """/health 回显本次生效的配置文件名（防"幽灵配置"，vllm 分支即可覆盖）。"""
    import app.config as cfg
    import app.main as main

    _mock_vllm_engine(monkeypatch)
    monkeypatch.setattr(cfg, "CONFIG_FILE", "config.yaml")
    app = main.create_app(_args(serve_mode="vllm", device="auto"))
    client = TestClient(app)
    assert client.get("/v1/health").json()["config_file"] == "config.yaml"
    assert client.get("/v2/health").json()["config_file"] == "config.yaml"


def test_config_vllm_defaults():
    import app.config as cfg
    assert cfg.VLLM_GPU_MEMORY_UTILIZATION == 0.6   # 单流 ASR 调优（原 0.8 过大）
    assert cfg.VLLM_MAX_MODEL_LEN == 32768          # 压低 KV 下限，使低占用率可启动
    assert cfg.VLLM_CHUNK_SIZE_SEC == 1.0       # V0 实测定档（细腻 partial）
    assert cfg.VLLM_CONCURRENCY == 1            # generate 串行，>1 无吞吐收益
    assert cfg.VLLM_MAX_UTTERANCE_SEC == 20
    assert cfg.VLLM_ENERGY_FLOOR_DBFS == -45.0
    assert cfg.VLLM_END_SILENCE_MS == 800
    assert cfg.VLLM_ENABLE_ALIGN is True            # 离线词级时间戳默认开
    assert cfg.VLLM_ALIGN_DEVICE == "cuda"          # 对齐器默认 GPU（长音频 OOM 时可改 cpu）
    assert cfg.VLLM_INFER_BATCH_SIZE == 4           # 对齐/ASR 批大小有界（非 -1，防长音频对齐 OOM）
    assert cfg.VLLM_OFFLINE_CHUNK_SEC == 180        # 离线逐块转写切块时长（长音频进度/显存/取消粒度）
    assert cfg.VLLM_SEGMENT_GAP_MS == 500           # 离线分段词间隙阈值


def test_parse_and_apply_vllm_args(monkeypatch):
    import app.config as cfg
    from app.main import _apply_cli_config, parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--serve-mode", "vllm",
        "--gpu-memory-utilization", "0.7", "--vllm-chunk-size-sec", "1.5",
        "--vllm-max-utterance-sec", "30", "--vllm-concurrency", "2",
        "--vllm-end-silence-ms", "600",
    ])
    saved = {k: getattr(cfg, k) for k in (
        "VLLM_GPU_MEMORY_UTILIZATION", "VLLM_CHUNK_SIZE_SEC", "VLLM_MAX_UTTERANCE_SEC",
        "VLLM_CONCURRENCY", "VLLM_END_SILENCE_MS", "MODEL_SOURCE", "MAX_SEGMENT_DURATION",
        "SERVE_MODE", "ENABLE_STREAM")}
    try:
        ns = parse_args()
        assert ns.serve_mode == "vllm"
        _apply_cli_config(ns)
        assert cfg.VLLM_GPU_MEMORY_UTILIZATION == 0.7
        assert cfg.VLLM_CHUNK_SIZE_SEC == 1.5
        assert cfg.VLLM_MAX_UTTERANCE_SEC == 30
        assert cfg.VLLM_CONCURRENCY == 2
        assert cfg.VLLM_END_SILENCE_MS == 600
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def test_parse_and_apply_vllm_align_device(monkeypatch):
    """--vllm-align-device cpu 解析并写入 cfg（OOM 逃生：对齐器移出 GPU）。"""
    import app.config as cfg
    from app.main import _apply_cli_config, parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--serve-mode", "vllm", "--vllm-align-device", "cpu"])
    saved = {k: getattr(cfg, k) for k in (
        "VLLM_ALIGN_DEVICE", "MODEL_SOURCE", "MAX_SEGMENT_DURATION",
        "SERVE_MODE", "ENABLE_STREAM")}
    try:
        ns = parse_args()
        assert ns.vllm_align_device == "cpu"
        _apply_cli_config(ns)
        assert cfg.VLLM_ALIGN_DEVICE == "cpu"
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


def test_apply_cli_config_writes_stream(monkeypatch):
    import app.config as cfg
    from app.main import _apply_cli_config, parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--enable-stream",
        "--max-stream-sessions", "5", "--stream-asr-concurrency", "4",
        "--stream-save-audio", "--stream-recording-retention-hours", "24",
    ])
    saved = {k: getattr(cfg, k) for k in ("SERVE_MODE", "ENABLE_STREAM", "MAX_STREAM_SESSIONS",
                                          "STREAM_ASR_CONCURRENCY", "STREAM_SAVE_AUDIO",
                                          "STREAM_RECORDING_RETENTION_HOURS",
                                          "MODEL_SOURCE", "MAX_SEGMENT_DURATION")}
    try:
        _apply_cli_config(parse_args())
        assert cfg.ENABLE_STREAM is True
        assert cfg.MAX_STREAM_SESSIONS == 5
        assert cfg.STREAM_ASR_CONCURRENCY == 4
        assert cfg.STREAM_SAVE_AUDIO is True
        assert cfg.STREAM_RECORDING_RETENTION_HOURS == 24
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


# ─── S 系列：说话人分离装配与降级 ───

def _mock_standard_engines(monkeypatch, *, align_enabled=False):
    """standard 分支的重引擎全套 mock（体例同 test_standard_mode_with_stream_mounts_ws）。"""
    import app.main as main
    from fastapi import APIRouter
    import app.config as cfg

    monkeypatch.setattr(main, "_STANDARD_DEPS_OK", True)
    monkeypatch.setattr(main, "_STANDARD_DEPS_ERR", None, raising=False)
    monkeypatch.setattr(main, "check_ffmpeg", lambda: None)
    monkeypatch.setattr(main, "detect_device",
                        lambda: {"type": "cuda", "vram_gb": 24.0, "name": "FakeGPU"})
    monkeypatch.setattr(main, "resolve_device", lambda req, device_info=None: "cuda")

    class FakeVAD:
        BACKEND = "pytorch"
        def __init__(self, *a, **k):
            self._model = MagicMock()
            self._infer_lock = threading.Lock()
        def load(self): pass

    class FakeASR:
        def __init__(self, *a, **k): self._model = MagicMock()
        def load(self): pass
        @property
        def align_enabled(self): return align_enabled

    class FakePunc:
        BACKEND = "pytorch"
        def __init__(self, *a, **k): self._model = MagicMock()
        def load(self): pass

    class FakePipeline:
        def __init__(self, *a, **k): pass
        def run(self, *a, **k): return {}

    class FakeTM:
        def __init__(self, *a, **k): pass
        def set_processor(self, fn): pass
        def start(self): pass
        def shutdown(self): pass

    class FakeStreamSession:
        audio_fs = cfg.STREAM_SAMPLE_RATE

        def configure(self, msg):
            self.audio_fs = msg.get("audio_fs", cfg.STREAM_SAMPLE_RATE)
            return []

        async def feed_audio(self, data):
            if False:
                yield data

        async def flush(self):
            if False:
                yield {}

    class FakeVadOfflineBackend:
        mode = "standard"
        backend = "vad-offline"

        def __init__(self, *a, **k):
            self.capabilities = {
                "partial_results": False,
                "word_timestamps": align_enabled,
                "speaker_labels": bool(k.get("speaker")),
            }

        async def acquire(self):
            return True

        def create_session(self, sid):
            return FakeStreamSession()

        def release(self, session):
            pass

        def shutdown(self):
            pass

    fake_stream_session = types.ModuleType("app.runtime.stream_session")
    fake_stream_session.VadOfflineBackend = FakeVadOfflineBackend

    monkeypatch.setattr(main, "VADEngine", FakeVAD)
    monkeypatch.setattr(main, "QwenASREngine", FakeASR)
    monkeypatch.setattr(main, "PuncEngine", FakePunc)
    monkeypatch.setattr(main, "ASRPipeline", FakePipeline)
    monkeypatch.setattr(main, "TaskManager", FakeTM)
    monkeypatch.setattr(main, "init_routes", lambda *a, **k: None)
    monkeypatch.setattr(main, "build_offline_router", lambda prefix, **k: APIRouter(prefix=prefix))
    monkeypatch.setitem(sys.modules, "app.runtime.stream_session", fake_stream_session)


def test_standard_mode_speaker_enabled(isolated_create_app, monkeypatch):
    """--enable-speaker 装配成功：capabilities/health 置位（离线+实时同一开关）。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)

    class FakeSpeaker:
        def __init__(self, *a, **k): pass
        def load(self): pass

    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", FakeSpeaker)

    app = main.create_app(_args(device="auto", enable_speaker=True, enable_stream=True))
    client = TestClient(app)

    caps = client.get("/v2/capabilities").json()
    assert caps["speaker_labels"] is True
    assert caps["stream"]["speaker_labels"] is True
    assert client.get("/v2/health").json()["speaker_enabled"] is True

    with client.websocket_connect("/v2/asr/stream") as ws:
        created = ws.receive_json()
        assert created["capabilities"]["speaker_labels"] is True


def test_standard_mode_speaker_load_failure_degrades(isolated_create_app, monkeypatch):
    """说话人引擎加载失败：降级关闭、不影响服务启动（容错对齐标点）。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)

    class BoomSpeaker:
        def __init__(self, *a, **k): pass
        def load(self): raise RuntimeError("weights missing")

    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", BoomSpeaker)

    app = main.create_app(_args(device="auto", enable_speaker=True))
    client = TestClient(app)

    health = client.get("/v2/health").json()
    assert health["status"] == "ready"               # 服务正常
    assert health["speaker_enabled"] is False        # 已降级
    assert client.get("/v2/capabilities").json()["speaker_labels"] is False


def test_standard_mode_speaker_disabled_by_default(isolated_create_app, monkeypatch):
    """未开启：字段为 false（关闭态零变化）。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)

    app = main.create_app(_args(device="auto"))
    client = TestClient(app)
    assert client.get("/v2/health").json()["speaker_enabled"] is False
    assert client.get("/v2/capabilities").json()["speaker_labels"] is False


def test_config_speaker_defaults():
    import app.config as cfg
    assert cfg.ENABLE_SPEAKER is False
    assert cfg.SPEAKER_THRESHOLD == 0.5      # S0 spike 定稿（区间 [0.35, 0.65]）
    assert cfg.SPEAKER_MAX == 8
    assert cfg.SPEAKER_MIN_SEG_MS == 1500    # S0 spike 定稿（原方案 800 作废）
    assert cfg.SPEAKER_MAX_WINDOWS == 4000


def test_parse_args_speaker_flags(monkeypatch):
    from app.main import parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--enable-speaker",
        "--speaker-threshold", "0.45", "--speaker-max", "4",
        "--speaker-min-seg-ms", "1000", "--speaker-max-windows", "2000",
    ])
    args = parse_args()
    assert args.enable_speaker is True
    assert args.speaker_threshold == 0.45
    assert args.speaker_max == 4
    assert args.speaker_min_seg_ms == 1000
    assert args.speaker_max_windows == 2000


def test_apply_cli_config_writes_speaker(monkeypatch):
    import app.config as cfg
    from app.main import _apply_cli_config, parse_args
    monkeypatch.setattr("sys.argv", [
        "prog", "--no-config", "--enable-speaker", "--speaker-threshold", "0.6",
    ])
    saved = {k: getattr(cfg, k) for k in (
        "ENABLE_SPEAKER", "SPEAKER_THRESHOLD", "SPEAKER_MAX", "SPEAKER_MIN_SEG_MS",
        "SPEAKER_MAX_WINDOWS", "MODEL_SOURCE", "MAX_SEGMENT_DURATION")}
    try:
        _apply_cli_config(parse_args())
        assert cfg.ENABLE_SPEAKER is True
        assert cfg.SPEAKER_THRESHOLD == 0.6
        assert cfg.SPEAKER_MIN_SEG_MS == 1500   # 未传 → schema 默认
    finally:
        for k, v in saved.items():
            setattr(cfg, k, v)


# ─── V 系列：声纹库降级矩阵四分支 ───

class _OkSpeaker:
    MODEL_TAG = "campplus_cn_common@v1"
    def __init__(self, *a, **k): pass
    def load(self): pass


def test_speaker_db_degrades_without_speaker_engine(isolated_create_app, monkeypatch, tmp_path):
    """分支①：未开 enable_speaker → 声纹库降级关闭，服务正常。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)

    app = main.create_app(_args(device="auto", enable_speaker_db=True, api_key="k",
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    health = client.get("/v2/health", headers={"Authorization": "Bearer k"}).json()
    assert health["status"] == "ready"
    assert health["speaker_db_enabled"] is False
    r = client.get("/v2/speakers", headers={"Authorization": "Bearer k"})
    assert r.status_code == 503 and r.json()["detail"] == "speaker_db_disabled"


def test_speaker_db_degrades_without_api_key(isolated_create_app, monkeypatch, tmp_path):
    """分支②：API_KEY 为空 → 合规硬规则降级关闭。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", _OkSpeaker)

    app = main.create_app(_args(device="auto", enable_speaker=True,
                                enable_speaker_db=True,
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    health = client.get("/v2/health").json()
    assert health["speaker_enabled"] is True            # 分离正常
    assert health["speaker_db_enabled"] is False        # 声纹库降级
    assert client.get("/v2/speakers").status_code == 503


def test_speaker_db_enabled_full_path(isolated_create_app, monkeypatch, tmp_path):
    """分支④（正常）：引擎+API_KEY+建库全通 → 端点可用、capabilities 置位。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", _OkSpeaker)

    app = main.create_app(_args(device="auto", enable_speaker=True, api_key="k",
                                enable_speaker_db=True,
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    auth = {"Authorization": "Bearer k"}
    health = client.get("/v2/health", headers=auth).json()
    assert health["speaker_db_enabled"] is True
    caps = client.get("/v2/capabilities", headers=auth).json()
    assert caps["speaker_identification"] is True
    body = client.get("/v2/speakers", headers=auth).json()
    assert body == {"total": 0, "speakers": []}          # 空库可列


def test_speaker_db_degrades_on_store_failure(isolated_create_app, monkeypatch, tmp_path):
    """分支③：建库失败 → 降级关闭，服务正常启动。"""
    import app.main as main
    _mock_standard_engines(monkeypatch)
    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", _OkSpeaker)

    class BoomStore:
        def __init__(self, *a, **k):
            raise RuntimeError("disk full")

    monkeypatch.setattr("app.runtime.speaker_store.SpeakerStore", BoomStore)

    app = main.create_app(_args(device="auto", enable_speaker=True, api_key="k",
                                enable_speaker_db=True,
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    auth = {"Authorization": "Bearer k"}
    health = client.get("/v2/health", headers=auth).json()
    assert health["status"] == "ready"
    assert health["speaker_db_enabled"] is False
    assert client.get("/v2/speakers", headers=auth).status_code == 503


# ─── vLLM 模式 Phase 2：说话人分离 / 声纹库装配 ───

def test_vllm_mode_speaker_enabled(isolated_create_app, monkeypatch):
    """vllm + --enable-speaker：离线说话人分离装配，capabilities/health 置位；流式说话人仍无。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)

    class FakeSpeaker:
        def __init__(self, *a, **k): pass
        def load(self): pass

    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", FakeSpeaker)

    app = main.create_app(_args(serve_mode="vllm", device="auto", enable_speaker=True))
    client = TestClient(app)

    caps = client.get("/v2/capabilities").json()
    assert caps["speaker_labels"] is True              # 离线分离
    assert caps["stream"]["speaker_labels"] is False   # 流式说话人仍无（仅离线）
    assert client.get("/v2/health").json()["speaker_enabled"] is True


def test_vllm_mode_speaker_load_failure_degrades(isolated_create_app, monkeypatch):
    """vllm 说话人引擎加载失败：降级关闭、不影响服务（容错对齐 standard）。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)

    class BoomSpeaker:
        def __init__(self, *a, **k): pass
        def load(self): raise RuntimeError("weights missing")

    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", BoomSpeaker)

    app = main.create_app(_args(serve_mode="vllm", device="auto", enable_speaker=True))
    client = TestClient(app)
    health = client.get("/v2/health").json()
    assert health["status"] == "ready"
    assert health["speaker_enabled"] is False
    assert client.get("/v2/capabilities").json()["speaker_labels"] is False


def test_vllm_mode_speaker_db_full_path(isolated_create_app, monkeypatch, tmp_path):
    """vllm 声纹库全通：speaker_identification 置位、/v2/speakers 可列（真实 SpeakerStore）。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", _OkSpeaker)

    app = main.create_app(_args(serve_mode="vllm", device="auto", enable_speaker=True,
                                api_key="k", enable_speaker_db=True,
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    auth = {"Authorization": "Bearer k"}
    assert client.get("/v2/health", headers=auth).json()["speaker_db_enabled"] is True
    assert client.get("/v2/capabilities", headers=auth).json()["speaker_identification"] is True
    assert client.get("/v2/speakers", headers=auth).json() == {"total": 0, "speakers": []}


def test_vllm_mode_speaker_db_degrades_without_api_key(isolated_create_app, monkeypatch, tmp_path):
    """vllm 声纹库合规硬约束：无 api_key → 降级关闭、端点 503（生物识别须鉴权）。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    monkeypatch.setattr(
        "app.engines.speaker_embedding_engine.SpeakerEmbeddingEngine", _OkSpeaker)

    app = main.create_app(_args(serve_mode="vllm", device="auto", enable_speaker=True,
                                enable_speaker_db=True,
                                speaker_db_path=str(tmp_path / "spk.db")))
    client = TestClient(app)
    assert client.get("/v2/health").json()["speaker_db_enabled"] is False
    assert client.get("/v2/speakers").status_code == 503


# ─── vLLM 模式 Phase 3：兼容接口装配 ───

def test_vllm_mode_compat_openai_mounted(isolated_create_app, monkeypatch):
    """vllm + --enable-openai-api：/compat/openai/v1/models 可达；capabilities.compat 置位。
    实时随离线开关挂（vLLM 流式恒开，无需 --enable-stream），R1 finals-only。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    app = main.create_app(_args(serve_mode="vllm", device="auto",
                                api_key="k", enable_openai_api=True))
    client = TestClient(app)

    compat = client.get("/v2/capabilities").json()["compat"]
    assert compat["openai"] is True
    assert compat["dashscope"] is False
    assert compat["realtime"] is True             # vLLM 流式恒开 → 实时兼容随挂
    assert compat["realtime_partial"] is True     # R2 增量已下发（OpenAI delta best-effort）

    r = client.get("/compat/openai/v1/models", headers={"Authorization": "Bearer k"})
    assert r.status_code == 200
    assert r.json()["data"][0]["id"].startswith("qwen3-asr-")


def test_vllm_mode_compat_dashscope_mounted(isolated_create_app, monkeypatch):
    """vllm + --enable-dashscope-api：DashScope 路由挂载、capabilities.compat.dashscope 置位。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    app = main.create_app(_args(serve_mode="vllm", device="auto",
                                api_key="k", enable_dashscope_api=True))
    client = TestClient(app)

    compat = client.get("/v2/capabilities").json()["compat"]
    assert compat["dashscope"] is True and compat["openai"] is False
    assert compat["realtime"] is True
    # 提交端点已挂载（无 body/auth → 401/422，非 404）
    r = client.post("/compat/dashscope/api/v1/services/audio/asr/transcription")
    assert r.status_code != 404


def test_vllm_mode_compat_disabled_by_default(isolated_create_app, monkeypatch):
    """未开启兼容接口：compat 全 false，/compat 路由不挂（404）。"""
    import app.main as main
    _mock_vllm_engine(monkeypatch)
    app = main.create_app(_args(serve_mode="vllm", device="auto"))
    client = TestClient(app)

    assert client.get("/v2/capabilities").json()["compat"] == {
        "openai": False, "dashscope": False, "realtime": False, "realtime_partial": False}
    assert client.get("/compat/openai/v1/models").status_code == 404
