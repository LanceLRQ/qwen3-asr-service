"""app/pipeline/asr_pipeline.py::_split_segments_to_chunks 测试（需真实 16k wav）。

行为依源码确认（asr_pipeline.py:303）：先 _merge_vad_segments 合并，
再按 MAX_SEGMENT_DURATION 二次切分超长段。
"""
import os
import types

import numpy as np
import pytest

from app.pipeline.asr_pipeline import ASRPipeline


@pytest.fixture
def pipe():
    dummy = types.SimpleNamespace()
    return ASRPipeline(asr_engine=dummy, vad_engine=dummy, punc_engine=None)


def test_split_merged_single_chunk(pipe, make_wav, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.MAX_SEGMENT_DURATION", 5)
    wav = make_wav(duration_sec=9.0)
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    # (0,3000)+(3500,4000): 跨度 4000<=5000 合并为 (0,4000)，时长 4s<=5 -> 1 chunk
    segs = [(0, 3000), (3500, 4000)]
    chunks = pipe._split_segments_to_chunks(wav, segs, str(chunk_dir))

    assert len(chunks) == 1
    assert chunks[0]["offset_sec"] == pytest.approx(0.0)
    assert chunks[0]["duration_sec"] == pytest.approx(4.0, abs=0.05)
    assert os.path.exists(chunks[0]["path"])


def test_split_oversize_secondary_split(pipe, make_wav, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.MAX_SEGMENT_DURATION", 5)
    monkeypatch.setattr("app.config.MAX_ASR_CHUNK_DURATION", 5)   # 强制切分阈值=5
    wav = make_wav(duration_sec=9.0)
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    # 单段 (0,8000) 时长 8s > 5s -> 二次切分为 5s + 3s（静音 wav 无停顿 → 回退名义切点）
    segs = [(0, 8000)]
    chunks = pipe._split_segments_to_chunks(wav, segs, str(chunk_dir))

    assert len(chunks) == 2
    assert [round(c["offset_sec"], 1) for c in chunks] == [0.0, 5.0]
    assert chunks[0]["duration_sec"] == pytest.approx(5.0, abs=0.05)
    assert chunks[1]["duration_sec"] == pytest.approx(3.0, abs=0.05)
    for c in chunks:
        assert os.path.exists(c["path"])


def test_long_continuous_segment_not_split_by_default(pipe, make_wav, tmp_path):
    # 单个连续语音段 12s：默认强制切分阈值=20s（MAX_ASR_CHUNK_DURATION）→ 整段不切，
    # 避免把连续语句切在词中（截图 没/面前 重复识别的根因）
    wav = make_wav(duration_sec=13.0)
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    chunks = pipe._split_segments_to_chunks(wav, [(0, 12280)], str(chunk_dir))
    assert len(chunks) == 1
    assert chunks[0]["duration_sec"] == pytest.approx(12.28, abs=0.05)


def test_explicit_max_segment_keeps_force_threshold(pipe, make_wav, tmp_path):
    # 修复 #10：显式传 max_segment（句子输出上限）不得压低 force_max（=MAX_ASR_CHUNK_DURATION=20s）
    # → 12s 连续段仍整段送 ASR，不被词中切分
    wav = make_wav(duration_sec=13.0)
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    chunks = pipe._split_segments_to_chunks(wav, [(0, 12280)], str(chunk_dir), max_segment_sec=5)
    assert len(chunks) == 1
    assert chunks[0]["duration_sec"] == pytest.approx(12.28, abs=0.05)


def test_force_split_marks_split_after(pipe, make_wav, tmp_path, monkeypatch):
    # 修复 #1：force-split 产生的非末子块带 split_after 标记（供边界去重精准定位）
    monkeypatch.setattr("app.config.MAX_ASR_CHUNK_DURATION", 5)
    wav = make_wav(duration_sec=9.0)
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()

    chunks = pipe._split_segments_to_chunks(wav, [(0, 8000)], str(chunk_dir))
    assert len(chunks) == 2
    assert chunks[0].get("split_after") is True          # 非末子块=人为切点
    assert chunks[1].get("split_after") is False          # 末子块


def test_quiet_cut_falls_back_on_flat_audio(pipe):
    # 静音/平坦区域无能量低谷 → 回退到名义切点（确定性）
    flat = np.zeros(16000 * 10, dtype="float32")
    assert pipe._find_quiet_cut(flat, 16000, target=5 * 16000, window=int(2.5 * 16000)) == 5 * 16000


def test_quiet_cut_snaps_to_silence_dip(pipe):
    # 在名义切点附近人为制造一段静音低谷 → 切点落到低谷中点附近
    sr = 16000
    data = (np.ones(sr * 10, dtype="float32") * 0.3)
    dip_lo, dip_hi = int(5.4 * sr), int(5.6 * sr)
    data[dip_lo:dip_hi] = 0.0                         # 5.4–5.6s 静音停顿
    cut = pipe._find_quiet_cut(data, sr, target=int(5.0 * sr), window=int(1.0 * sr))
    assert dip_lo <= cut <= dip_hi
