"""PANNs CNN14 通用音频打标引擎（527 类 AudioSet）。

16k（默认，Zenodo 原生权重，直喂 16k PCM）/ 32k（HF nicofarr，推理前重采样）双变体；
网络结构一致，仅前端参数 + 采样率 + 权重源不同。vendored CNN14 + torchlibrosa 前端 +
torch.load 权重，不依赖 panns_inference（照 campplus vendoring 先例）。

torch / torchlibrosa / librosa 均在方法内惰性导入：未启用打标时本模块零重依赖触发，
单测可在不装 torchlibrosa 的环境直接 mock 本引擎。
"""
import logging
import os
import threading

import numpy as np

from app.engines.audio_tagger import TagResult, load_labels_csv, topk_from_probs
from app.utils.model_manager import ensure_file, ensure_model_hf
from app.config import (
    MODEL_LOCAL_MAP,
    AUDIOSET_LABELS_CSV,
    TAGGING_PANNS_16K_URL,
    TAGGING_PANNS_16K_FILENAME,
    TAGGING_PANNS_32K_REPO,
)

logger = logging.getLogger(__name__)

# 变体前端参数（mel_bins/fmin 两者相同；其余按变体取）
_VARIANT_PARAMS = {
    "16k": dict(sample_rate=16000, window_size=512, hop_size=160, mel_bins=64, fmin=50, fmax=8000),
    "32k": dict(sample_rate=32000, window_size=1024, hop_size=320, mel_bins=64, fmin=50, fmax=14000),
}
_CLASSES_NUM = 527
_MIN_WEIGHT_BYTES = 50 * 1024 * 1024     # CNN14 权重 ~300MB，防 LFS 指针/下载截断


class PANNsTaggerEngine:
    BACKEND = "panns"

    def __init__(self, variant: str = "16k", device: str = "cpu"):
        if variant not in _VARIANT_PARAMS:
            raise ValueError(f"未知 PANNs 变体: {variant}（可选 16k / 32k）")
        self.variant = variant
        self._device = device
        self._model = None
        self.labels: list[str] = []
        self._infer_lock = threading.Lock()   # 共享实例跨任务/会话调用，forward 串行化
        # CNN14 五次时间池化(/32)后须 ≥1 帧，否则 RuntimeError；据此推最小采样（按 target_sr）
        self._min_samples = 32 * _VARIANT_PARAMS[variant]["hop_size"]

    @property
    def target_sr(self) -> int:
        return _VARIANT_PARAMS[self.variant]["sample_rate"]

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def load(self) -> None:
        from app.engines.panns import Cnn14

        weight_path = self._ensure_weights()
        model = Cnn14(classes_num=_CLASSES_NUM, **_VARIANT_PARAMS[self.variant])

        state = _extract_state_dict(_load_checkpoint(weight_path))
        missing, unexpected = model.load_state_dict(state, strict=False)
        # 分类头/全连接必须命中，否则判为权重不匹配（如 32k 仓库键名差异）
        critical = {"fc_audioset.weight", "fc_audioset.bias", "fc1.weight"}
        if critical & set(missing):
            raise RuntimeError(
                f"PANNs 权重与 vendored CNN14 不匹配（缺关键键 {sorted(critical & set(missing))}）；"
                f"variant={self.variant}, weight={weight_path}")
        if missing or unexpected:
            logger.warning(
                f"PANNs 加载非严格匹配: missing={len(missing)} unexpected={len(unexpected)}"
                "（前端 STFT/Mel buffer 由 torchlibrosa 复现，通常可忽略）")

        model.eval().to(self._device)
        self._model = model
        self.labels = load_labels_csv(AUDIOSET_LABELS_CSV)
        if len(self.labels) != _CLASSES_NUM:
            raise ValueError(f"AudioSet 标签数应为 {_CLASSES_NUM}，实际 {len(self.labels)}")
        logger.info(f"PANNs CNN14 打标引擎已加载: variant={self.variant} "
                    f"device={self._device} ({weight_path})")

    def predict_window(self, wav: np.ndarray, sr: int, topk: int = 5) -> TagResult:
        if self._model is None:
            raise RuntimeError("PANNs 打标引擎未加载，请先调用 load()")
        import torch

        x = np.asarray(wav, dtype=np.float32).ravel()
        if sr != self.target_sr:
            import librosa
            x = librosa.resample(x, orig_sr=sr, target_sr=self.target_sr)
        # 不足整窗的尾片/极短输入补零到模型最小采样，否则 CNN14 时间维被池化成 0 触发崩溃
        if x.size < self._min_samples:
            x = np.pad(x, (0, self._min_samples - x.size))

        t = torch.from_numpy(np.ascontiguousarray(x)).float().unsqueeze(0).to(self._device)
        with self._infer_lock, torch.no_grad():
            out = self._model(t)
        probs = out["clipwise_output"].squeeze(0).detach().cpu().numpy()
        emb = out["embedding"].squeeze(0).detach().cpu().numpy()
        top = topk_from_probs(probs, self.labels, topk)
        scores = {lab: float(probs[i]) for i, lab in enumerate(self.labels)}
        return TagResult(top=top, scores=scores, embedding=emb)

    def _ensure_weights(self) -> str:
        if self.variant == "16k":
            local_dir = MODEL_LOCAL_MAP["tagging_panns_16k"]
            weight_path = os.path.join(local_dir, TAGGING_PANNS_16K_FILENAME)
            ensure_file(TAGGING_PANNS_16K_URL, weight_path, min_bytes=_MIN_WEIGHT_BYTES)
            return weight_path
        # 32k：HF 仓库（含 state_dict 权重文件）
        local_dir = MODEL_LOCAL_MAP["tagging_panns_32k"]
        ensure_model_hf(TAGGING_PANNS_32K_REPO, local_dir)
        weight_path = _find_weight_file(local_dir)
        if weight_path is None:
            raise FileNotFoundError(
                f"未在 {local_dir} 找到 PANNs 32k 权重文件（.pth/.bin/.ckpt）")
        return weight_path


def _load_checkpoint(weight_path: str):
    """加载权重 ckpt。优先 weights_only=True；官方 PANNs ckpt 含 numpy 标量元数据会被
    严格模式拒载，此时回退 weights_only=False——权重源为硬编码官方 Zenodo DOI
    （HTTPS + 大小校验）或 HF 仓库，可信。"""
    import torch
    try:
        return torch.load(weight_path, map_location="cpu", weights_only=True)
    except Exception as e:
        logger.warning(f"权重含非张量元数据，weights_only 严格模式回退为 False"
                       f"（来源可信：官方 Zenodo/HF）: {type(e).__name__}")
        return torch.load(weight_path, map_location="cpu", weights_only=False)


def _extract_state_dict(obj):
    """从 torch.load 结果取纯 state_dict（官方 ckpt 包在 {'model': ...} 里）。"""
    if isinstance(obj, dict):
        for key in ("model", "state_dict"):
            if isinstance(obj.get(key), dict):
                return obj[key]
    return obj


def _find_weight_file(local_dir: str) -> str | None:
    """在目录里按优先扩展名找权重文件（torch.load 可读的格式）。"""
    for ext in (".pth", ".bin", ".ckpt"):
        for name in sorted(os.listdir(local_dir)):
            if name.endswith(ext):
                return os.path.join(local_dir, name)
    return None
