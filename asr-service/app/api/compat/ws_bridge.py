"""实时兼容 WS 共享骨架（OpenAI Realtime / DashScope Realtime 复用）。

v2 的 ws_routes.stream 是已稳定的紧耦合 handler，不侵入它；这里另起一份骨架，把
最易出错的部分集中一处：鉴权 / acquire 准入 / 收发解耦消费 / 帧大小·积压上限 /
会话超时 / 连接复用 / finally release。协议差异全部收进 adapter（每连接一个实例）。

adapter 鸭子接口：
  reusable: bool                         # end 后是否保持连接等下一轮（DashScope=True / OpenAI=False）
  async on_open(ws, backend)             # 连接建立后发上游"已建立"消息（OpenAI session.created；DashScope 无）
  classify(m) -> (kind, payload)         # starlette receive dict → kind∈{configure,audio,flush,end,ignore}
                                         #   configure→cfg dict；audio→PCM bytes（OpenAI 已 base64 解码）
  async on_configured(ws, warnings)      # configure 成功后（OpenAI session.updated；DashScope task-started）
  translate_finals(final) -> list[dict]  # 一条 final → 上游事件（OpenAI completed / DashScope result-generated）
  translate_partials(p) -> list[dict]    # （可选，R2）一条 partial → 增量事件；未实现 = R1 finals-only
  translate_error(code, message, *, fatal=False) -> dict
  async on_finish(ws)                    # end（HARD_END）冲刷后（DashScope task-finished；OpenAI 不触发）

partial 分发：route B 只产整句 final；vLLM 路线 A 会产 partial 增量。消费循环 _emit 把
type=="partial" 的事件交 adapter.translate_partials（若实现，R2 增量下发：OpenAI delta /
DashScope 中间 result-generated），未实现则跳过（R1 finals-only，整句行为同 standard）；
type!="partial" 一律交 translate_finals。adapter 缺 translate_partials 时行为与 R1 完全一致。
"""
import asyncio
import logging
from uuid import uuid4

from fastapi import WebSocket, WebSocketDisconnect

import app.config as cfg
from app.api.ws_routes import verify_ws_token

logger = logging.getLogger(__name__)

# 活动后端（与 v2 共享同一 StreamBackend 实例；并发计数同源），由 main.py 注入
_backend = None
_recording_manager = None

# 消费队列哨兵
_SOFT_FLUSH = object()   # 冲刷当前缓冲末句但**不结束**本轮（OpenAI commit）
_HARD_END = object()     # 冲刷并结束本轮（DashScope finish-task）


def init_compat_ws(backend, recording_manager=None):
    """注入实时兼容用活动后端（None=未启用实时，端点不挂载）。"""
    global _backend, _recording_manager
    _backend = backend
    _recording_manager = recording_manager


async def _run_round(ws: WebSocket, adapter, session, deadline, loop, holder) -> bool:
    """单轮会话（一次 run-task 周期 / OpenAI 整个连接）。返回是否继续复用连接。"""
    frame_q: asyncio.Queue = asyncio.Queue()
    state = {"backlog": 0}
    recorder = None

    async def _emit(f):
        # partial（vLLM 路线 A）：adapter 实现 translate_partials → 下发增量（R2）；未实现 → 跳过
        # （R1 finals-only）。route B 不产 partial，此分支不触发。final 一律交 translate_finals。
        if f.get("type") == "partial":
            fn = getattr(adapter, "translate_partials", None)
            if fn is None:
                return
            for ev in fn(f):
                await ws.send_json(ev)
            return
        for ev in adapter.translate_finals(f):
            await ws.send_json(ev)

    async def _consume():
        while True:
            item = await frame_q.get()
            if item is _SOFT_FLUSH:
                async for f in session.flush():
                    await _emit(f)
                continue
            if item is _HARD_END:
                async for f in session.flush():
                    await _emit(f)
                return
            try:
                async for f in session.feed_audio(item):
                    await _emit(f)
            except WebSocketDisconnect:
                raise
            except Exception as e:
                logger.warning(f"[compat-ws] 音频处理失败: {e}", exc_info=True)
                try:
                    await ws.send_json(adapter.translate_error("feed_failed", "音频处理失败"))
                except Exception:
                    return
            finally:
                state["backlog"] -= len(item)

    consume_task = asyncio.create_task(_consume())
    holder["task"] = consume_task
    try:
        while True:
            if consume_task.done():
                consume_task.result()        # 消费侧异常上抛；正常结束 → 连接不可用
                return False
            m = await asyncio.wait_for(ws.receive(), timeout=deadline - loop.time())
            if m["type"] == "websocket.disconnect":
                return False
            kind, payload = adapter.classify(m)
            if kind == "configure":
                try:
                    warnings = session.configure(payload)
                except ValueError as e:
                    await ws.send_json(adapter.translate_error("invalid_config", str(e), fatal=True))
                    return False
                if recorder is not None:
                    recorder.close()
                    recorder = None
                if _recording_manager is not None:
                    sample_rate = getattr(session, "audio_fs", payload.get("audio_fs", cfg.STREAM_SAMPLE_RATE))
                    recorder = _recording_manager.start(
                        wav_name=payload.get("wav_name") or "compat-stream",
                        sample_rate=sample_rate,
                    )
                await adapter.on_configured(ws, warnings)
            elif kind == "audio":
                if not payload:
                    continue
                if len(payload) > cfg.STREAM_MAX_FRAME_BYTES:
                    await ws.send_json(adapter.translate_error(
                        "frame_too_large", f"单帧超过上限 {cfg.STREAM_MAX_FRAME_BYTES} 字节"))
                    continue
                if state["backlog"] + len(payload) > cfg.STREAM_MAX_BACKLOG_BYTES:
                    await ws.send_json(adapter.translate_error(
                        "backlog_overflow", "服务端处理积压超限，请降低推流速率", fatal=True))
                    return False
                state["backlog"] += len(payload)
                if recorder is not None:
                    try:
                        recorder.write(payload)
                    except Exception as e:
                        logger.warning(f"[compat-ws] 录音写入失败，停止本轮录音: {e}", exc_info=True)
                        try:
                            recorder.close()
                        except Exception:
                            pass
                        recorder = None
                frame_q.put_nowait(payload)
            elif kind == "flush":
                frame_q.put_nowait(_SOFT_FLUSH)
            elif kind == "end":
                frame_q.put_nowait(_HARD_END)
                await consume_task
                await adapter.on_finish(ws)
                return adapter.reusable
            # kind == "ignore": 跳过
    finally:
        if recorder is not None:
            try:
                recorder.close()
            except Exception:
                pass
        if not consume_task.done():
            consume_task.cancel()
            try:
                await consume_task
            except (asyncio.CancelledError, Exception):
                pass


async def run_compat_ws(ws: WebSocket, adapter) -> None:
    """实时兼容 WS 主骨架：鉴权 → 准入 → 多轮会话循环 → finally 释放。"""
    if not await verify_ws_token(ws):
        await ws.close(code=1008)
        return
    backend = _backend
    if backend is None:
        await ws.close(code=1011)
        return
    if not await backend.acquire():
        await ws.close(code=1013)
        return

    sid = uuid4().hex
    session = backend.create_session(sid)
    holder = {"task": None}
    try:
        await ws.accept()
        await adapter.on_open(ws, backend)
        logger.info(f"[compat-ws] accepted sid={sid[:8]} adapter={type(adapter).__name__}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + cfg.STREAM_MAX_SESSION_SECONDS
        while True:
            reuse = await _run_round(ws, adapter, session, deadline, loop, holder)
            if not reuse:
                break
            # 连接复用：沿用同一 session（下一轮 run-task 的 configure 会重置缓冲/VAD/seg_id），
            # 不新建——避免多 session 缓冲堆积，且 acquire/release 计数始终配对一次
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.info(f"[compat-ws] 会话超时关闭 (>{cfg.STREAM_MAX_SESSION_SECONDS}s) sid={sid[:8]}")
        try:
            await ws.send_json(adapter.translate_error("session_timeout", "会话超时", fatal=True))
        except Exception:
            pass
    except Exception as e:
        logger.error(f"[compat-ws] 会话异常: {e}", exc_info=True)
        try:
            await ws.send_json(adapter.translate_error("internal", "内部错误", fatal=True))
        except Exception:
            pass
    finally:
        ct = holder.get("task")
        if ct is not None and not ct.done():
            ct.cancel()
            try:
                await ct
            except (asyncio.CancelledError, Exception):
                pass
        backend.release(session)
