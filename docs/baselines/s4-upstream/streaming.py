"""S4-02：仅通过网关 WebSocket 观测实时事件，保存每次会话的时序。"""
import argparse
import asyncio
import base64
import hashlib
import json
import subprocess
import time
from pathlib import Path
from urllib.request import build_opener, ProxyHandler
from uuid import uuid4

import numpy as np
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

PATHS = {"native": "/v2/asr/stream", "openai": "/compat/openai/v1/realtime",
         "dashscope": "/compat/dashscope/api-ws/v1/inference"}
LINES = ["The blue train leaves the station early in the morning and arrives at noon.",
         "Please meet me at the station and remember to bring the yellow bag."]


def synthesize(line):
    raw = subprocess.check_output(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                   f"flite=voice=slt:text={line}", "-ar", "16000", "-ac", "1",
                                   "-f", "s16le", "-"])
    samples = np.frombuffer(raw, dtype="<i2")
    active = np.flatnonzero(np.abs(samples.astype(int)) > 300)
    return raw[:2 * (active[-1] + 1)]


async def session(base, protocol, case, clips, row):
    origin = time.monotonic()
    audio_bytes = 0
    task = uuid4().hex
    header = {"task_id": task, "streaming": "duplex"}
    start = {"native": {"type": "start", "audio_fs": 16000},
             "openai": {"type": "session.update", "session": {"audio": {
                 "input": {"format": {"type": "audio/pcm", "rate": 16000}}}}},
             "dashscope": {"header": {**header, "action": "run-task"}, "payload": {
                 "task_group": "audio", "task": "asr", "function": "recognition",
                 "model": "paraformer-realtime-v2", "input": {},
                 "parameters": {"format": "pcm", "sample_rate": 16000}}}}
    stop = {"native": {"type": "stop"}, "openai": {"type": "input_audio_buffer.commit"},
            "dashscope": {"header": {**header, "action": "finish-task"}, "payload": {"input": {}}}}

    def mark(kind, **values):
        row["events"].append(dict(ms=round((time.monotonic()-origin)*1000),
                                  audio_ms=round(audio_bytes/32), kind=kind, **values))

    while True:
        try:
            ws = await connect(base + PATHS[protocol], proxy=None, close_timeout=2)
            break
        except InvalidStatus as exc:
            if case != "reconnect" or exc.response.status_code != 403 or time.monotonic()-origin >= 5:
                raise
            mark("handshake.403")
            await asyncio.sleep(0.1)
    mark("handshake.ok", unix_ms=round(time.time()*1000))
    async with ws:
        async def read():
            msg = json.loads(await ws.recv())
            kind = msg.get("type") or msg["header"]["event"]
            sentence = msg.get("payload", {}).get("output", {}).get("sentence", {})
            if protocol == "dashscope":
                assert msg["header"]["task_id"] == task
            if kind == "partial" or kind.endswith(".delta") or sentence.get("sentence_end") is False:
                assert isinstance(msg.get("text", msg.get("delta", sentence.get("text"))), str)
                kind = "partial"
            if kind == "final" or kind.endswith(".completed") or sentence.get("sentence_end") is True:
                kind = "final"
            mark(kind, event=msg)
            assert kind not in ("error", "task-failed"), msg
            return kind, msg

        if protocol != "dashscope":
            kind, hello = await read()
            assert kind == "session.created"
            if protocol == "native":
                assert hello["capabilities"]["partial_results"]
        await ws.send(json.dumps(start[protocol]))
        if protocol != "native":
            assert (await read())[0] in ("session.updated", "task-started")

        async def send(pcm, pace):
            nonlocal audio_bytes
            started = time.monotonic()
            for pos in range(0, len(pcm), 3200):
                chunk = pcm[pos:pos+3200]
                await ws.send(json.dumps({"type": "input_audio_buffer.append",
                                          "audio": base64.b64encode(chunk).decode()})
                              if protocol == "openai" else chunk)
                audio_bytes += len(chunk)
                await asyncio.sleep(max(0, started + (pos+len(chunk))*pace/32000 - time.monotonic()))

        async def receive_until(target):
            while True:
                kind, msg = await read()
                if kind == target:
                    return msg

        if case == "disconnect":
            async with asyncio.TaskGroup() as group:
                sending = group.create_task(send(clips[0], 1))
                await receive_until("partial")
                sending.cancel()
                mark("client.abort", unix_ms=round(time.time()*1000))
                ws.transport.abort()
            return

        sentence_ids = []
        for i, pcm in enumerate(clips):
            before = len(row["events"])
            async with asyncio.TaskGroup() as group:
                receiving = group.create_task(receive_until("final"))
                mark("utterance.start", index=i)
                await send(pcm, 1.6 if case == "slow_input" else 1)
                mark("utterance.end", index=i)
                if i == 0:
                    await send(bytes(64000), 1)
                else:
                    mark("client.stop")
                    await ws.send(json.dumps(stop[protocol]))
            events = row["events"][before:]
            end = next(e for e in events if e["kind"] == "utterance.end")
            assert any(e["kind"] == "partial" and e["audio_ms"] < end["audio_ms"]
                       and (e["event"].get("text") or e["event"].get("delta")
                            or e["event"].get("payload", {}).get("output", {}).get("sentence", {}).get("text"))
                       for e in events), "缺少该句尾音送出前的非空 partial"
            ids = []
            for event in events:
                if event["kind"] in ("partial", "final"):
                    payload = event["event"]
                    if protocol == "native":
                        ids.append(payload["seg_id"])
                    elif protocol == "openai":
                        ids.append(payload["item_id"])
                    else:
                        ids.append(payload["payload"]["output"]["sentence"]["sentence_id"])
            assert len(set(ids)) == 1 and ids[0] not in sentence_ids, ids
            sentence_ids.append(ids[0])
            msg = receiving.result()
            text = (msg.get("text") or msg.get("transcript")
                    or msg["payload"]["output"]["sentence"]["text"]).lower()
            assert all(w in text for w in (("train", "noon") if i == 0 else ("station", "bag"))), text
        if protocol == "openai":
            mark("post_commit_observe.start")
            try:
                async with asyncio.timeout(0.5):
                    while True:
                        kind, msg = await read()
                        assert kind not in ("partial", "final"), msg
            except TimeoutError:
                mark("post_commit_observe.end")
        else:
            assert (await read())[0] in ("session.closed", "task-finished")
            if protocol == "native":
                await asyncio.wait_for(ws.wait_closed(), 2)
                assert ws.close_code == 1000, ws.close_code
                mark("websocket.close", code=ws.close_code)


async def main(args):
    clips = [synthesize(line) for line in LINES]
    opener = build_opener(ProxyHandler({}))
    with opener.open(args.base.replace("ws://", "http://") + "/v2/health", timeout=8) as response:
        health = json.load(response)
    evidence = {"gateway": args.base, "health": health, "started_unix": time.time(),
                "audio": [{"text": line, "seconds": len(pcm)/32000,
                           "pcm_sha256": hashlib.sha256(pcm).hexdigest()} for line, pcm in zip(LINES, clips)],
                "sessions": []}
    for protocol in args.protocol or PATHS:
        for case in ("two_tail", "slow_input", "disconnect", "reconnect"):
            row = {"protocol": protocol, "case": case, "started_unix": time.time(), "events": []}
            evidence["sessions"].append(row)
            try:
                async with asyncio.timeout(50):
                    await session(args.base, protocol, case, clips, row)
                if case == "reconnect":
                    previous = evidence["sessions"][-2]
                    aborted = next(e for e in previous["events"] if e["kind"] == "client.abort")
                    opened = next(e for e in row["events"] if e["kind"] == "handshake.ok")
                    row["handshake_403_count"] = sum(e["kind"] == "handshake.403" for e in row["events"])
                    row["abort_to_handshake_ms"] = opened["unix_ms"] - aborted["unix_ms"]
                row["passed"] = True
            except Exception as exc:
                row.update(passed=False, error=repr(exc))
            args.out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
            print(protocol, case, row["passed"], row.get("error", ""), flush=True)
    return all(row["passed"] for row in evidence["sessions"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="ws://127.0.0.1:18843")
    parser.add_argument("--protocol", action="append", choices=PATHS)
    parser.add_argument("--out", type=Path, required=True)
    raise SystemExit(0 if asyncio.run(main(parser.parse_args())) else 1)
