import os
import shutil
import logging
import soundfile as sf
import numpy as np

from app.engines.vad_engine import VADEngine
from app.engines.punc_engine import PuncEngine
from app.pipeline.audio_preprocessor import convert_to_wav, get_audio_duration
from app.config import (
    UPLOADS_DIR,
    AUDIO_CHUNKS_DIR,
    MIN_AUDIO_DURATION,
    MAX_AUDIO_DURATION,
)
import app.config as cfg

logger = logging.getLogger(__name__)


class ASRPipeline:
    def __init__(
        self,
        asr_engine,
        vad_engine: VADEngine,
        punc_engine: PuncEngine | None = None,
    ):
        self.asr = asr_engine
        self.vad = vad_engine
        self.punc = punc_engine

    def run(
        self,
        audio_path: str,
        task_id: str,
        language: str | None = None,
        progress_callback=None,
        cancelled=None,
    ) -> dict:
        """
        执行完整 ASR Pipeline。

        流程：
        0. ffmpeg 格式转换 → 16kHz WAV
        1. VAD 切片 → segments
        2. 超长 segment 二次切分
        3. ASR 识别
        4. 标点恢复（可选）
        5. 合并结果，回算绝对时间戳
        6. 清理临时文件
        """
        wav_path = None
        chunk_dir = os.path.join(AUDIO_CHUNKS_DIR, task_id)

        try:
            os.makedirs(chunk_dir, exist_ok=True)

            # 0. 格式转换
            if progress_callback:
                progress_callback(0.05)
            wav_path = os.path.join(UPLOADS_DIR, f"{task_id}.wav")
            os.makedirs(UPLOADS_DIR, exist_ok=True)
            convert_to_wav(audio_path, wav_path)

            # 检查音频时长
            duration = get_audio_duration(wav_path)
            logger.info(f"[Pipeline] 音频转换完成: 时长={duration:.1f}s, 路径={wav_path}")
            if duration < MIN_AUDIO_DURATION:
                raise ValueError(f"音频过短（{duration:.1f}s），最短要求 {MIN_AUDIO_DURATION}s")
            if duration > MAX_AUDIO_DURATION:
                raise ValueError(
                    f"音频过长（{duration:.0f}s），最大支持 {MAX_AUDIO_DURATION}s，请分段上传"
                )

            # 1. VAD 切片
            if progress_callback:
                progress_callback(0.1)
            vad_segments = self.vad.detect(wav_path)
            logger.info(f"[Pipeline] VAD 检测完成: {len(vad_segments)} 个语音段")

            if not vad_segments:
                logger.info(f"VAD 未检测到语音段: {audio_path}")
                return {
                    "segments": [],
                    "full_text": "",
                    "language": language,
                    "align_enabled": self.asr.align_enabled,
                    "punc_enabled": self.punc is not None,
                }

            # 2. 合并相邻 VAD 段 + 切分写入 chunk 文件
            chunks = self._split_segments_to_chunks(wav_path, vad_segments, chunk_dir)
            total_chunks = len(chunks)
            logger.info(f"[Pipeline] 切片完成: {len(vad_segments)} 个 VAD 段 -> {total_chunks} 个 chunk")

            # 3. 批量 ASR 识别（按 batch 分批推理，每批之间更新进度）
            segments = []
            if cancelled and cancelled():
                logger.info("[Pipeline] 任务已取消，跳过 ASR 识别")
            elif hasattr(self.asr, "batch_transcribe"):
                segments = self._transcribe_batched(
                    chunks, total_chunks, language, cancelled, progress_callback,
                )
            else:
                segments = self._transcribe_sequential(
                    chunks, total_chunks, language, cancelled, progress_callback,
                )

            # 4. 标点恢复（可选）
            if self.punc:
                punc_count = 0
                for seg in segments:
                    if seg["text"] and seg["text"] != "[识别失败]":
                        try:
                            original = seg["text"]
                            seg["text"] = self.punc.restore(seg["text"])
                            if seg["text"] != original:
                                punc_count += 1
                        except Exception as e:
                            logger.warning(f"标点恢复失败，使用原始文本: {e}")
                logger.info(f"[Pipeline] 标点恢复完成: {punc_count}/{len(segments)} 个段落有变化")

            # 5. 合并全文
            full_text = "".join(
                seg["text"] for seg in segments
                if seg["text"] and seg["text"].strip() and seg["text"] != "[识别失败]"
            )

            if progress_callback:
                progress_callback(1.0)

            return {
                "segments": segments,
                "full_text": full_text,
                "language": language,
                "align_enabled": self.asr.align_enabled,
                "punc_enabled": self.punc is not None,
            }

        finally:
            # 6. 清理临时文件
            self._cleanup(audio_path, wav_path, chunk_dir)

    def _transcribe_batched(
        self,
        chunks: list[dict],
        total_chunks: int,
        language: str | None,
        cancelled,
        progress_callback,
    ) -> list[dict]:
        """按 batch 分批调用 ASR 推理，每批之间更新进度和检查取消"""
        batch_size = getattr(self.asr, "batch_size", None) or cfg.ASR_BATCH_SIZE
        segments: list[dict] = []
        processed = 0

        logger.info(
            f"[Pipeline] ASR 批量处理: {total_chunks} 个 chunk, batch_size={batch_size}"
        )

        for batch_start in range(0, total_chunks, batch_size):
            if cancelled and cancelled():
                logger.info(
                    f"[Pipeline] 任务已取消，已完成 {processed}/{total_chunks} 个 chunk"
                )
                break

            batch_end = min(batch_start + batch_size, total_chunks)
            batch_chunks = chunks[batch_start:batch_end]
            batch_paths = [c["path"] for c in batch_chunks]

            logger.info(
                f"[Pipeline] ASR 推理批次 {batch_start // batch_size + 1}: "
                f"chunk {batch_start + 1}-{batch_end}/{total_chunks}"
            )

            try:
                batch_results = self.asr.batch_transcribe(
                    audio_paths=batch_paths,
                    language=language,
                )
            except Exception as e:
                logger.error(f"批次推理失败，回退到逐条处理: {e}")
                fallback = self._transcribe_sequential(
                    chunks[batch_start:], total_chunks, language,
                    cancelled, progress_callback,
                )
                segments.extend(fallback)
                break

            if len(batch_results) != len(batch_chunks):
                logger.error(
                    f"批次结果数不匹配: 期望 {len(batch_chunks)}, 得到 {len(batch_results)}，"
                    "回退到逐条处理"
                )
                fallback = self._transcribe_sequential(
                    chunks[batch_start:], total_chunks, language,
                    cancelled, progress_callback,
                )
                segments.extend(fallback)
                break

            for chunk_info, result in zip(batch_chunks, batch_results):
                text = self._extract_text([result])
                words = self._extract_words([result], chunk_info["offset_sec"])

                segment = {
                    "start": chunk_info["offset_sec"],
                    "end": chunk_info["offset_sec"] + chunk_info["duration_sec"],
                    "text": text,
                }
                if self.asr.align_enabled and words:
                    segment["words"] = words
                if text.strip():
                    segments.append(segment)

            processed = batch_end
            logger.info(
                f"[Pipeline] ASR 进度: {processed}/{total_chunks} 个 chunk 完成"
            )
            if progress_callback:
                progress_callback(0.1 + 0.8 * processed / total_chunks)

        return segments

    def _transcribe_sequential(
        self,
        chunks: list[dict],
        total_chunks: int,
        language: str | None,
        cancelled,
        progress_callback,
    ) -> list[dict]:
        """逐 chunk 串行 ASR 识别（fallback 路径）"""
        segments = []
        for i, chunk_info in enumerate(chunks):
            if cancelled and cancelled():
                logger.info(f"[Pipeline] 任务已取消，已完成 {i}/{total_chunks} 个 chunk")
                break

            logger.info(
                f"[Pipeline] ASR 处理中: chunk {i + 1}/{total_chunks} "
                f"({chunk_info['offset_sec']:.1f}s ~ "
                f"{chunk_info['offset_sec'] + chunk_info['duration_sec']:.1f}s)"
            )
            try:
                results = self.asr.transcribe(
                    audio_path=chunk_info["path"],
                    language=language,
                )
                text = self._extract_text(results)
                words = self._extract_words(results, chunk_info["offset_sec"])

                segment = {
                    "start": chunk_info["offset_sec"],
                    "end": chunk_info["offset_sec"] + chunk_info["duration_sec"],
                    "text": text,
                }
                if self.asr.align_enabled and words:
                    segment["words"] = words

                if text.strip():
                    segments.append(segment)
            except Exception as e:
                logger.error(f"chunk {i} 识别失败: {e}")
                segments.append({
                    "start": chunk_info["offset_sec"],
                    "end": chunk_info["offset_sec"] + chunk_info["duration_sec"],
                    "text": "[识别失败]",
                })

            if progress_callback:
                progress_callback(0.1 + 0.8 * (i + 1) / total_chunks)
        return segments

    def _merge_vad_segments(
        self,
        vad_segments: list[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        """
        贪心合并相邻 VAD 段：从第一段开始，持续追加后续段，
        直到合并后总跨度（首段 start 到末段 end）超过 cfg.MAX_SEGMENT_DURATION，
        则切出一组，开始新的一组。保留段间静音以维持时间戳准确性。
        """
        if not vad_segments:
            return []

        max_span_ms = int(cfg.MAX_SEGMENT_DURATION * 1000)
        merged = []
        group_start, group_end = vad_segments[0]

        for start_ms, end_ms in vad_segments[1:]:
            # 如果追加后总跨度仍在阈值内，合并
            if end_ms - group_start <= max_span_ms:
                group_end = end_ms
            else:
                merged.append((group_start, group_end))
                group_start, group_end = start_ms, end_ms

        merged.append((group_start, group_end))
        return merged

    def _split_segments_to_chunks(
        self,
        wav_path: str,
        vad_segments: list[tuple[int, int]],
        chunk_dir: str,
    ) -> list[dict]:
        """
        合并相邻 VAD 段后切分音频，超长段二次切分。

        返回:
            [{"path": str, "offset_sec": float, "duration_sec": float}, ...]
        """
        data, sr = sf.read(wav_path)

        # 先合并碎片段
        merged = self._merge_vad_segments(vad_segments)
        logger.info(
            f"VAD 段合并: {len(vad_segments)} -> {len(merged)} "
            f"(阈值={cfg.MAX_SEGMENT_DURATION}s)"
        )

        chunks = []
        idx = 0

        for start_ms, end_ms in merged:
            start_sample = int(start_ms / 1000 * sr)
            end_sample = int(end_ms / 1000 * sr)
            segment_data = data[start_sample:end_sample]
            segment_duration = len(segment_data) / sr

            if segment_duration <= cfg.MAX_SEGMENT_DURATION:
                chunk_path = os.path.join(chunk_dir, f"chunk_{idx:04d}.wav")
                sf.write(chunk_path, segment_data, sr)
                chunks.append({
                    "path": chunk_path,
                    "offset_sec": start_ms / 1000,
                    "duration_sec": segment_duration,
                })
                idx += 1
            else:
                # 单段超长（理论上合并后不会出现，但作为兜底）
                sub_samples = int(cfg.MAX_SEGMENT_DURATION * sr)
                offset = 0
                while offset < len(segment_data):
                    end = min(offset + sub_samples, len(segment_data))
                    sub_data = segment_data[offset:end]
                    chunk_path = os.path.join(chunk_dir, f"chunk_{idx:04d}.wav")
                    sf.write(chunk_path, sub_data, sr)
                    chunk_offset_sec = start_ms / 1000 + offset / sr
                    chunks.append({
                        "path": chunk_path,
                        "offset_sec": chunk_offset_sec,
                        "duration_sec": len(sub_data) / sr,
                    })
                    offset = end
                    idx += 1

        logger.info(f"切分完成: {len(merged)} 个合并段 -> {len(chunks)} 个 chunk")
        return chunks

    def _extract_text(self, results) -> str:
        """从 qwen_asr transcribe 结果中提取纯文本"""
        if not results:
            return ""
        if isinstance(results, str):
            return results
        if isinstance(results, list):
            texts = []
            for item in results:
                if hasattr(item, "text"):
                    texts.append(item.text)
                elif isinstance(item, dict):
                    texts.append(item.get("text", ""))
                elif isinstance(item, str):
                    texts.append(item)
            return "".join(texts)
        if hasattr(results, "text"):
            return results.text
        return str(results)

    def _extract_words(self, results, offset_sec: float) -> list[dict] | None:
        """从 qwen_asr 结果中提取单词级时间戳（带偏移修正）"""
        if not results or not isinstance(results, list):
            return None

        words = []
        for item in results:
            # ASRTranscription.time_stamps -> ForcedAlignResult.items -> [ForcedAlignItem]
            ts = getattr(item, "time_stamps", None)
            if ts is None:
                continue
            align_items = getattr(ts, "items", [])
            for w in align_items:
                words.append({
                    "text": w.text,
                    "start": round(w.start_time + offset_sec, 3),
                    "end": round(w.end_time + offset_sec, 3),
                })
        return words if words else None

    def _cleanup(self, original_path: str, wav_path: str | None, chunk_dir: str):
        """清理临时文件"""
        try:
            if original_path and os.path.exists(original_path):
                os.remove(original_path)
        except OSError as e:
            logger.warning(f"清理原始文件失败: {e}")

        try:
            if wav_path and os.path.exists(wav_path):
                os.remove(wav_path)
        except OSError as e:
            logger.warning(f"清理转换文件失败: {e}")

        try:
            if os.path.exists(chunk_dir):
                shutil.rmtree(chunk_dir, ignore_errors=True)
        except OSError as e:
            logger.warning(f"清理 chunk 目录失败: {e}")
