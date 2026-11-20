"""三种 WebSocket 实时协议的黑盒验收。Python >= 3.11。"""
import argparse
import asyncio
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus


PATHS = {
    "native": "/v2/asr/stream",
    "openai": "/compat/openai/v1/realtime",
    "dashscope": "/compat/dashscope/api-ws/v1/inference",
}
TEXTS = ["The blue train leaves at noon", "Please meet me at the station"]
KEYWORDS = [("blue", "train"), ("station",)]


async def trial(args, protocol, case, clips, row):
    task = uuid4().hex
    header = {"task_id": task, "streaming": "duplex"}
    start = {
        "native": {"type": "start", "audio_fs": 16000},
        "openai": {"type": "session.update", "session": {"audio": {
            "input": {"format": {"type": "audio/pcm", "rate": 16000}}}}},
        "dashscope": {"header": {**header, "action": "run-task"}, "payload": {
            "task_group": "audio", "task": "asr", "function": "recognition",
            "model": "paraformer-realtime-v2", "input": {},
            "parameters": {"format": "pcm", "sample_rate": 16000}}},
    }
    stop = {"native": {"type": "stop"}, "openai": {"type": "input_audio_buffer.commit"},
            "dashscope": {"header": {**header, "action": "finish-task"}, "payload": {"input": {}}}}
    key = os.getenv("ASR_API_KEY")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with connect((args.slow_base if case in ("slow", "abort_slow", "recover_slow") else args.base) + PATHS[protocol], additional_headers=headers,
                       proxy=None, open_timeout=8, close_timeout=2) as ws:
        async def event():
            msg = json.loads(await ws.recv())
            kind = msg.get("type") or msg["header"]["event"]
            row["events"].append({"ms": round((time.monotonic()-origin)*1000), "event": msg})
            if kind in ("error", "task-failed"):
                raise ValueError(kind)
            if protocol == "dashscope" and msg["header"]["task_id"] != task:
                raise ValueError("task_id 不匹配")
            return kind, msg

        origin = time.monotonic()
        if case in ("disconnect", "recover_slow"):
            row["reconnect_ms"] = round((origin - row.pop("_aborted")) * 1000)
        if protocol != "dashscope":
            kind, msg = await event()
            if kind != "session.created" or (protocol == "native" and msg["mode"] != "remote"):
                raise ValueError("会话未就绪或非 remote 模式")
        await ws.send(json.dumps(start[protocol]))
        if protocol != "native":
            if (await event())[0] != {"openai": "session.updated", "dashscope": "task-started"}[protocol]:
                raise ValueError("配置未确认")
        two = case in ("two", "slow")
        pcm = clips[0] + (bytes(64000) + clips[1] + bytes(64000) if two else b"")
        if case == "abort_slow":
            pcm += bytes(51200)  # 1.6 s 静音触发 VAD，随后在代理延迟窗口内断开。
        sent = time.monotonic()
        finals = []
        ends = [len(clips[0])/32000, (len(pcm)-64000)/32000]
        async def receive():
            kind, msg = await event()
            sentence = msg.get("payload", {}).get("output", {}).get("sentence", {})
            if kind == "partial" or kind.endswith(".delta") or sentence.get("sentence_end") is False:
                raise ValueError("整句模式出现意外的增量事件")
            if kind == "final" or kind.endswith(".input_audio_transcription.completed") or sentence.get("sentence_end"):
                index = len(finals)
                text = msg.get("text") or msg.get("transcript") or sentence.get("text", "")
                if index >= (2 if two else 1) or not all(w in text.lower() for w in KEYWORDS[index]):
                    raise ValueError(f"final 数量、顺序或内容错误: {text}")
                finals.append(text)
                row["end_to_final_ms"].append(round((time.monotonic()-sent-ends[index])*1000))
            return kind
        async def read_two():
            while len(finals) < 2:
                await receive()

        async with asyncio.TaskGroup() as group:
            if two:
                group.create_task(read_two())
            row["max_send_lag_ms"] = 0
            for pos in range(0, len(pcm), 3200):
                frame = pcm[pos:pos+3200]
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(frame).decode()})
                              if protocol == "openai" else frame)
                lag = (time.monotonic()-sent-pos/32000)*1000
                row["max_send_lag_ms"] = max(row["max_send_lag_ms"], round(lag))
                await asyncio.sleep(max(0, sent + (pos+len(frame))/32000 - time.monotonic()))
            if case in ("abort", "abort_slow"):
                row["_aborted"] = time.monotonic()
                row["abort_unix_ms"] = round(time.time()*1000)
                ws.transport.abort()
                return
        await ws.send(json.dumps(stop[protocol]))
        while not finals or protocol != "openai":
            if await receive() in ("session.closed", "task-finished"):
                break
        finished = time.monotonic()
        if protocol == "openai":
            try:
                async with asyncio.timeout(0.5):
                    while True:
                        await receive()
            except TimeoutError:
                pass
        if len(finals) != (2 if two else 1):
            raise ValueError("缺少 final")
        row["audio_s"] = len(pcm)/32000
        row["wall_rtf"] = (finished-sent)/row["audio_s"]


async def main(args):
    clips = [subprocess.check_output(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
             f"flite=text={line}", "-ar", "16000", "-ac", "1", "-f", "s16le", "-"]) for line in TEXTS]
    rows = []
    for protocol in PATHS:
        for case in (("cancel",) if args.cancel_only else ("two", "stop", "disconnect", "slow")):
            row = dict(protocol=protocol, case=case, events=[], end_to_final_ms=[])
            rows.append(row)
            injected = False
            try:
                if case in ("slow", "cancel"):
                    if not args.marker or not args.proxy_log:
                        raise ValueError("缺少慢上游代理参数")
                    offset = args.proxy_log.stat().st_size
                    args.marker.touch(exist_ok=False)
                    injected = True
                async with asyncio.timeout(60):
                    phases = ["abort", "disconnect"] if case == "disconnect" else [case]
                    if case == "cancel":
                        phases = ["abort_slow", "recover_slow"]
                    for phase in phases:
                        deadline = time.monotonic() + 5
                        while True:
                            try:
                                await trial(args, protocol, phase, clips, row)
                                if phase == "abort_slow":
                                    args.marker.unlink()
                                break
                            except InvalidStatus as exc:
                                if exc.response.status_code != 403 or time.monotonic() >= deadline:
                                    raise
                                retries = row.setdefault("handshake_403", {})
                                retries[phase] = retries.get(phase, 0) + 1
                                await asyncio.sleep(0.1)
                if injected:
                    with args.proxy_log.open("rb") as log:
                        log.seek(offset)
                        row["proxy"] = [json.loads(line) for line in log]
                    if case == "cancel":
                        if len(row["proxy"]) != 1:
                            raise ValueError("缺少断连请求的代理证据")
                        remaining = row["proxy"][0]["at"] - row["abort_unix_ms"]
                        if not 0 < row["reconnect_ms"] < remaining <= 2100:
                            raise ValueError("未在旧响应延迟窗口内重获槽位")
                    elif sum(e["delay_ms"] == 2000 and e["path"].endswith("/audio/transcriptions")
                           for e in row["proxy"]) != 2:
                        raise ValueError("未观测到上游延迟注入")
                row["passed"] = True
            except Exception as exc:
                row.pop("_aborted", None)
                row.update(passed=False, error=repr(exc))
            finally:
                if injected:
                    args.marker.unlink(missing_ok=True)
            args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    return all(row["passed"] for row in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="ws://127.0.0.1:18766")
    parser.add_argument("--slow-base", default="ws://127.0.0.1:18768")
    parser.add_argument("--cancel-only", action="store_true")
    parser.add_argument("--marker", type=Path)
    parser.add_argument("--proxy-log", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(main(parser.parse_args())) else 1)
