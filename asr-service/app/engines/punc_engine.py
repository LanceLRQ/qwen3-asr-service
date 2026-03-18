import logging
from funasr import AutoModel
from app.utils.model_manager import ensure_model_modelscope
from app.config import MODEL_LOCAL_MAP, MODELSCOPE_ONLY_REPO_MAP

logger = logging.getLogger(__name__)


class PuncEngine:
    """CT-Transformer 标点恢复引擎"""

    def __init__(self, use_onnx: bool = False):
        self._use_onnx = use_onnx
        self._model_key = "punc_onnx" if use_onnx else "punc"
        self._model = None

    def load(self):
        local_dir = MODEL_LOCAL_MAP[self._model_key]
        repo_id = MODELSCOPE_ONLY_REPO_MAP[self._model_key]
        ensure_model_modelscope(repo_id, local_dir)

        self._model = AutoModel(
            model=local_dir,
            model_revision="v2.0.4",
        )
        backend = "ONNX" if self._use_onnx else "PyTorch"
        logger.info(f"标点模型已加载 ({backend}): {local_dir}")

    def restore(self, text: str) -> str:
        """对文本补充标点符号"""
        if self._model is None:
            raise RuntimeError("标点模型未加载，请先调用 load()")

        if not text or not text.strip():
            return text

        res = self._model.generate(input=text)
        if res and len(res) > 0:
            return res[0].get("text", text)
        return text

    def unload(self):
        self._model = None
        logger.info("标点模型已卸载")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None
