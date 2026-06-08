"""声纹库编排层：登记/识别业务流程（格式转换 → VAD → 质量门槛 → embedding → 入库/比对）。

全部方法【同步】，路由层经 asyncio.to_thread 下沉调用（对齐 routes.py 体例）。
错误约定：
- 质量门槛/输入问题 → ValueError（中文信息，路由转 400 直接透传 detail）；
- 库故障 → SpeakerStoreError 原样上抛（路由转 500）；
- 转写联动入口（map_clusters / map_and_enroll_clusters）【永不抛错】——
  识别/登记失败一律优雅退回匿名（speaker_id/name=None），不影响转写结果。
"""
import logging
import os
import shutil
import uuid

import numpy as np
import soundfile as sf

import app.config as cfg
from app.engines.speaker_embedding_engine import make_windows
from app.pipeline.audio_preprocessor import convert_to_wav
from app.runtime.speaker_cluster import cluster_offline
from app.runtime.speaker_store import SpeakerStore, SpeakerStoreError

logger = logging.getLogger(__name__)

QUALITY_HINT_MIN_TEMPLATES = 3   # 建议模板数（不足仅提示，不阻断）


class SpeakerService:

    def __init__(self, store: SpeakerStore, embed_engine, vad_engine):
        self.store = store
        self._engine = embed_engine
        self._vad = vad_engine

    # ─── 内部：单文件 → 模板向量 ───

    def _embed_file(self, file_path: str, *, keep_wav: bool = False,
                    purpose: str = "登记") -> tuple[np.ndarray, float, str | None]:
        """转换 → VAD → 时长门槛 → 单人校验 → 窗均值模板。

        返回 (L2 归一模板向量, 有效语音秒数, 留存的 wav 路径或 None)。
        keep_wav=False 或失败路径上一律清理临时 wav。
        """
        os.makedirs(cfg.UPLOADS_DIR, exist_ok=True)
        wav_path = os.path.join(cfg.UPLOADS_DIR, f"spk_{uuid.uuid4().hex}.wav")
        ok = False
        try:
            convert_to_wav(file_path, wav_path)
            segments = self._vad.detect(wav_path)
            dur = sum(e - s for s, e in segments) / 1000.0
            if dur < cfg.SPEAKER_ENROLL_MIN_SEC:
                raise ValueError(
                    f"{purpose}样本有效语音不足（{dur:.1f}s < {cfg.SPEAKER_ENROLL_MIN_SEC}s），"
                    "请提供更长的清晰人声"
                )
            wav, _sr = sf.read(wav_path, dtype="float32")
            windows = [w for s, e in segments
                       for w in make_windows(s / 1000.0, e / 1000.0)]
            embs = self._engine.embed_windows(wav, windows)
            labels = cluster_offline(embs, max_speakers=cfg.SPEAKER_MAX)
            if len(np.unique(labels)) > 1:
                raise ValueError(f"{purpose}样本含多个说话人，请提供单人清晰录音")
            template = embs.mean(axis=0)
            norm = float(np.linalg.norm(template))
            if norm > 0:
                template = template / norm
            ok = True
            return template.astype(np.float32), dur, (wav_path if keep_wav else None)
        finally:
            if (not ok or not keep_wav) and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except OSError as e:
                    logger.warning(f"声纹临时文件清理失败: {e}")

    def _audio_dir(self, speaker_id: str) -> str:
        return os.path.join(cfg.BASE_DIR, "data", "speaker_audio", speaker_id)

    # ─── 管理路径（路由经 to_thread 调用）───

    def enroll(self, name: str, note: str | None, file_paths: list[str],
               consent: bool) -> dict:
        """登记新说话人。全部样本过质量门槛后单事务入库。"""
        if consent is not True:
            raise ValueError("登记必须携带 consent=true（确认已获得数据主体同意）")
        if not file_paths:
            raise ValueError("至少需要 1 个音频样本")
        if len(file_paths) > SpeakerStore.MAX_TEMPLATES:
            raise ValueError(f"样本数超过模板上限 {SpeakerStore.MAX_TEMPLATES}")

        keep = cfg.SPEAKER_STORE_AUDIO
        vectors, durs, kept_wavs = [], [], []
        try:
            for p in file_paths:
                vec, dur, kept = self._embed_file(p, keep_wav=keep)
                vectors.append(vec)
                durs.append(dur)
                if kept:
                    kept_wavs.append(kept)
            speaker_id = self.store.enroll_speaker(
                name, note, vectors, durs, consent=True, source="manual")
        except Exception:
            for w in kept_wavs:
                if os.path.exists(w):
                    os.remove(w)
            raise
        if kept_wavs:
            audio_dir = self._audio_dir(speaker_id)
            os.makedirs(audio_dir, exist_ok=True)
            for i, w in enumerate(kept_wavs):
                shutil.move(w, os.path.join(audio_dir, f"{i:02d}.wav"))
        resp = {"speaker_id": speaker_id, "name": name, "templates": len(vectors)}
        if len(vectors) < QUALITY_HINT_MIN_TEMPLATES:
            resp["quality_hint"] = (
                f"建议提供 ≥{QUALITY_HINT_MIN_TEMPLATES} 个不同场景的样本以提升识别稳健性"
            )
        return resp

    def add_template(self, speaker_id: str, file_path: str) -> dict:
        """为既有说话人追加模板（质心自动重算）。"""
        vec, dur, kept = self._embed_file(file_path, keep_wav=cfg.SPEAKER_STORE_AUDIO,
                                          purpose="追加")
        try:
            self.store.add_template(speaker_id, vec, dur)
        except Exception:
            if kept and os.path.exists(kept):
                os.remove(kept)
            raise
        if kept:
            audio_dir = self._audio_dir(speaker_id)
            os.makedirs(audio_dir, exist_ok=True)
            shutil.move(kept, os.path.join(audio_dir, f"t_{uuid.uuid4().hex[:8]}.wav"))
        info = self.store.get_speaker(speaker_id)
        return {"speaker_id": speaker_id, "templates": len(info["templates"]) if info else 0}

    def delete_speaker(self, speaker_id: str) -> None:
        """硬删除（库 + 留存音频目录同步清理——被遗忘权完整覆盖）。"""
        self.store.delete_speaker(speaker_id)
        audio_dir = self._audio_dir(speaker_id)
        if os.path.isdir(audio_dir):
            shutil.rmtree(audio_dir, ignore_errors=True)

    def identify_file(self, file_path: str) -> dict:
        """单文件 1:N 识别。"""
        vec, dur, _ = self._embed_file(file_path, keep_wav=False, purpose="识别")
        hit = self.store.identify(vec, threshold=cfg.SPEAKER_ID_THRESHOLD,
                                  margin=cfg.SPEAKER_ID_MARGIN)
        self.store.audit("identify", hit["speaker_id"] if hit else None,
                         {"matched": hit is not None,
                          "score": hit["score"] if hit else None, "via": "file"})
        if hit is None:
            return {"matched": False}
        return {"matched": True, **hit}

    # ─── 转写联动路径（永不抛错，失败退回匿名）───

    @staticmethod
    def _anon(label: str) -> dict:
        return {"label": label, "speaker_id": None, "name": None, "score": None}

    def map_clusters(self, clusters: list[dict], *,
                     id_threshold: float | None = None,
                     id_margin: float | None = None) -> list[dict]:
        """实时联动入口（仅识别，不自动登记）。

        clusters = [{"label", "centroid"}]；返回每簇 {"label","speaker_id","name","score"}，
        未命中/任何异常 → 该簇 speaker_id/name=None（识别失败永远优雅退回匿名）。
        id_threshold/id_margin 缺省=服务端 cfg（支持按会话覆盖）。
        """
        thr = cfg.SPEAKER_ID_THRESHOLD if id_threshold is None else id_threshold
        mgn = cfg.SPEAKER_ID_MARGIN if id_margin is None else id_margin
        out = []
        for c in clusters:
            label = c.get("label", "?")
            try:
                hit = self.store.identify(c["centroid"], threshold=thr, margin=mgn)
                self.store.audit("identify", hit["speaker_id"] if hit else None,
                                 {"matched": hit is not None,
                                  "score": hit["score"] if hit else None,
                                  "label": label, "via": "stream"})
                out.append({"label": label, **hit} if hit else self._anon(label))
            except Exception as e:
                logger.warning(f"簇识别失败，退回匿名: {e}")
                out.append(self._anon(label))
        return out

    def map_and_enroll_clusters(self, clusters: list[dict], *,
                                id_threshold: float | None = None,
                                id_margin: float | None = None) -> list[dict]:
        """离线联动入口（识别 + 自动登记）。

        clusters = [{"label","centroid","dur_sec"}]（S3 衔接面）。未命中且开启
        speaker_auto_enroll 且簇语音总时长过门槛 → 以「说话人_NN」占位名登记
        （source='auto'；开启自动登记 = 部署方声明已获数据主体同意，consent 同责）。
        已命中的说话人不自动追加模板（防投毒）。登记失败退回匿名，不影响转写。
        id_threshold/id_margin 缺省=服务端 cfg（支持按请求覆盖）。
        """
        thr = cfg.SPEAKER_ID_THRESHOLD if id_threshold is None else id_threshold
        mgn = cfg.SPEAKER_ID_MARGIN if id_margin is None else id_margin
        out = []
        for c in clusters:
            label = c.get("label", "?")
            try:
                hit = self.store.identify(c["centroid"], threshold=thr, margin=mgn)
                self.store.audit("identify", hit["speaker_id"] if hit else None,
                                 {"matched": hit is not None,
                                  "score": hit["score"] if hit else None,
                                  "label": label, "via": "offline"})
                if hit:
                    out.append({"label": label, **hit})
                    continue
                dur = float(c.get("dur_sec") or 0.0)
                if cfg.SPEAKER_AUTO_ENROLL and dur >= cfg.SPEAKER_AUTO_ENROLL_MIN_SEC:
                    name = self.store.alloc_auto_name()
                    sid = self.store.enroll_speaker(
                        name, None, [np.asarray(c["centroid"], dtype=np.float32)],
                        [dur], consent=True, source="auto")
                    out.append({"label": label, "speaker_id": sid, "name": name,
                                "score": None, "auto_enrolled": True})
                else:
                    out.append(self._anon(label))
            except Exception as e:
                logger.warning(f"簇识别/自动登记失败，退回匿名: {e}")
                out.append(self._anon(label))
        return out
