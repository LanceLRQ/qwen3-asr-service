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
                 unfixed_chunk_num=2, unfixed_token_num=5, enable_align=True):
        self._model_size = model_size
        self._gpu_mem = gpu_memory_utilization
        self._max_model_len = max_model_len
        self._chunk_size_sec = clamp_chunk_size_sec(chunk_size_sec)
        self._unfixed_chunk_num = unfixed_chunk_num
        self._unfixed_token_num = unfixed_token_num
        # 离线词级时间戳用：加载 ForcedAligner（与流式无关，仅 transcribe 用）。
        # 加载失败时降级为 False（仍可出文本，只是无 words）。
        self._enable_align = enable_align
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

        llm_kwargs = dict(gpu_memory_utilization=self._gpu_mem)
        if self._max_model_len:
            llm_kwargs["max_model_len"] = self._max_model_len

        # 可选 ForcedAligner（离线 transcribe 出词级时间戳用）：在主进程加载一份
        # transformers 对齐模型（与 vLLM EngineCore 子进程相互独立，各占一份显存）。
        # 下载/加载失败则降级为无对齐（仍可出文本）。aligner 仓库 HF 与 modelscope 都有。
        if self._enable_align:
            import torch
            try:
                aligner_local = MODEL_LOCAL_MAP["aligner"]
                ensure_model(MODEL_REPO_MAP[source]["aligner"], aligner_local)
                llm_kwargs["forced_aligner"] = aligner_local
                llm_kwargs["forced_aligner_kwargs"] = dict(
                    dtype=torch.bfloat16, device_map="cuda")
                logger.info(f"对齐模型将加载: {aligner_local}")
            except Exception as e:
                logger.warning(f"对齐模型准备失败，离线降级为无词级时间戳: {e}")
                self._enable_align = False

        self._model = Qwen3ASRModel.LLM(model=local_dir, **llm_kwargs)
        logger.info(f"vLLM ASR 引擎已加载: size={self._model_size} "
                    f"gpu_mem={self._gpu_mem} max_model_len={self._max_model_len or '默认'} "
                    f"chunk={self._chunk_size_sec}s align={self._enable_align}")

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
    def transcribe(self, audio_path: str, language=None, with_words: bool = False):
        """对整段音频做离线转写，返回 [ASRTranscription, ...]（单文件即长度 1）。

        with_words 且对齐器已加载时产词级时间戳（state 内部已加 chunk offset = 绝对时间）。
        长音频由 qwen_asr 内部按 MAX_*_INPUT_SECONDS 切块后合并回单结果。
        """
        if self._model is None:
            raise RuntimeError("vLLM ASR 引擎未加载，请先调用 load()")
        want_ts = bool(with_words) and self._enable_align
        with self._infer_lock:
            return self._model.transcribe(
                audio=audio_path, language=language, return_time_stamps=want_ts)

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def align_enabled(self) -> bool:
        return self._enable_align
