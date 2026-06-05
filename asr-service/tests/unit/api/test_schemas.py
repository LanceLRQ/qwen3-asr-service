"""app/api/schemas.py pydantic 模型测试（字段/默认值/校验）。

HealthResponse 已在 T01 改为 mode-aware（仅增字段/放宽可选，向后兼容）；
新增 StreamCapabilities / CapabilitiesResponse。字段定义依 implementation-plan §5.2。
"""
import pytest
from pydantic import ValidationError

from app.api import schemas


def test_asr_response():
    assert schemas.ASRResponse(task_id="t1").task_id == "t1"


def test_task_status_defaults():
    r = schemas.TaskStatusResponse(task_id="t", status="pending", progress=0.0)
    assert r.result is None
    assert r.error is None


def test_task_status_progress_int_coerced_to_float():
    r = schemas.TaskStatusResponse(task_id="t", status="completed", progress=1)
    assert r.progress == 1.0
    assert isinstance(r.progress, float)


def test_task_status_missing_required_raises():
    with pytest.raises(ValidationError):
        schemas.TaskStatusResponse(task_id="t", status="pending")  # 缺 progress


def test_task_list_item_defaults():
    item = schemas.TaskListItem(
        task_id="t", status="completed", progress=1.0, created_at="2026-06-03T10:00:00",
    )
    assert item.language is None
    assert item.finished_at is None
    assert item.error is None


def test_task_list_response_coerces_dicts():
    resp = schemas.TaskListResponse(
        total=1,
        tasks=[{"task_id": "t", "status": "pending", "progress": 0.0, "created_at": "2026-06-03T10:00:00"}],
    )
    assert resp.total == 1
    assert isinstance(resp.tasks[0], schemas.TaskListItem)
    assert resp.tasks[0].task_id == "t"


def test_cancel_response():
    r = schemas.CancelResponse(task_id="t", status="cancelled", message="任务已取消")
    assert r.status == "cancelled"


def test_health_response_full():
    r = schemas.HealthResponse(
        status="ready",
        device="cpu",
        model_size="0.6b",
        align_enabled=False,
        punc_enabled=True,
        asr_backend="qwen_asr",
        vad_backend="pytorch",
        punc_backend="pytorch",
    )
    assert r.status == "ready"
    assert r.device == "cpu"


def test_health_response_missing_required_raises():
    # status 有值但缺 device（device 仍为必填，无默认）
    with pytest.raises(ValidationError):
        schemas.HealthResponse(status="ready")


# ─── T01: mode-aware HealthResponse + Capabilities ───

def test_stream_capabilities_defaults():
    sc = schemas.StreamCapabilities()
    assert sc.enabled is False
    assert sc.backend is None
    assert sc.path is None
    assert sc.partial_results is False
    assert sc.word_timestamps is False


def test_capabilities_response():
    cap = schemas.CapabilitiesResponse(
        mode="standard",
        offline_api=True,
        stream=schemas.StreamCapabilities(enabled=True, backend="vad-offline", path="/v2/asr/stream"),
    )
    assert cap.mode == "standard"
    assert cap.offline_api is True
    assert cap.stream.backend == "vad-offline"


def test_health_response_new_field_defaults():
    # 仅给原必填字段，mode/capabilities 走默认
    r = schemas.HealthResponse(status="ready", device="cpu")
    assert r.mode == "standard"
    assert r.capabilities is None
    # 放宽为可选的字段默认值
    assert r.model_size is None
    assert r.align_enabled is False
    assert r.asr_backend is None


def test_health_response_backward_compatible_dump():
    # 旧的 8 字段输入仍可构造，且原字段值原样保留，新增字段以默认值出现
    legacy = {
        "status": "ready",
        "device": "cpu",
        "model_size": "0.6b",
        "align_enabled": False,
        "punc_enabled": True,
        "asr_backend": "qwen_asr",
        "vad_backend": "pytorch",
        "punc_backend": "pytorch",
    }
    dumped = schemas.HealthResponse(**legacy).model_dump()
    for k, v in legacy.items():
        assert dumped[k] == v          # 原字段值不变
    assert dumped["mode"] == "standard"
    assert dumped["capabilities"] is None


def test_health_response_with_capabilities():
    r = schemas.HealthResponse(
        status="ready",
        mode="standard",
        device="cpu",
        capabilities=schemas.CapabilitiesResponse(
            mode="standard",
            offline_api=True,
            stream=schemas.StreamCapabilities(enabled=True, backend="vad-offline", path="/v2/asr/stream"),
        ),
    )
    assert r.capabilities.stream.path == "/v2/asr/stream"


# ─── S 系列：说话人分离字段（全部可选，向后兼容）───

def test_stream_capabilities_speaker_labels_default():
    assert schemas.StreamCapabilities().speaker_labels is False


def test_capabilities_response_speaker_labels_default():
    cap = schemas.CapabilitiesResponse(
        mode="standard", offline_api=True, stream=schemas.StreamCapabilities(),
    )
    assert cap.speaker_labels is False


def test_health_response_speaker_enabled_default():
    r = schemas.HealthResponse(status="ready", device="cpu")
    assert r.speaker_enabled is False


def test_final_msg_speaker_field():
    from app.api.ws_schemas import FinalMsg
    m = FinalMsg(seg_id=0, text="你好")
    assert m.speaker is None
    assert "speaker" in FinalMsg(seg_id=0, text="你好", speaker="A").model_dump()
    assert FinalMsg(seg_id=0, text="你好", speaker="A").speaker == "A"
