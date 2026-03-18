import logging
from funasr import AutoModel
from app.utils.model_manager import ensure_model_modelscope
from app.config import MODEL_LOCAL_MAP, MODELSCOPE_ONLY_REPO_MAP

logger = logging.getLogger(__name__)


class VADEngine:
    """FSMN-VAD 语音活动检测引擎"""

    def __init__(self, use_onnx: bool = False):
        self._use_onnx = use_onnx
        # FunASR 1.3.1 不支持 ONNX VAD，统一使用 PyTorch 版本
        self._model_key = "vad"
        self._model = None

    def load(self):
        local_dir = MODEL_LOCAL_MAP[self._model_key]
        repo_id = MODELSCOPE_ONLY_REPO_MAP[self._model_key]
        ensure_model_modelscope(repo_id, local_dir)

        self._model = AutoModel(
            model=local_dir,
            model_revision="v2.0.4",
            device="cpu",
            disable_update=True,
        )
        logger.info(f"VAD 模型已加载 (PyTorch): {local_dir}")

    def detect(self, audio_path: str) -> list[tuple[int, int]]:
        """
        检测语音段，返回时间区间列表（毫秒）。

        返回:
            [(start_ms, end_ms), ...]
        """
        if self._model is None:
            raise RuntimeError("VAD 模型未加载，请先调用 load()")

        res = self._model.generate(input=audio_path)

        segments = []
        if res and len(res) > 0 and res[0]:
            # FunASR VAD 输出格式: [[start, end], [start, end], ...]
            raw = res[0].get("value", [])
            for pair in raw:
                if len(pair) == 2:
                    segments.append((int(pair[0]), int(pair[1])))

        logger.info(f"VAD 检测到 {len(segments)} 个语音段: {audio_path}")
        return segments

    def unload(self):
        self._model = None
        logger.info("VAD 模型已卸载")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
