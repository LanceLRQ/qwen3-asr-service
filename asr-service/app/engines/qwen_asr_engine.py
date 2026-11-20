import logging
import threading
import warnings
import torch
from app.engines.asr_result import ASRResult, BackendCapabilities, from_qwen
from app.utils.model_manager import ensure_model
from app.config import MODEL_REPO_MAP, MODEL_LOCAL_MAP, MODEL_SOURCE

logger = logging.getLogger(__name__)


class QwenASREngine:
    """Qwen3-ASR 语音识别引擎（支持可选的 ForcedAligner）"""

    def __init__(
        self,
        model_size: str = "0.6b",
        device: str = "cuda:0",
        enable_align: bool = True,
        asr_batch_size: int = 32,
    ):
        if asr_batch_size <= 0:
            raise ValueError(f"asr_batch_size 必须为正整数，收到 {asr_batch_size}")
        self._model_size = model_size
        self._device = device
        self._enable_align = enable_align
        self._asr_batch_size = asr_batch_size
        self._model = None
        # Qwen3ASRModel.generate 非线程安全：prefill 写 self.rope_deltas、
        # decode 步回读（modeling_qwen3_asr.py:1221/1224），并发调用会交叉污染
        # 位置编码（同形状静默劣化、异形状崩溃）。离线/流式路径共用此锁串行化。
        self._infer_lock = threading.Lock()

    def load(self):
        from qwen_asr import Qwen3ASRModel

        # 确保 ASR 模型已下载
        model_key = f"asr_{self._model_size}"
        local_dir = MODEL_LOCAL_MAP[model_key]
        source = MODEL_SOURCE if MODEL_SOURCE in MODEL_REPO_MAP else "modelscope"
        repo_id = MODEL_REPO_MAP[source][model_key]
        ensure_model(repo_id, local_dir)

        # 构建加载参数
        dtype = torch.bfloat16 if self._device.startswith("cuda") else torch.float32
        load_kwargs = dict(
            pretrained_model_name_or_path=local_dir,
            dtype=dtype,
            device_map=self._device,
            max_inference_batch_size=self._asr_batch_size,
            max_new_tokens=256,
        )

        # 可选加载 ForcedAligner
        if self._enable_align:
            aligner_local = MODEL_LOCAL_MAP["aligner"]
            aligner_repo = MODEL_REPO_MAP[source]["aligner"]
            try:
                ensure_model(aligner_repo, aligner_local)
                load_kwargs["forced_aligner"] = aligner_local
                load_kwargs["forced_aligner_kwargs"] = dict(
                    dtype=dtype,
                    device_map=self._device,
                )
                logger.info(f"对齐模型将加载: {aligner_local}")
            except Exception as e:
                logger.warning(f"对齐模型下载失败，降级为无对齐模式: {e}")
                self._enable_align = False

        self._model = Qwen3ASRModel.from_pretrained(**load_kwargs)

        # 抑制 transformers 每次 generate 时的 pad_token_id 警告
        logging.getLogger("transformers.generation.utils").setLevel(logging.ERROR)

        logger.info(
            f"Qwen ASR 模型已加载: size={self._model_size}, "
            f"device={self._device}, align={self._enable_align}"
        )

    def transcribe(
        self,
        audio_path: str,
        language: str | None = None,
        with_words: bool = True,
    ) -> ASRResult:
        """
        对单个音频执行 ASR 识别。

        返回:
            ASRResult
        """
        if self._model is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        with self._infer_lock:
            want_words = self._enable_align and with_words
            try:
                results = self._model.transcribe(
                    audio=audio_path, language=language, return_time_stamps=want_words)
            except Exception:
                if not want_words:
                    raise
                logger.warning("含时间戳的转写失败，尝试仅转写")
                results = self._model.transcribe(
                    audio=audio_path, language=language, return_time_stamps=False)
        return from_qwen(results, audio_path)

    def batch_transcribe(
        self,
        audio_paths: list[str],
        language: str | None = None,
        with_words: bool = True,
    ) -> list[ASRResult]:
        """
        批量音频 ASR 识别，利用 Qwen3ASRModel 内部按 max_inference_batch_size 分批并行推理。

        参数:
            audio_paths: 音频文件路径列表
            language: 语言（标量，广播到所有音频）

        返回:
            list[ASRResult]，每个元素对应一个输入音频
        """
        if self._model is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        if not audio_paths:
            return []

        with self._infer_lock:
            results = self._model.transcribe(
                audio=audio_paths,
                language=language,
                return_time_stamps=self._enable_align and with_words,
            )
        return [from_qwen([item], path) for item, path in zip(results, audio_paths, strict=True)]

    @property
    def aligner(self):
        from app.engines.forced_alignment import ForcedAlignment
        return ForcedAlignment(self._model.forced_aligner, self._infer_lock) if self._enable_align else None

    def transcribe_array(
        self,
        audio,
        sr: int = 16000,
        language: str | None = None,
        chunk_id: str | None = None,
    ) -> ASRResult:
        """对内存音频数组执行 ASR 识别（实时逐句解码，不落盘）。

        参数:
            audio: np.ndarray（float32/int16，单声道）
            sr: 采样率
            language: 语言（可选）

        返回:
            ASRResult
        """
        if self._model is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        with self._infer_lock:
            results = self._model.transcribe(
                audio=(audio, sr),
                language=language,
                return_time_stamps=self._enable_align,
            )
        return from_qwen(results, chunk_id)

    def unload(self):
        self._model = None
        if self._device.startswith("cuda"):
            torch.cuda.empty_cache()
        logger.info("Qwen ASR 模型已卸载")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(batch=True, word_timestamps=self._enable_align)

    @property
    def align_enabled(self) -> bool:
        return self._enable_align
