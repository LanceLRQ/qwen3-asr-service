"""流式录音文件管理。"""
from __future__ import annotations

import os
import re
import time
import uuid
import wave
from pathlib import Path

_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")


def _safe_wav_name(name: str | None) -> str:
    base = Path(name or "stream").name.strip() or "stream"
    base = _SAFE_NAME_RE.sub("_", base).strip(" ._") or "stream"
    stem = Path(base).stem or "stream"
    return f"{stem[:80]}.wav"


class StreamRecorder:
    """单个流式会话的 WAV 写入器。"""

    def __init__(self, path: Path, recording_id: str, wav_name: str, sample_rate: int):
        self.path = path
        self.info = {"recording_id": recording_id, "wav_name": wav_name}
        self._closed = False
        self._wave = wave.open(str(path), "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)
        self._wave.setframerate(int(sample_rate))

    def write(self, pcm_bytes: bytes) -> None:
        if self._closed or not pcm_bytes:
            return
        self._wave.writeframes(pcm_bytes)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._wave.close()


class StreamRecordingManager:
    """流式录音资源 owner。retention_hours <= 0 表示永不自动清理。"""

    def __init__(self, *, enabled: bool, directory: str, retention_hours: int = 72):
        self.enabled = bool(enabled)
        self.directory = Path(directory)
        self.retention_hours = int(retention_hours)

    def start(self, *, wav_name: str | None, sample_rate: int) -> StreamRecorder | None:
        if not self.enabled:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        recording_id = uuid.uuid4().hex
        safe_name = _safe_wav_name(wav_name)
        path = self.directory / f"{recording_id}_{safe_name}"
        return StreamRecorder(path, recording_id, safe_name, sample_rate)

    def path_for(self, recording_id: str) -> Path | None:
        if not _ID_RE.fullmatch(recording_id or ""):
            return None
        for path in self.directory.glob(f"{recording_id}_*.wav"):
            if path.is_file():
                return path
        return None

    def filename_for(self, recording_id: str) -> str:
        path = self.path_for(recording_id)
        if path is None:
            return f"{recording_id}.wav"
        return path.name.split("_", 1)[1]

    def delete(self, recording_id: str) -> bool:
        path = self.path_for(recording_id)
        if path is None:
            return False
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def cleanup_expired(self) -> int:
        if self.retention_hours <= 0 or not self.directory.exists():
            return 0
        cutoff = time.time() - self.retention_hours * 3600
        removed = 0
        for path in self.directory.glob("*.wav"):
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                continue
        return removed
