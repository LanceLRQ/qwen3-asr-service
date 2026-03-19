import logging
import warnings
import torch
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
    ):
        self._model_size = model_size
        self._device = device
        self._enable_align = enable_align
        self._model = None

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
            max_inference_batch_size=32,
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
    ) -> list[dict]:
        """
        对单个音频执行 ASR 识别。

        返回:
            [ASRTranscription, ...]
        """
        if self._model is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        results = self._model.transcribe(
            audio=audio_path,
            language=language,
            return_time_stamps=self._enable_align,
        )
        return results

    def batch_transcribe(
        self,
        audio_paths: list[str],
        language: str | None = None,
    ) -> list:
        """
        批量音频 ASR 识别，利用 Qwen3ASRModel 内部按 max_inference_batch_size 分批并行推理。

        参数:
            audio_paths: 音频文件路径列表
            language: 语言（标量，广播到所有音频）

        返回:
            List[ASRTranscription]，每个元素对应一个输入音频
        """
        if self._model is None:
            raise RuntimeError("ASR 模型未加载，请先调用 load()")

        if not audio_paths:
            return []

        results = self._model.transcribe(
            audio=audio_paths,
            language=language,
            return_time_stamps=self._enable_align,
        )
        return results

    def unload(self):
        self._model = None
        if self._device.startswith("cuda"):
            torch.cuda.empty_cache()
        logger.info("Qwen ASR 模型已卸载")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def align_enabled(self) -> bool:
        return self._enable_align
