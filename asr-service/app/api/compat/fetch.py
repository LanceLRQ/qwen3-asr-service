"""DashScope file_urls 服务端下载（SSRF / 大小 / 超时防护）。

DashScope 录音文件识别传 URL（非上传），服务端需把 URL 拉到本地再入队。下载任意外部
URL 有 SSRF 风险，本模块强制：仅 http/https、解析 host→IP 校验私网、流式限大小、整体
超时、不跟随重定向、扩展名白名单。
"""
import ipaddress
import logging
import os
import socket
import uuid
from urllib.parse import urlparse

import httpx

from app.api.routes import ALLOWED_EXTENSIONS
from app.config import UPLOADS_DIR

logger = logging.getLogger(__name__)

_CHUNK = 256 * 1024


class FetchError(Exception):
    """下载失败：携带 code/message，供 DashScope 子任务标记 FAILED。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _is_blocked_ip(ip: str) -> bool:
    """私网/回环/链路本地/保留/多播/未指定 → 阻断。"""
    addr = ipaddress.ip_address(ip)
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)


def _resolve_and_check(host: str, allow_private: bool) -> None:
    """解析 host 的所有 IP；allow_private=False 时任一被阻断地址即拒。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise FetchError("FetchDNSError", f"无法解析主机: {host}") from e
    ips = {info[4][0] for info in infos}
    if not ips:
        raise FetchError("FetchDNSError", f"无法解析主机: {host}")
    if not allow_private:
        for ip in ips:
            if _is_blocked_ip(ip):
                raise FetchError("FetchForbidden", f"拒绝下载私网/回环地址: {host} → {ip}")


def _safe_remove(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def fetch_to_local(url: str, *, max_mb: int, timeout_s: int,
                         allow_private: bool = False) -> str:
    """下载 url 到 UPLOADS_DIR，返回本地路径。失败抛 FetchError。

    防护：① 仅 http/https；② 解析 host→IP 校验私网（allow_private=False 时拒）；
         ③ 流式写盘，累计超 max_mb 立即中断删除；④ 整体 timeout_s 超时；
         ⑤ 不跟随重定向（3xx 视为拒绝，避免绕过 SSRF）；
         ⑥ 扩展名白名单复用 ALLOWED_EXTENSIONS。
    注：DNS rebinding（校验 IP 与连接 IP 不一致）窗口未完全闭合，初版已知项。
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError("FetchForbidden", f"仅支持 http/https，收到: {parsed.scheme or '(空)'}")
    if not parsed.hostname:
        raise FetchError("FetchForbidden", "URL 缺少主机名")

    _resolve_and_check(parsed.hostname, allow_private)

    ext = os.path.splitext(parsed.path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise FetchError("FetchBadFormat", f"不支持的音频格式 '{ext or '(无扩展名)'}'")

    os.makedirs(UPLOADS_DIR, exist_ok=True)
    save_path = os.path.join(UPLOADS_DIR, f"dsdl_{uuid.uuid4().hex}{ext}")
    max_bytes = max_mb * 1024 * 1024
    total = 0
    try:
        # trust_env=False：禁用环境 HTTP(S)_PROXY，否则下载经代理转发会绕过上面的 IP 校验
        async with httpx.AsyncClient(
                follow_redirects=False, timeout=httpx.Timeout(timeout_s),
                trust_env=False) as client:
            async with client.stream("GET", url) as resp:
                if resp.is_redirect:
                    raise FetchError("FetchForbidden", "拒绝跟随重定向")
                if resp.status_code != 200:
                    raise FetchError("FetchHttpError", f"下载失败 HTTP {resp.status_code}")
                with open(save_path, "wb") as f:
                    async for chunk in resp.aiter_bytes(_CHUNK):
                        total += len(chunk)
                        if total > max_bytes:
                            raise FetchError("FetchTooLarge", f"文件超过 {max_mb}MB 上限")
                        f.write(chunk)
    except httpx.TimeoutException as e:
        _safe_remove(save_path)
        raise FetchError("FetchTimeout", f"下载超时（>{timeout_s}s）") from e
    except httpx.HTTPError as e:
        _safe_remove(save_path)
        raise FetchError("FetchNetworkError", f"下载网络错误: {e}") from e
    except FetchError:
        _safe_remove(save_path)
        raise
    return save_path
