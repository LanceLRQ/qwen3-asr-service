"""段级远场/环境音门控（方案 1）单测：纯函数 + StreamSession 接线。

纯函数（rms_dbfs / NoiseFloorTracker / should_gate）覆盖核心判定；
StreamSession 用例验证 noise_filter 开关接线与"门控段不触达 ASR"。
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import numpy as np
import pytest

from app.runtime.noise_gate import (
    DBFS_FLOOR, rms_dbfs, NoiseFloorTracker, should_gate,
)
from app.runtime.stream_session import StreamSession


# ─── rms_dbfs ───

def test_rms_dbfs_empty_and_silence_return_floor():
    assert rms_dbfs(np.zeros(0, dtype=np.float32)) == DBFS_FLOOR
    assert rms_dbfs(np.zeros(16000, dtype=np.float32)) == DBFS_FLOOR


@pytest.mark.parametrize("amp, expected", [
    (1.0, 0.0),       # 满量程 → 0 dBFS
    (0.1, -20.0),     # 1/10 → -20 dBFS
    (0.001, -60.0),   # 1/1000 → -60 dBFS
])
def test_rms_dbfs_known_amplitude(amp, expected):
    seg = np.full(8000, amp, dtype=np.float32)
    assert rms_dbfs(seg) == pytest.approx(expected, abs=0.1)


# ─── NoiseFloorTracker ───

def test_noise_floor_uninitialized_is_none():
    assert NoiseFloorTracker().floor_dbfs is None


def test_noise_floor_converges_toward_input_level():
    tracker = NoiseFloorTracker(alpha=0.5)
    frame = np.full(1600, 0.01, dtype=np.float32)   # ≈ -40 dBFS
    for _ in range(50):
        tracker.update(frame)
    assert tracker.floor_dbfs == pytest.approx(-40.0, abs=0.5)


def test_noise_floor_ignores_pure_silence():
    tracker = NoiseFloorTracker()
    tracker.update(np.zeros(1600, dtype=np.float32))
    assert tracker.floor_dbfs is None               # 纯静音帧不参与估计


# ─── should_gate ───

def test_energy_gate_rejects_quiet_segment():
    gated, reason = should_gate(-60.0, None, energy_floor_dbfs=-50.0, snr_min_db=6.0)
    assert gated and reason == "energy"


def test_passes_when_loud_and_no_noise_floor():
    gated, _ = should_gate(-20.0, None, energy_floor_dbfs=-50.0, snr_min_db=6.0)
    assert not gated


def test_snr_gate_rejects_segment_close_to_noise_floor():
    # 段 -30，底噪 -33 → SNR=3 < 6 → 丢弃
    gated, reason = should_gate(-30.0, -33.0, energy_floor_dbfs=-50.0, snr_min_db=6.0)
    assert gated and reason == "snr"


def test_snr_gate_passes_when_segment_well_above_floor():
    gated, _ = should_gate(-20.0, -45.0, energy_floor_dbfs=-50.0, snr_min_db=6.0)
    assert not gated


def test_snr_gate_disabled_when_threshold_non_positive():
    gated, _ = should_gate(-30.0, -31.0, energy_floor_dbfs=-50.0, snr_min_db=0.0)
    assert not gated                                # snr_min_db<=0 关闭 SNR 门


# ─── StreamSession 接线 ───

def _drain(agen):
    async def _run():
        return [m async for m in agen]
    return asyncio.run(_run())


def _make_session(**kw):
    return StreamSession(
        "sid-test", MagicMock(), MagicMock(), None,
        ThreadPoolExecutor(max_workers=1), asyncio.Semaphore(1), **kw,
    )


def test_tracker_absent_when_filter_off():
    sess = _make_session()
    sess.configure({})
    assert sess._noise_tracker is None


def test_tracker_present_when_filter_on():
    sess = _make_session(noise_filter=True)
    sess.configure({})
    assert sess._noise_tracker is not None


def test_gated_segment_does_not_reach_asr():
    asr = MagicMock()
    asr.transcribe_array.side_effect = AssertionError("门控段不应触达 ASR")
    sess = StreamSession(
        "sid-test", MagicMock(), asr, None,
        ThreadPoolExecutor(max_workers=1), asyncio.Semaphore(1),
        noise_filter=True, energy_floor_dbfs=-50.0, snr_min_db=0.0,
    )
    sess.configure({})
    sess.buffer.append(np.full(8000, 0.001, dtype=np.float32))   # ≈ -60 dBFS < -50
    msgs = _drain(sess._emit_final(0, 500))
    assert msgs == []
    asr.transcribe_array.assert_not_called()


# ─── 客户端 start 消息覆盖 + 服务端范围钳制 ───

def test_configure_override_enables_filter_per_session():
    sess = _make_session()                       # 服务端默认关
    sess.configure({"noise_filter": True})
    assert sess._noise_filter is True and sess._noise_tracker is not None


def test_configure_override_thresholds():
    sess = _make_session(noise_filter=True)
    sess.configure({"energy_floor_dbfs": -40.0, "snr_min_db": 10.0})
    assert sess._energy_floor_dbfs == -40.0
    assert sess._snr_min_db == 10.0


def test_configure_keeps_server_defaults_when_absent():
    sess = _make_session(noise_filter=True, energy_floor_dbfs=-55.0, snr_min_db=7.0)
    sess.configure({})
    assert sess._energy_floor_dbfs == -55.0 and sess._snr_min_db == 7.0


@pytest.mark.parametrize("msg", [
    {"energy_floor_dbfs": 5.0},      # >0 越界
    {"energy_floor_dbfs": -200.0},   # <-90 越界
    {"snr_min_db": 50.0},            # >40 越界
    {"snr_min_db": -1.0},            # <0 越界
    {"noise_filter": "yes"},         # 非布尔
])
def test_configure_rejects_invalid_override(msg):
    sess = _make_session()
    with pytest.raises(ValueError):
        sess.configure(msg)


def test_capabilities_advertises_noise_filter_tunable():
    from app.runtime.stream_session import VadOfflineBackend
    backend = VadOfflineBackend(MagicMock(), MagicMock(), None)
    assert backend.capabilities["noise_filter_tunable"] is True
