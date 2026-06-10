"""app/api/compat/fetch.py 测试（SSRF 防护 + 真实本地下载）。

SSRF 拒绝路径不触网（scheme/私网 IP/坏扩展名直接拒）；成功/超大/404 用本地 HTTP server
（127.0.0.1，需 allow_private=True 放行回环）验证真实 httpx 流式下载。
"""
import asyncio
import functools
import http.server
import os
import threading

import pytest

from app.api.compat import fetch
from app.api.compat.fetch import FetchError, fetch_to_local


@pytest.fixture
def local_server(tmp_path, monkeypatch):
    """起一个 serve tmp_path 的本地 HTTP server；UPLOADS_DIR 重定向到 tmp。"""
    # 绕过测试环境可能存在的 HTTP 代理（否则 127.0.0.1 请求被代理拦截）
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.setattr(fetch, "UPLOADS_DIR", str(tmp_path / "dl"))
    handler = functools.partial(http.server.SimpleHTTPRequestHandler,
                                directory=str(tmp_path))
    srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port, tmp_path
    srv.shutdown()


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── SSRF 拒绝（不触网）───

def test_reject_non_http_scheme():
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("ftp://example.com/a.wav", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchForbidden"


def test_reject_loopback():
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("http://127.0.0.1/a.wav", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchForbidden"


def test_reject_private_ip():
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("http://10.0.0.5/a.wav", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchForbidden"


def test_reject_link_local():
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("http://169.254.1.1/a.wav", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchForbidden"


def test_reject_bad_extension_public_ip():
    # 公网 IP 字面量过私网校验，扩展名 .txt 被拒（不触网）
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("http://1.2.3.4/a.txt", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchBadFormat"


def test_reject_missing_hostname():
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local("http:///a.wav", max_mb=10, timeout_s=5))
    assert ei.value.code == "FetchForbidden"


# ─── 真实下载（本地 server，allow_private=True）───

def test_download_success(local_server):
    port, tmp_path = local_server
    (tmp_path / "a.wav").write_bytes(b"RIFFxxxxWAVE_payload")
    path = _run(fetch_to_local(f"http://127.0.0.1:{port}/a.wav",
                               max_mb=10, timeout_s=5, allow_private=True))
    assert os.path.exists(path) and path.endswith(".wav")
    assert open(path, "rb").read() == b"RIFFxxxxWAVE_payload"


def test_download_too_large(local_server):
    port, tmp_path = local_server
    (tmp_path / "big.wav").write_bytes(b"x" * (2 * 1024 * 1024))   # 2MB
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local(f"http://127.0.0.1:{port}/big.wav",
                            max_mb=1, timeout_s=5, allow_private=True))
    assert ei.value.code == "FetchTooLarge"
    # 超限文件已删除
    assert not any(f.startswith("dsdl_") for f in os.listdir(tmp_path / "dl"))


def test_download_404(local_server):
    port, _ = local_server
    with pytest.raises(FetchError) as ei:
        _run(fetch_to_local(f"http://127.0.0.1:{port}/missing.wav",
                            max_mb=10, timeout_s=5, allow_private=True))
    assert ei.value.code == "FetchHttpError"


def test_allow_private_flag_permits_loopback(local_server):
    port, tmp_path = local_server
    (tmp_path / "ok.wav").write_bytes(b"data")
    # allow_private=True 时回环放行（默认 False 会拒）
    path = _run(fetch_to_local(f"http://127.0.0.1:{port}/ok.wav",
                               max_mb=10, timeout_s=5, allow_private=True))
    assert os.path.exists(path)
