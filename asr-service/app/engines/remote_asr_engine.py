"""S2-01 Qwen HTTP multipart 单块离线适配器。"""
import math
from pathlib import Path

import httpx

from app.engines.asr_result import ASRResult, BackendCapabilities, from_qwen_text
from app.utils.language import to_iso_language


class RemoteASRError(RuntimeError):
    """可向任务调用方公开的上游错误，不含服务地址、密钥或响应正文。"""


class RemoteASREngine:
    capabilities = BackendCapabilities()
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

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with httpx.Client(headers=self._headers, timeout=self.timeout, trust_env=False) as client:
                response = client.request(method, self.base_url + path, **kwargs)
                response.raise_for_status()
        except httpx.TimeoutException:
            raise RemoteASRError("远端 ASR 请求超时") from None
        except httpx.HTTPStatusError as exc:
            raise RemoteASRError(f"远端 ASR 返回 HTTP {exc.response.status_code}") from None
        except httpx.RequestError:
            raise RemoteASRError("远端 ASR 连接失败") from None

        try:
            payload = response.json()
        except ValueError:
            raise RemoteASRError("远端 ASR 响应格式错误") from None
        if not isinstance(payload, dict):
            raise RemoteASRError("远端 ASR 响应格式错误")
        return payload

    def load(self):
        models = self._request("GET", "/models").get("data")
        if not isinstance(models, list):
            raise RemoteASRError("远端 ASR 模型清单格式错误")
        if not any(isinstance(model, dict) and model.get("id") == self.model for model in models):
            raise ValueError("上游未提供配置的 remote_model")

    def transcribe(self, audio_path: str, language: str | None = None) -> ASRResult:
        data = {"model": self.model}
        hint = to_iso_language(language)
        if hint:
            data["language"] = hint
        with open(audio_path, "rb") as audio:
            payload = self._request("POST", "/audio/transcriptions", data=data,
                                    files={"file": (Path(audio_path).name, audio, "audio/wav")})
        text = payload.get("text")
        if not isinstance(text, str):
            raise RemoteASRError("远端 ASR 转写响应格式错误")
        return from_qwen_text(text, audio_path)
