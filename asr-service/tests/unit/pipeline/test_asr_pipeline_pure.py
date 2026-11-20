"""app/pipeline/asr_pipeline.py 纯方法测试（不触模型/音频文件）。

行为依源码确认（asr_pipeline.py:276/363/383/403）。
"""
import types

import pytest

from app.pipeline.asr_pipeline import ASRPipeline


@pytest.fixture
def pipe():
    # 引擎参数仅被构造函数存储，纯方法测试用占位对象即可
    dummy = types.SimpleNamespace()
    return ASRPipeline(asr_engine=dummy, vad_engine=dummy, punc_engine=None)


# ─── _merge_vad_segments ───

def test_merge_empty(pipe):
    assert pipe._merge_vad_segments([]) == []


def test_merge_single(pipe):
    assert pipe._merge_vad_segments([(0, 1000)]) == [(0, 1000)]


def test_merge_greedy_within_threshold(pipe, monkeypatch):
    monkeypatch.setattr("app.config.MAX_SEGMENT_DURATION", 5)  # 5000ms
    segs = [(0, 1000), (1500, 2000), (6000, 7000)]
    # (0,1000)+ (1500,2000): 2000-0<=5000 合并 -> (0,2000)
    # +(6000,7000): 7000-0>5000 切组
    assert pipe._merge_vad_segments(segs) == [(0, 2000), (6000, 7000)]


def test_merge_boundary_equal_merges(pipe, monkeypatch):
    monkeypatch.setattr("app.config.MAX_SEGMENT_DURATION", 5)
    # 跨度恰好 == 阈值 5000 -> 合并（<=）
    assert pipe._merge_vad_segments([(0, 2000), (5000, 5000)]) == [(0, 5000)]


def test_merge_boundary_over_splits(pipe, monkeypatch):
    monkeypatch.setattr("app.config.MAX_SEGMENT_DURATION", 5)
    # 跨度 6000 > 5000 -> 不合并
    assert pipe._merge_vad_segments([(0, 2000), (5001, 6000)]) == [(0, 2000), (5001, 6000)]
