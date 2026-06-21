"""YAMNet 通用音频打标引擎（521 类 AudioSet，非推荐轻量备选）。

定位（设计 §3.4）：CPU 极轻备选，精度低于 PANNs；仅 --audio-tagging-engine yamnet 时启用。
集成方式：HF thelou1s 官方 Google TFLite + tflite-runtime（可选依赖，requirements-yamnet.txt）。
py3.12 无 tflite-runtime wheel 时自动改用 Google 官方后继 ai-edge-litert（同 Interpreter API）。

限制（文档亦标注）：vLLM venv 默认无 tflite-runtime → 该模式不可用；帧级粒度固定 ~0.96s；
521 类少于 PANNs 527。tflite/numpy 均方法内惰性导入：未选用时零依赖触发，单测可 mock。
"""
import logging
import os
import threading

import numpy as np

from app.engines.audio_tagger import TagResult, load_labels_csv, topk_from_probs
from app.utils.model_manager import ensure_model_hf
from app.config import MODEL_LOCAL_MAP, YAMNET_LABELS_CSV, TAGGING_YAMNET_REPO

logger = logging.getLogger(__name__)

_CLASSES_NUM = 521
_TARGET_SR = 16000
_MIN_SAMPLES = 16000     # YAMNet 帧 0.96s≈15360 样本；不足一帧则零填到 1s，避免空输出


def _import_interpreter():
    """优先 tflite_runtime，回退 Google 官方后继 ai-edge-litert（py3.12 wheel 可用）。"""
    try:
        from tflite_runtime.interpreter import Interpreter
        return Interpreter
    except ImportError:
        pass
    try:
        from ai_edge_litert.interpreter import Interpreter
        return Interpreter
    except ImportError as e:
        raise ImportError(
            "YAMNet 需要 tflite-runtime 或 ai-edge-litert，未安装。"
            "请 `pip install -r requirements-yamnet.txt`（py3.12 用 ai-edge-litert）。"
        ) from e


class YamnetTaggerEngine:
    BACKEND = "yamnet"

    def __init__(self, device: str = "cpu"):
        self._device = device          # 仅 CPU（TFLite），保留参数对齐引擎签名
        self._interp = None
        self._in_idx = None
        self._score_idx = None
        self.labels: list[str] = []
        self._infer_lock = threading.Lock()

    @property
    def target_sr(self) -> int:
        return _TARGET_SR

    @property
    def is_loaded(self) -> bool:
        return self._interp is not None

    def load(self) -> None:
        Interpreter = _import_interpreter()
        local_dir = MODEL_LOCAL_MAP["tagging_yamnet"]
        ensure_model_hf(TAGGING_YAMNET_REPO, local_dir)
        model_path = _find_tflite(local_dir)
        if model_path is None:
            raise FileNotFoundError(f"未在 {local_dir} 找到 YAMNet *.tflite 模型文件")

        interp = Interpreter(model_path=model_path)
        self._in_idx = interp.get_input_details()[0]["index"]
        # 定位 521 类得分输出（YAMNet TFLite 另含 embedding/spectrogram 输出）
        self._score_idx = None
        for o in interp.get_output_details():
            if int(o["shape"][-1]) == _CLASSES_NUM:
                self._score_idx = o["index"]
                break
        if self._score_idx is None:
            self._score_idx = interp.get_output_details()[0]["index"]
        self._interp = interp

        self.labels = load_labels_csv(YAMNET_LABELS_CSV)
        if len(self.labels) != _CLASSES_NUM:
            raise ValueError(f"YAMNet 标签数应为 {_CLASSES_NUM}，实际 {len(self.labels)}")
        logger.info(f"YAMNet 打标引擎已加载（非推荐轻量备选）: {model_path}")

    def predict_window(self, wav: np.ndarray, sr: int, topk: int = 5) -> TagResult:
        if self._interp is None:
            raise RuntimeError("YAMNet 打标引擎未加载，请先调用 load()")

        x = np.asarray(wav, dtype=np.float32).ravel()
        if sr != _TARGET_SR:
            import librosa
            x = librosa.resample(x, orig_sr=sr, target_sr=_TARGET_SR)
        if x.size < _MIN_SAMPLES:
            x = np.pad(x, (0, _MIN_SAMPLES - x.size))

        with self._infer_lock:
            self._interp.resize_tensor_input(self._in_idx, [x.shape[0]], strict=False)
            self._interp.allocate_tensors()
            self._interp.set_tensor(self._in_idx, x)
            self._interp.invoke()
            scores = np.asarray(self._interp.get_tensor(self._score_idx))
        probs = scores.mean(axis=0) if scores.ndim > 1 else scores.ravel()

        top = topk_from_probs(probs, self.labels, topk)
        scores_map = {lab: float(probs[i]) for i, lab in enumerate(self.labels)}
        return TagResult(top=top, scores=scores_map)


# 标准全模型（waveform→scores/embedding/spectrogram），符合 AudioTagger 契约
_PREFERRED_TFLITE = "lite-model_yamnet_tflite_1.tflite"


def _find_tflite(local_dir: str) -> str | None:
    """定位 CPU 版 YAMNet tflite：排除 coral/edgetpu（需专用硬件），优先标准全模型。"""
    cands = [n for n in sorted(os.listdir(local_dir))
             if n.endswith(".tflite")
             and "coral" not in n.lower() and "edgetpu" not in n.lower()]
    if not cands:
        return None
    name = _PREFERRED_TFLITE if _PREFERRED_TFLITE in cands else cands[0]
    return os.path.join(local_dir, name)
