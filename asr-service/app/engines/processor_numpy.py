"""
processor_numpy.py
─────────────────────────────────────────────────────────────────────
纯 numpy 实现 Qwen3-ASR Processor，完整取代 torch / transformers / qwen_asr。

功能：
  • Mel 特征提取  ─ 与 WhisperFeatureExtractor 完全对齐
  • BPE 解码      ─ byte-level GPT-2 风格，从 vocab.json 读取
  • Prompt 组装   ─ 从 prompt_template.json 读取预计算 IDs

依赖：
  numpy（已有）、pathlib（标准库）
  不需要 torch、transformers、qwen_asr

移植自 QwenASRMiniTool/processor_numpy.py
"""
from __future__ import annotations

import json
import numpy as np
from pathlib import Path

# ══════════════════════════════════════════════════════════════════════
# Mel 特征提取（对齐 WhisperFeatureExtractor）
# ══════════════════════════════════════════════════════════════════════

_N_FFT = 400
_HOP = 160
_N_MELS = 128
_N_SAMPLES = 480_000          # 30s × 16000
_NB_FRAMES = 3000             # nb_max_frames
_SR = 16_000

_MEL_FILTERS: np.ndarray | None = None


def _load_mel_filters(model_dir: Path | None = None) -> np.ndarray:
    """
    载入从 WhisperFeatureExtractor 导出的 mel filterbank。
    形状为 (n_freqs, n_mels) = (201, 128)，由 generate_prompt_template.py 产生。
    """
    global _MEL_FILTERS
    if _MEL_FILTERS is not None:
        return _MEL_FILTERS

    candidates: list[Path] = []
    if model_dir is not None:
        candidates.append(model_dir / "mel_filters.npy")
        candidates.append(model_dir.parent / "mel_filters.npy")
    candidates.append(Path(__file__).parent.parent.parent / "models" / "mel_filters.npy")

    for p in candidates:
        if p.exists():
            raw = np.load(str(p))
            if raw.shape == (_N_MELS, _N_FFT // 2 + 1):
                _MEL_FILTERS = raw.astype(np.float32)
            elif raw.shape == (_N_FFT // 2 + 1, _N_MELS):
                _MEL_FILTERS = raw.T.astype(np.float32)
            else:
                raise ValueError(f"mel_filters.npy shape {raw.shape} 不符预期")
            return _MEL_FILTERS

    raise FileNotFoundError(
        "找不到 mel_filters.npy，请确保模型目录包含此文件。"
    )


# 周期性汉宁窗（与 transformers window_function(periodic=True) 一致）
_HANN_WINDOW: np.ndarray = np.hanning(_N_FFT + 1)[:-1].astype(np.float32)


# ══════════════════════════════════════════════════════════════════════
# BPE 解码（byte-level GPT-2 风格）
# ══════════════════════════════════════════════════════════════════════

def _build_byte_decoder() -> dict[str, int]:
    """GPT-2 byte-to-unicode mapping 的反向版本（unicode char → byte value）。"""
    bs = (list(range(ord("!"), ord("~") + 1))
          + list(range(ord("¡"), ord("¬") + 1))
          + list(range(ord("®"), ord("ÿ") + 1)))
    cs = list(bs)
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return {chr(c): b for b, c in zip(bs, cs)}


_BYTE_DECODER: dict[str, int] = _build_byte_decoder()


def _bpe_decode(token_strings: list[str]) -> str:
    """将 BPE token 字串列表解码回 UTF-8 文字。"""
    merged = "".join(token_strings)
    byte_vals = []
    for ch in merged:
        bval = _BYTE_DECODER.get(ch)
        if bval is not None:
            byte_vals.append(bval)
    try:
        return bytes(byte_vals).decode("utf-8", errors="replace")
    except Exception:
        return merged


# ══════════════════════════════════════════════════════════════════════
# LightProcessor：组合 Mel 提取 + BPE 解码
# ══════════════════════════════════════════════════════════════════════

class LightProcessor:
    """
    纯 NumPy ASR Processor，不依赖 torch/transformers。

    属性：
        pad_id  : int   ← <|audio_pad|> 的 token id
        eos_id  : int   ← <|im_end|>
        eot_id  : int   ← <|endoftext|>
        supported_languages : list[str]  ← 支持的语系名称清单
    """

    def __init__(self, model_dir: Path):
        _load_mel_filters(model_dir)
        self._model_dir = model_dir

        # 读取 prompt template
        tpl_path = model_dir / "prompt_template.json"
        if not tpl_path.exists():
            tpl_path = model_dir.parent / "prompt_template.json"
        if not tpl_path.exists():
            raise FileNotFoundError(
                f"找不到 prompt_template.json，已搜索: {model_dir}, {model_dir.parent}"
            )
        with open(tpl_path, "r", encoding="utf-8") as f:
            tpl = json.load(f)

        self._prefix_ids: list[int] = tpl["prefix_ids"]
        self._suffix_ids: list[int] = tpl["suffix_ids"]
        self._n_audio: int = tpl["n_audio_tokens"]
        self.pad_id: int = tpl["audio_pad_id"]
        self.eos_id: int = tpl["eos_id"]
        self.eot_id: int = tpl["eot_id"]
        self._special_ids: set[int] = set(tpl["special_ids"])

        self._n_samples: int = tpl.get("n_samples", _N_SAMPLES)
        self._nb_frames: int = tpl.get("nb_frames", _NB_FRAMES)

        # 语系相关
        self._language_suffix_ids: dict[str, list[int]] = tpl.get("language_suffix_ids", {})
        self.supported_languages: list[str] = tpl.get(
            "supported_languages", list(self._language_suffix_ids.keys())
        )

        # id → token string 映射（BPE decode 用）
        vocab_path = model_dir / "vocab.json"
        with open(vocab_path, "r", encoding="utf-8") as f:
            vocab: dict[str, int] = json.load(f)
        self._id2str: dict[int, str] = {v: k for k, v in vocab.items()}

        # 补上 added_tokens
        tc_path = model_dir / "tokenizer_config.json"
        if tc_path.exists():
            with open(tc_path, "r", encoding="utf-8") as f:
                tc = json.load(f)
            for tok_id_str, info in tc.get("added_tokens_decoder", {}).items():
                self._id2str[int(tok_id_str)] = info["content"]

    def _extract_mel(self, audio: np.ndarray) -> np.ndarray:
        """输出 [1, 128, nb_frames] float32 mel"""
        audio = audio.astype(np.float32)
        if len(audio) > self._n_samples:
            audio = audio[:self._n_samples]
        if len(audio) < self._n_samples:
            audio = np.pad(audio, (0, self._n_samples - len(audio)))

        half = _N_FFT // 2
        audio_c = np.pad(audio, half, mode="reflect")
        frames = np.lib.stride_tricks.sliding_window_view(audio_c, _N_FFT)[::_HOP]
        frames = frames[:self._nb_frames].astype(np.float32)
        windowed = frames * _HANN_WINDOW

        stft = np.fft.rfft(windowed, axis=1)
        power = np.abs(stft).astype(np.float32) ** 2
        mel = (_load_mel_filters(self._model_dir) @ power.T)

        log_mel = np.log10(np.maximum(mel, 1e-10))
        log_mel = np.maximum(log_mel, log_mel.max() - 8.0)
        log_mel = (log_mel + 4.0) / 4.0
        return log_mel[np.newaxis, :, :].astype(np.float32)

    def prepare(
        self,
        audio: np.ndarray,
        language: str | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        输入：16kHz float32 音频
        参数：language - 语系名称（如 "Chinese"、"English"），None 表示自动检测
        输出：(mel, input_ids)
            mel       : [1, 128, nb_frames] float32
            input_ids : [1, L]              int64
        """
        mel = self._extract_mel(audio)

        # 组装 suffix（含强制语系）
        if language and language in self._language_suffix_ids:
            suffix_ids = self._suffix_ids + self._language_suffix_ids[language]
        else:
            suffix_ids = self._suffix_ids

        ids = np.array(
            self._prefix_ids + [self.pad_id] * self._n_audio + suffix_ids,
            dtype=np.int64,
        )[np.newaxis, :]

        return mel, ids

    def decode(self, token_ids: list[int], skip_special: bool = True) -> str:
        """将生成的 token id 列表解码为 UTF-8 字串。"""
        parts: list[str] = []
        for tid in token_ids:
            if skip_special and tid in self._special_ids:
                continue
            s = self._id2str.get(tid, "")
            if s:
                parts.append(s)
        return _bpe_decode(parts)
