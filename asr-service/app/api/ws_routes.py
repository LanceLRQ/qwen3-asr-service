"""实时转写统一端点 WS /v2/asr/stream（后端无关）。

两种 serve-mode 共用此端点；启动时注入"活动后端"（路线 B / 路线 A），
二者实现同一 StreamBackend 接口。连接即下发 session.created 声明协议/后端/能力。
鉴权复用 cfg.API_KEY + hmac.compare_digest（与 HTTP 一致）。
"""
import hmac
import json
import logging
from uuid import uuid4

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

import app.config as cfg
from app.api.ws_schemas import SessionCreated, SessionClosed, ErrorMsg

logger = logging.getLogger(__name__)

ws_router_stream = APIRouter(prefix="/v2/asr")

# 活动后端，由 main.py 启动时注入
_backend = None


def init_ws_stream(backend):
    """注入活动后端（VadOfflineBackend / VllmStreamBackend）。"""
    global _backend
    _backend = backend


async def verify_ws_token(ws: WebSocket) -> bool:
    """WS 鉴权：未配置 API_KEY 时放行；否则校验 query `token` 或 Authorization Bearer。"""
    if not cfg.API_KEY:
        return True
    token = ws.query_params.get("token")
    if token is None:
        auth = ws.headers.get("authorization")
        if auth and auth.lower().startswith("bearer "):
            token = auth[7:]
    return token is not None and hmac.compare_digest(token, cfg.API_KEY)


@ws_router_stream.websocket("/stream")
async def stream(ws: WebSocket):
    # 鉴权（在 accept 前，失败以 1008 关闭）
    if not await verify_ws_token(ws):
        await ws.close(code=1008)
        return
    if _backend is None:
        await ws.close(code=1011)      # 服务未就绪（未注入后端）
        return
    # 并发准入（超额 1013）
    if not await _backend.acquire():
        await ws.close(code=1013)
        return

    await ws.accept()
    session = None
    recv_bytes = 0
    sent_msgs = 0
    try:
        # 连接即声明协议/后端/能力
        await ws.send_json(SessionCreated(
            mode=_backend.mode,
            backend=_backend.backend,
            capabilities=_backend.capabilities,
        ).model_dump())

        sid = uuid4().hex
        session = _backend.create_session(sid)
        logger.info(f"[stream] WS accepted sid={sid[:8]}")
        start_msg = await ws.receive_json()             # 首条 {type:"start", ...}
        logger.info(f"[stream] 收到 start: {start_msg}")
        session.configure(start_msg)

        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                logger.info(f"[stream] 客户端断开 sid={sid[:8]} "
                            f"累计收字节={recv_bytes} 累计发消息={sent_msgs}")
                break
            if m.get("bytes") is not None:
                recv_bytes += len(m["bytes"])
                try:
                    async for r in session.feed_audio(m["bytes"]):
                        await ws.send_json(r)
                        sent_msgs += 1
                except Exception as e:
                    logger.warning(f"音频处理失败: {e}")
                    await ws.send_json(ErrorMsg(code="feed_failed", message=str(e)).model_dump())
            elif m.get("text"):
                try:
                    typ = json.loads(m["text"]).get("type")
                except (ValueError, TypeError):
                    typ = None
                if typ == "stop":
                    logger.info(f"[stream] 收到 stop sid={sid[:8]} 累计收字节={recv_bytes}")
                    async for r in session.flush():
                        await ws.send_json(r)
                        sent_msgs += 1
                    break
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"实时会话异常: {e}", exc_info=True)
        try:
            await ws.send_json(ErrorMsg(code="internal", message="内部错误", fatal=True).model_dump())
        except Exception:
            pass
    finally:
        try:
            await ws.send_json(SessionClosed(reason="end").model_dump())
        except Exception:
            pass
        _backend.release(session)
