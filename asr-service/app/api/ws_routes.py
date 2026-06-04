"""实时转写统一端点 WS /v2/asr/stream（后端无关）。

两种 serve-mode 共用此端点；启动时注入"活动后端"（路线 B / 路线 A），
二者实现同一 StreamBackend 接口。连接即下发 session.created 声明协议/后端/能力。
鉴权复用 cfg.API_KEY + hmac.compare_digest（与 HTTP 一致）。
"""
import asyncio
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

    # acquire 成功后任何失败路径（含 accept 异常）都必须经 finally 释放计数
    session = None
    consume_task = None
    recv_bytes = 0
    sent_msgs = 0
    backlog_bytes = 0      # 已入队未处理完的字节数（接收侧增、消费侧减，单循环内无竞态）
    try:
        await ws.accept()
        # 连接即声明协议/后端/能力
        await ws.send_json(SessionCreated(
            mode=_backend.mode,
            backend=_backend.backend,
            capabilities=_backend.capabilities,
        ).model_dump())

        sid = uuid4().hex
        session = _backend.create_session(sid)
        logger.info(f"[stream] WS accepted sid={sid[:8]}")

        # 会话级超时：从 accept 起计 STREAM_MAX_SESSION_SECONDS（含等待 start 阶段）
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.STREAM_MAX_SESSION_SECONDS

        start_msg = await asyncio.wait_for(             # 首条 {type:"start", ...}
            ws.receive_json(), timeout=deadline - loop.time())
        logger.info(f"[stream] 收到 start: {start_msg}")
        try:
            session.configure(start_msg)
        except ValueError as e:
            # 配置校验失败属客户端错误，消息为服务端自产文案，可直接回传
            await ws.send_json(ErrorMsg(
                code="invalid_config", message=str(e), fatal=True).model_dump())
            return

        # ── 收发解耦：接收循环只入队，独立任务消费（VAD/ASR/发送）──
        # 推理慢于实时时积压留在应用层队列，接收不阻塞，pong 可被及时读取，
        # 避免 websockets 接收队列满 → 读帧暂停 → keepalive 误判超时杀连接。
        frame_q: asyncio.Queue = asyncio.Queue()

        async def _consume():
            nonlocal backlog_bytes, sent_msgs
            while True:
                item = await frame_q.get()
                if item is None:                    # stop 哨兵：冲刷末句后结束
                    async for r in session.flush():
                        await ws.send_json(r)
                        sent_msgs += 1
                    return
                try:
                    async for r in session.feed_audio(item):
                        await ws.send_json(r)
                        sent_msgs += 1
                except WebSocketDisconnect:
                    raise                           # 连接已断，交由主循环统一收尾
                except Exception as e:
                    logger.warning(f"音频处理失败: {e}", exc_info=True)
                    try:
                        await ws.send_json(ErrorMsg(
                            code="feed_failed", message="音频处理失败").model_dump())
                    except Exception:
                        return                      # 连接不可用，结束消费
                finally:
                    backlog_bytes -= len(item)

        consume_task = asyncio.create_task(_consume())

        while True:
            if consume_task.done():
                consume_task.result()               # 消费侧异常上抛；正常结束则连接已不可用
                break
            m = await asyncio.wait_for(ws.receive(), timeout=deadline - loop.time())
            if m["type"] == "websocket.disconnect":
                logger.info(f"[stream] 客户端断开 sid={sid[:8]} "
                            f"累计收字节={recv_bytes} 累计发消息={sent_msgs}")
                break
            if m.get("bytes") is not None:
                if len(m["bytes"]) > cfg.STREAM_MAX_FRAME_BYTES:
                    logger.warning(f"[stream] 拒收超限帧 sid={sid[:8]} "
                                   f"{len(m['bytes'])}B > {cfg.STREAM_MAX_FRAME_BYTES}B")
                    await ws.send_json(ErrorMsg(
                        code="frame_too_large",
                        message=f"单帧超过上限 {cfg.STREAM_MAX_FRAME_BYTES} 字节").model_dump())
                    continue
                if backlog_bytes + len(m["bytes"]) > cfg.STREAM_MAX_BACKLOG_BYTES:
                    logger.warning(f"[stream] 处理积压超限断开 sid={sid[:8]} "
                                   f"backlog={backlog_bytes}B > {cfg.STREAM_MAX_BACKLOG_BYTES}B")
                    await ws.send_json(ErrorMsg(
                        code="backlog_overflow",
                        message="服务端处理积压超限，请降低推流速率或稍后重试",
                        fatal=True).model_dump())
                    break
                recv_bytes += len(m["bytes"])
                backlog_bytes += len(m["bytes"])
                frame_q.put_nowait(m["bytes"])
            elif m.get("text"):
                try:
                    typ = json.loads(m["text"]).get("type")
                except (ValueError, TypeError):
                    typ = None
                if typ == "stop":
                    logger.info(f"[stream] 收到 stop sid={sid[:8]} 累计收字节={recv_bytes}")
                    frame_q.put_nowait(None)
                    await consume_task              # 消费完积压并冲刷末句
                    break
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.info(f"[stream] 会话超时关闭 (>{cfg.STREAM_MAX_SESSION_SECONDS}s) "
                    f"累计收字节={recv_bytes}")
        try:
            await ws.send_json(ErrorMsg(
                code="session_timeout", message="会话超时", fatal=True).model_dump())
        except Exception:
            pass
    except Exception as e:
        logger.error(f"实时会话异常: {e}", exc_info=True)
        try:
            await ws.send_json(ErrorMsg(code="internal", message="内部错误", fatal=True).model_dump())
        except Exception:
            pass
    finally:
        # 先停消费任务再发收尾消息，避免两个协程并发写同一 WS
        if consume_task is not None and not consume_task.done():
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await ws.send_json(SessionClosed(reason="end").model_dump())
        except Exception:
            pass
        _backend.release(session)
