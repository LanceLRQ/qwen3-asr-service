"""S2-01 Qwen HTTP multipart 单块离线适配器。"""
import math
from pathlib import Path
from types import SimpleNamespace

import httpx

from app.utils.language import to_iso_language


class RemoteASREngine:
    is_remote = True
    align_enabled = False

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout: float):
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("remote_timeout 必须是有限正数")
        url = httpx.URL(base_url or "")
        if url.scheme not in ("https", "http") or not url.host:
            raise ValueError("remote_base_url 必须是含主机名的 HTTP(S) 地址")
        if not model.strip():
            raise ValueError("remote_model 不能为空")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.timeout = timeout

    def load(self):
        with httpx.Client(headers=self._headers, timeout=self.timeout, trust_env=False) as client:
            response = client.get(f"{self.base_url}/models")
            response.raise_for_status()
            if not any(model["id"] == self.model for model in response.json()["data"]):
                raise ValueError("上游未提供配置的 remote_model")

    def transcribe(self, audio_path: str, language: str | None = None) -> list:
        data = {"model": self.model}
        hint = to_iso_language(language)
        if hint:
            data["language"] = hint
        with open(audio_path, "rb") as audio, httpx.Client(
            headers=self._headers, timeout=self.timeout, trust_env=False
        ) as client:
            response = client.post(f"{self.base_url}/audio/transcriptions", data=data,
                                   files={"file": (Path(audio_path).name, audio, "audio/wav")})
            response.raise_for_status()
            text = response.json()["text"]
        prefix, marker, body = text.partition("<asr_text>")
        detected = None
        if marker:
            detected = prefix.removeprefix("language ").strip() or None
            text = body
        return [SimpleNamespace(text=text.strip(), language=detected, time_stamps=None)]
