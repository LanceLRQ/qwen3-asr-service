import logging
import threading
from funasr import AutoModel
from app.utils.model_manager import ensure_model_modelscope
from app.config import MODEL_LOCAL_MAP, MODELSCOPE_ONLY_REPO_MAP
import app.config as cfg

logger = logging.getLogger(__name__)


class VADEngine:
    """FSMN-VAD 语音活动检测引擎（始终使用 PyTorch 后端）"""

    BACKEND = "pytorch"

    def __init__(self):
        self._model_key = "vad"
        self._model = None
        # funasr AutoModel.generate 非线程安全：离线 detect 与在线流式
        # （StreamingVADEngine）共用同一模型实例，必须共用此推理锁
        self._infer_lock = threading.Lock()

    def load(self):
        local_dir = MODEL_LOCAL_MAP[self._model_key]
        repo_id = MODELSCOPE_ONLY_REPO_MAP[self._model_key]
        ensure_model_modelscope(repo_id, local_dir)

        # speech_noise_thres 仅在构造时读入 vad_opts（funasr==1.3.1，generate 运行时
        # 不支持按调用覆盖该参数），故离线 detect 与在线 StreamingVADEngine 统一此全局阈值
        self._model = AutoModel(
            model=local_dir,
            model_revision="v2.0.4",
            device="cpu",
            disable_update=True,
            speech_noise_thres=cfg.VAD_SPEECH_NOISE_THRES,
        )
        logger.info(f"VAD 模型已加载 (PyTorch): {local_dir} "
                    f"speech_noise_thres={cfg.VAD_SPEECH_NOISE_THRES}")

    def detect(self, audio_path: str) -> list[tuple[int, int]]:
        """
        检测语音段，返回时间区间列表（毫秒）。

        返回:
            [(start_ms, end_ms), ...]
        """
        if self._model is None:
            raise RuntimeError("VAD 模型未加载，请先调用 load()")

        with self._infer_lock:
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
