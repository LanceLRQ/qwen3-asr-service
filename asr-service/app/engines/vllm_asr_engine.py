"""Qwen3-ASR vLLM 后端封装（原生流式，路线 A）。

所有 vLLM / qwen-asr[vllm] 相关 import 集中在本模块、且惰性（仅 load() 内）；
standard / CPU 模式不导入本模块，故不依赖 vLLM。

真实流式 API（qwen_asr.inference.qwen3_asr）：
    Qwen3ASRModel.LLM(model, **kwargs)        # kwargs 透传 vllm.LLM；import 即注册 vLLM 架构
    init_streaming_state(language, chunk_size_sec, unfixed_chunk_num, unfixed_token_num)
    streaming_transcribe(pcm16k, state)        # state.text 为累计全文（非增量）
    finish_streaming_transcribe(state)         # 冲刷尾音
底层为同步 vllm.LLM、单流、无 batch、无时间戳；generate 非并发安全 → _infer_lock 串行化。
"""
import logging
import threading

import numpy as np

from app.utils.model_manager import ensure_model
from app.config import MODEL_REPO_MAP, MODEL_LOCAL_MAP, MODEL_SOURCE

logger = logging.getLogger(__name__)

# 流式解码块大小取值范围（秒）：过小→partial 过碎且每块重算整段；过大→反馈迟钝
CHUNK_SIZE_SEC_MIN = 0.5
CHUNK_SIZE_SEC_MAX = 5.0


def clamp_chunk_size_sec(value: float) -> float:
    return max(CHUNK_SIZE_SEC_MIN, min(CHUNK_SIZE_SEC_MAX, float(value)))


class VLLMASREngine:
    """Qwen3-ASR vLLM 引擎封装。一个进程内一份模型，会话间共享，generate 串行。"""

    def __init__(self, model_size="0.6b", *, gpu_memory_utilization=0.8,
                 max_model_len=None, chunk_size_sec=1.0,
                 unfixed_chunk_num=2, unfixed_token_num=5, enable_align=True,
                 align_device="cuda", infer_batch_size=4):
        self._model_size = model_size
        self._gpu_mem = gpu_memory_utilization
        self._max_model_len = max_model_len
        self._chunk_size_sec = clamp_chunk_size_sec(chunk_size_sec)
        self._unfixed_chunk_num = unfixed_chunk_num
        self._unfixed_token_num = unfixed_token_num
        # 离线词级时间戳用：加载 ForcedAligner（与流式无关，仅 transcribe 用）。
        # 加载失败时降级为 False（仍可出文本，只是无 words）。
        self._enable_align = enable_align
        # 对齐器设备：cuda（默认，快，但显存在 vLLM 的 gpu_memory_utilization 预算之外，
        # 长音频对齐前向激活大、余量不足时 OOM）/ cpu（无 GPU 争用，float32，慢但稳）。
        self._align_device = str(align_device or "cuda").lower()
        # 离线一次对齐/ASR 的音频块数上限（qwen_asr max_inference_batch_size）：transcribe
        # 内部按 ≤180s 切块，默认 -1 会把全部块一次性喂对齐器前向 → 长音频激活叠加 OOM。
        # 取有界小值=逐批对齐、峰值显存随批大小线性下降（块数为 1 的短音频无差异）。
        self._infer_batch_size = int(infer_batch_size)
        self._model = None
        # vllm.LLM.generate 非并发安全：与路线 B 同思路，用锁串行化（见 §3 并发）
        self._infer_lock = threading.Lock()

    @property
    def chunk_size_sec(self) -> float:
        return self._chunk_size_sec

    def load(self):
        """加载模型。失败（未装 vLLM / 模型缺失）抛异常，由调用方决定退出。"""
        # 惰性导入：未装 vLLM 时此处 ImportError，信息明确
        from qwen_asr import Qwen3ASRModel       # import 即注册 vLLM 架构

        model_key = f"asr_{self._model_size}"
        local_dir = MODEL_LOCAL_MAP[model_key]
        source = MODEL_SOURCE if MODEL_SOURCE in MODEL_REPO_MAP else "modelscope"
        ensure_model(MODEL_REPO_MAP[source][model_key], local_dir)

        llm_kwargs = dict(gpu_memory_utilization=self._gpu_mem,
                          max_inference_batch_size=self._infer_batch_size)
        if self._max_model_len:
            llm_kwargs["max_model_len"] = self._max_model_len

        # 可选 ForcedAligner（离线 transcribe 出词级时间戳用）：在主进程加载一份
        # transformers 对齐模型，**显存在 vLLM EngineCore 的 gpu_memory_utilization 预算之外**
        # ——cuda 时须留 GPU 余量（util 过高→对齐前向 OOM），cpu 时彻底无 GPU 争用（float32）。
        # 下载/加载失败则降级为无对齐（仍可出文本）。aligner 仓库 HF 与 modelscope 都有。
        if self._enable_align:
            import torch
            try:
                aligner_local = MODEL_LOCAL_MAP["aligner"]
                ensure_model(MODEL_REPO_MAP[source]["aligner"], aligner_local)
                on_cpu = self._align_device.startswith("cpu")
                llm_kwargs["forced_aligner"] = aligner_local
                llm_kwargs["forced_aligner_kwargs"] = dict(
                    # CPU 上 bf16 多数算子慢/不支持 → float32；GPU 与主模型一致 bf16
                    dtype=torch.float32 if on_cpu else torch.bfloat16,
                    device_map="cpu" if on_cpu else "cuda")
                logger.info(f"对齐模型将加载: {aligner_local} "
                            f"(device={'cpu' if on_cpu else 'cuda'})")
            except Exception as e:
                logger.warning(f"对齐模型准备失败，离线降级为无词级时间戳: {e}")
                self._enable_align = False

        self._model = Qwen3ASRModel.LLM(model=local_dir, **llm_kwargs)
        logger.info(f"vLLM ASR 引擎已加载: size={self._model_size} "
                    f"gpu_mem={self._gpu_mem} max_model_len={self._max_model_len or '默认'} "
                    f"chunk={self._chunk_size_sec}s align={self._enable_align}"
                    f"{f'@{self._align_device}' if self._enable_align else ''} "
                    f"infer_batch={self._infer_batch_size}")

    # ── 三段式流式（同步；调用方在线程池内执行，避免阻塞事件循环）──
    def new_state(self, language=None, chunk_size_sec=None):
        """为一句新建流式状态。chunk_size_sec 可按会话覆盖（缺省=引擎默认）。"""
        css = clamp_chunk_size_sec(chunk_size_sec) if chunk_size_sec else self._chunk_size_sec
        return self._model.init_streaming_state(
            language=language, chunk_size_sec=css,
            unfixed_chunk_num=self._unfixed_chunk_num,
            unfixed_token_num=self._unfixed_token_num)

    def feed(self, pcm16k: np.ndarray, state):
        """喂一块 16k PCM（float32 或 int16，内部自转），返回 (text, language)；state 原地更新。"""
        with self._infer_lock:
            self._model.streaming_transcribe(pcm16k, state)
        return state.text, state.language

    def finish(self, state):
        """冲刷尾音并收尾，返回 (text, language)。"""
        with self._infer_lock:
            self._model.finish_streaming_transcribe(state)
        return state.text, state.language

    # ── 离线一次性转写（同步；与流式共用同一 vllm.LLM 与 _infer_lock，故串行）──
    def transcribe(self, audio, language=None, with_words: bool = False):
        """对一段音频做离线转写，返回 [ASRTranscription, ...]（单输入即长度 1）。

        audio 可为文件路径或 (np.ndarray, sr) / np.ndarray（qwen_asr 内部归一化），便于离线
        逐块转写时直接传切块数组、免临时文件。with_words 且对齐器已加载时产词级时间戳。
        长音频由 qwen_asr 内部按 MAX_*_INPUT_SECONDS 再切块后合并回单结果。
        """
        if self._model is None:
            raise RuntimeError("vLLM ASR 引擎未加载，请先调用 load()")
        want_ts = bool(with_words) and self._enable_align
        with self._infer_lock:
            return self._model.transcribe(
                audio=audio, language=language, return_time_stamps=want_ts)

    def split_chunks(self, wav, sr, chunk_sec):
        """按低能量（静音）边界把长音频切块，返回 [(chunk_wav, offset_sec)]。

        复用 qwen_asr 的 split_audio_into_chunks（与其 transcribe 内部对齐切块同一函数，
        保证块拼接=原音频）。供离线逐块转写：进度反馈 / 峰值显存 / 取消粒度。惰性 import
        使本依赖只在 vLLM 环境触发。
        """
        from qwen_asr.inference.utils import split_audio_into_chunks
        return split_audio_into_chunks(wav, sr, max_chunk_sec=float(chunk_sec))

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def align_enabled(self) -> bool:
        return self._enable_align
