"""直连官方 streaming demo，逐半秒发送 float32 PCM 并记录原始响应。"""
import argparse
import hashlib
import json
import time
import wave
from pathlib import Path

import httpx
import numpy as np


def capture(args):
    with wave.open(str(args.audio)) as wav:
        assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
        pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
    end = np.flatnonzero(np.abs(pcm.astype(int)) > 300)[-1] + 1
    pcm = pcm[:end].astype("<f4") / 32768
    origin = time.monotonic()
    events = []
    with httpx.Client(base_url=args.base, trust_env=False, timeout=60) as client:
        def post(path, **kwargs):
            started = time.monotonic()
            response = client.post(path, **kwargs)
            response.raise_for_status()
            body = response.json()
            events.append({"path": path, "sent_ms": round((started-origin)*1000),
                           "received_ms": round((time.monotonic()-origin)*1000),
                           "body": body})
            return body

        sid = post("/api/start")["session_id"]
        try:
            for off in range(0, len(pcm), 8000):
                chunk = pcm[off:off+8000]
                post("/api/chunk", params={"session_id": sid}, content=chunk.tobytes(),
                     headers={"Content-Type": "application/octet-stream"})
                events[-1]["audio_s"] = (off + len(chunk))/16000
        finally:
            final = post("/api/finish", params={"session_id": sid})
    evidence = {"base": args.base, "sample_sha256": hashlib.sha256(args.audio.read_bytes()).hexdigest(),
                "audio_s": len(pcm)/16000, "events": events}
    args.out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n")
    assert any(e["body"].get("text") and e.get("audio_s", len(pcm)/16000) < len(pcm)/16000
               for e in events)
    assert all(word in final["text"].lower() for word in ("train", "noon"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("audio", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:18842")
    parser.add_argument("--out", required=True, type=Path)
    capture(parser.parse_args())
