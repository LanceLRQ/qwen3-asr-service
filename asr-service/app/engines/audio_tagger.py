"""通用音频事件标注（Audio Tagging）的引擎无关接口与共用工具。

两个引擎（PANNs / YAMNet）共形于 AudioTaggerEngine：离线与流式都按「滑窗 →
predict_window」统一处理，下游（scene_mapper / 事件段聚合）不感知具体引擎。

本模块仅依赖 numpy（不引 torch/torchlibrosa），故标签加载/排序工具与接口契约
可在不安装重依赖的环境（含 CI 单测）直接导入；真正的模型实现在 *_tagger_engine.py。
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass
class TagResult:
    """单个推理窗的打标结果。

    top:    top-k (label, prob)，已按概率降序——对外通用输出。
    scores: {label: prob} 全类概率——供 scene_mapper 按桶成员折叠、下游自定阈值。
    embedding: 倒数第二层向量（可选，下游迁移用）。
    """
    top: list[tuple[str, float]]
    scores: dict[str, float] = field(default_factory=dict)
    embedding: np.ndarray | None = None


@runtime_checkable
class AudioTaggerEngine(Protocol):
    """音频打标引擎契约。BACKEND 取 "panns" | "yamnet"。"""
    BACKEND: str
    labels: list[str]

    def load(self) -> None: ...

    def predict_window(self, wav: np.ndarray, sr: int, topk: int = 5) -> TagResult: ...


def load_labels_csv(path: str) -> list[str]:
    """读取 AudioSet 风格标签表（表头 index,mid,display_name），按 index 升序返回 display_name。

    PANNs（527）与 YAMNet（521）共用此格式；index 即模型输出向量的下标。
    """
    rows: dict[int, str] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[int(r["index"])] = r["display_name"]
    if not rows:
        raise ValueError(f"标签表为空: {path}")
    n = max(rows) + 1
    if len(rows) != n:
        raise ValueError(f"标签表 index 不连续: {path}（{len(rows)} 行，max index {max(rows)}）")
    return [rows[i] for i in range(n)]


def topk_from_probs(probs: np.ndarray, labels: list[str], k: int = 5) -> list[tuple[str, float]]:
    """概率向量 → top-k (label, prob)，按概率降序。k<=0 或越界自动夹取。"""
    probs = np.asarray(probs).ravel()
    k = max(1, min(k, probs.shape[0]))
    idx = np.argpartition(probs, -k)[-k:]
    idx = idx[np.argsort(probs[idx])[::-1]]
    return [(labels[i], float(probs[i])) for i in idx]
