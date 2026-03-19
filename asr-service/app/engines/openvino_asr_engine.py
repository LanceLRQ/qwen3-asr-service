"""
openvino_asr_engine.py
─────────────────────────────────────────────────────────────────────
OpenVINO ASR 引擎，使用 INT8 量化的 OpenVINO IR 模型进行 CPU 推理。
与 QwenASREngine 保持相同接口，供 ASRPipeline 使用。
"""
import logging
from pathlib import Path

import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# pipeline 传入的语言代码 → processor 期望的语言名称
_LANG_MAP = {
    "zh": "Chinese",
    "en": "English",
    "ja": "Japanese",
    "ko": "Korean",
    "yue": "Cantonese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "ar": "Arabic",
    "th": "Thai",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "ms": "Malay",
}


class OpenVINOASREngine:
    """OpenVINO CPU ASR 引擎（INT8 量化，纯 NumPy 预处理）"""

    def __init__(self, model_size: str = "0.6b"):
        self._model_size = model_size
        self._audio_enc = None
        self._embedder = None
        self._dec_req = None
        self._dec_prefill = None
        self._dec_kv = None
        self._processor = None

    def load(self):
        """下载模型 + 编译 OpenVINO 子模型"""
        try:
            import openvino as ov
        except ImportError:
            raise ImportError(
                "CPU 模式需要 openvino 包，请执行: pip install openvino>=2024.0"
            )

        from app.engines.processor_numpy import LightProcessor
        from app.utils.openvino_model_downloader import ensure_openvino_model

        model_dir = ensure_openvino_model(self._model_size)
        ov_dir = Path(model_dir)

        logger.info(f"开始编译 OpenVINO 模型（CPU）...")

        cpu_cfg = {
            "PERFORMANCE_HINT": "LATENCY",
            "ENABLE_HYPER_THREADING": "YES",
        }
        core = ov.Core()
        self._audio_enc = core.compile_model(
            str(ov_dir / "audio_encoder_model.xml"), "CPU", cpu_cfg
        )
        self._embedder = core.compile_model(
            str(ov_dir / "thinker_embeddings_model.xml"), "CPU", cpu_cfg
        )

        if self._model_size == "1.7b":
            self._dec_prefill = core.compile_model(
                str(ov_dir / "decoder_prefill_kv_model.xml"), "CPU", cpu_cfg
            )
            self._dec_kv = core.compile_model(
                str(ov_dir / "decoder_kv_model.xml"), "CPU", cpu_cfg
            )
            self._dec_req = self._dec_prefill.create_infer_request()
        else:
            dec_compiled = core.compile_model(
                str(ov_dir / "decoder_model.xml"), "CPU", cpu_cfg
            )
            self._dec_req = dec_compiled.create_infer_request()

        self._processor = LightProcessor(ov_dir)

        logger.info(
            f"OpenVINO ASR 模型已加载: size={self._model_size}, device=CPU"
        )

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
    ) -> list[dict]:
        """
        对音频执行 ASR 识别。

        返回:
            [{"text": str}]
        """
        if self._processor is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        # 1. 读取音频
        audio, sr = sf.read(audio_path, dtype="float32")
        if sr != 16000:
            raise ValueError(f"音频采样率必须为 16kHz，当前为 {sr}Hz")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        # 2. 语言代码映射
        lang_name = self._map_language(language)

        # 3. 预处理：mel + input_ids
        mel, ids = self._processor.prepare(audio, language=lang_name)

        # 4. 推理
        text = self._infer(mel, ids)

        return [{"text": text}]

    def _map_language(self, language: str | None) -> str | None:
        """将短语言代码映射为 processor 期望的全称"""
        if language is None:
            return None
        # 先检查是否已经是全称（如 "Chinese"）
        if language in self._processor._language_suffix_ids:
            return language
        # 短代码映射
        return _LANG_MAP.get(language, language)

    def _infer(self, mel: np.ndarray, ids: np.ndarray, max_tokens: int = 300) -> str:
        """
        OpenVINO 推理：
        1. audio_encoder(mel) → audio_embeddings
        2. thinker_embeddings(input_ids) → text_embeddings
        3. 融合 audio + text embeddings
        4. decoder 自回归解码
        5. BPE decode → text
        """
        ae = list(self._audio_enc({"mel": mel}).values())[0]
        te = list(self._embedder({"input_ids": ids}).values())[0]

        combined = te.copy()
        pad_id = self._processor.pad_id
        mask = ids[0] == pad_id
        n_pad = int(mask.sum())
        n_audio = ae.shape[1]
        if n_pad != n_audio:
            mn = min(n_pad, n_audio)
            combined[0, np.where(mask)[0][:mn]] = ae[0, :mn]
        else:
            combined[0, mask] = ae[0]

        seq_len = combined.shape[1]
        pos = np.arange(seq_len, dtype=np.int64)[np.newaxis, :]

        eos = self._processor.eos_id
        eot = self._processor.eot_id
        gen_tokens: list[int] = []

        if self._model_size == "1.7b":
            out = self._dec_req.infer({0: combined, "position_ids": pos})
            logits = list(out.values())[0]
            next_token = int(np.argmax(logits[0, -1, :]))
            cur_pos = seq_len

            kv_req = self._dec_kv.create_infer_request()

            while next_token not in (eos, eot) and len(gen_tokens) < max_tokens:
                gen_tokens.append(next_token)
                emb = list(self._embedder(
                    {"input_ids": np.array([[next_token]], dtype=np.int64)}
                ).values())[0]
                out = kv_req.infer({
                    0: emb,
                    "position_ids": np.array([[cur_pos]], dtype=np.int64),
                })
                logits = list(out.values())[0]
                next_token = int(np.argmax(logits[0, -1, :]))
                cur_pos += 1
        else:
            self._dec_req.reset_state()
            out = self._dec_req.infer({0: combined, "position_ids": pos})
            logits = list(out.values())[0]
            next_token = int(np.argmax(logits[0, -1, :]))
            cur_pos = seq_len

            while next_token not in (eos, eot) and len(gen_tokens) < max_tokens:
                gen_tokens.append(next_token)
                emb = list(self._embedder(
                    {"input_ids": np.array([[next_token]], dtype=np.int64)}
                ).values())[0]
                out = self._dec_req.infer(
                    {0: emb, "position_ids": np.array([[cur_pos]], dtype=np.int64)}
                )
                logits = list(out.values())[0]
                next_token = int(np.argmax(logits[0, -1, :]))
                cur_pos += 1

        raw = self._processor.decode(gen_tokens)
        if "<asr_text>" in raw:
            raw = raw.split("<asr_text>", 1)[1]
        return raw.strip()

    def unload(self):
        """释放资源"""
        self._audio_enc = None
        self._embedder = None
        self._dec_req = None
        self._dec_prefill = None
        self._dec_kv = None
        self._processor = None
        logger.info("OpenVINO ASR 模型已卸载")

    @property
    def is_loaded(self) -> bool:
        return self._processor is not None

    @property
    def align_enabled(self) -> bool:
        return False
