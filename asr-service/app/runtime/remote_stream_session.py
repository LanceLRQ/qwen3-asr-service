"""Qwen streaming demo 有状态 HTTP 客户端；每句占用一个上游 session_id。"""
import asyncio
import logging
import math

import httpx

from app.engines.remote_asr_engine import RemoteASRError
from app.runtime.vllm_stream_session import EnergyEndpointer
from app.utils.audio_resampler import TARGET_SR, pcm_bytes_to_array, resample_to_16k

logger = logging.getLogger(__name__)


class RemoteStreamBackend:
    mode = "remote"
    backend = "remote-qwen-streaming"
    capabilities = {
        "partial_results": True, "word_timestamps": False, "languages_auto": True,
        "speaker_labels": False, "output_toggles": False,
    }

    def __init__(self, base_url, timeout, *, max_sessions, max_utterance_sec):
        url = httpx.URL(base_url)
        if url.scheme not in ("http", "https") or not url.host:
            raise ValueError("远端流式地址必须是含主机名的 HTTP(S) 地址")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("远端流式超时必须为有限正数")
        if max_sessions < 1 or not math.isfinite(max_utterance_sec) or max_utterance_sec * TARGET_SR < 1:
            raise ValueError("会话数和单句样本数必须为正")
        self._base_url = base_url.rstrip("/") + "/"
        self._timeout = timeout
        self._max_samples = int(max_utterance_sec * TARGET_SR)
        self._active = False

    async def acquire(self):
        # WS 路由在同一事件循环调用；demo 的模型实例只能处理一个活动会话。
        if self._active:
            return False
        self._active = True
        return True

    def create_session(self, sid):
        return RemoteStreamSession(sid, self._base_url, self._timeout, self._max_samples)

    def release(self, session):
        self._active = False

    def shutdown(self):
        # 每个 HTTP 客户端由对应 WS 的 finally 调用 session.aclose() 关闭。
        pass


class RemoteStreamSession:
    def __init__(self, sid, base_url, timeout, max_samples):
        self.sid = sid
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, trust_env=False)
        self._timeout = timeout
        self._max_samples = max_samples
        self._endpoint = EnergyEndpointer()
        self._upstream = None
        self._pending = None
        self._failed = False
        self._closed = False
        self._busy = False
        self.configure({})

    def configure(self, cfg):
        if self._failed or self._closed:
            raise ValueError("当前远端会话无法重新配置")
        try:
            rate = int(cfg.get("audio_fs", TARGET_SR))
        except (TypeError, ValueError, OverflowError):
            raise ValueError("audio_fs 必须是有效采样率") from None
        if not 8000 <= rate <= 96000:
            raise ValueError("audio_fs 必须在 [8000, 96000] 范围内")
        warnings = [key for key, value in cfg.items()
                    if key not in ("type", "audio_fs", "wav_name") and value is not None]
        if self._busy or self._upstream is not None:
            if rate != self.audio_fs:
                raise ValueError("活动句期间不能更改采样率")
            return warnings
        self.audio_fs = rate
        self.seg_id = 0
        self._total = self._start = 0
        self._partial = ""
        self._endpoint.reset()
        return warnings

    async def _post(self, action, **kwargs):
        params = {"session_id": self._upstream} if action != "start" else {}
        if action == "finish":
            self._upstream = None  # 响应丢失时上游可能已经执行，不能再次 finish。
        try:
            response = await asyncio.wait_for(
                self._client.post("api/" + action, params=params, **kwargs), self._timeout
            )
            response.raise_for_status()
            payload = response.json()
            key = "session_id" if action == "start" else "text"
            if not isinstance(payload, dict) or not isinstance(payload.get(key), str):
                raise ValueError
            if action == "start":
                if not payload[key]:
                    raise ValueError
                self._upstream = payload[key]
            return payload[key]
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
            self._failed = True
            raise RemoteASRError(f"远端流式 {action} 请求失败") from None

    async def _request(self, action, **kwargs):
        # 断连可能正逢 start 已创建状态但响应尚未到达。保留在途请求给 aclose 收尾。
        self._pending = asyncio.create_task(self._post(action, **kwargs))
        try:
            return await asyncio.shield(self._pending)
        except asyncio.CancelledError:
            self._failed = True
            raise
        finally:
            if self._pending.done():
                self._pending = None

    async def _final(self):
        text = await self._request("finish")
        event = dict(type="final", seg_id=self.seg_id, text=text,
                     start=self._start * 1000 // TARGET_SR, end=self._total * 1000 // TARGET_SR)
        self.seg_id += 1
        self._partial = ""
        self._endpoint.reset()
        return event

    async def feed_audio(self, pcm):
        if self._failed or self._closed:
            raise RemoteASRError("远端流式会话已终止")
        self._busy = True
        try:
            samples = pcm_bytes_to_array(pcm)
            if self.audio_fs != TARGET_SR and samples.size:
                samples = await asyncio.to_thread(resample_to_16k, samples, self.audio_fs)
            offset = 0
            while offset < samples.size:
                used = self._total - self._start if self._upstream is not None else 0
                frame = samples[offset:offset + self._max_samples - used]
                events = self._endpoint.process(frame)
                if any(event["type"] == "start" for event in events):
                    self._start = self._total
                    await self._request("start")
                self._total += frame.size
                offset += frame.size
                if self._upstream is None:
                    continue
                text = await self._request("chunk", content=frame.astype("<f4", copy=False).tobytes(),
                                           headers={"Content-Type": "application/octet-stream"})
                if text != self._partial:
                    self._partial = text
                    yield dict(type="partial", seg_id=self.seg_id, text=text)
                if (self._total - self._start >= self._max_samples
                        or any(event["type"] == "end" for event in events)):
                    yield await self._final()
        finally:
            self._busy = False

    async def flush(self):
        if self._failed or self._closed:
            raise RemoteASRError("远端流式会话已终止")
        if self._upstream is not None:
            self._busy = True
            try:
                yield await self._final()
            finally:
                self._busy = False

    async def aclose(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._pending is not None:
                try:
                    await self._pending
                except RemoteASRError:
                    pass  # 已知 ID 仍需尽力 finish；未知 ID 无法回收。
            if self._upstream is not None:
                await self._post("finish")
        except RemoteASRError:
            logger.warning("远端流式会话清理失败 sid=%s", self.sid)
        finally:
            await self._client.aclose()
