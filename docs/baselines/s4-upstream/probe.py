"""Probe the deployed ASR upstream directly, using a short speech sample."""
import argparse
import asyncio
import json
from pathlib import Path

import httpx
import websockets
from websockets.exceptions import InvalidStatus


async def probe(base, audio):
    with httpx.Client(base_url=base, trust_env=False, timeout=30) as client:
        models = client.get("/v1/models")
        models.raise_for_status()
        model = models.json()["data"][0]["id"]
        with audio.open("rb") as source:
            reply = client.post("/v1/audio/transcriptions", data={"model": model},
                                files={"file": (audio.name, source, "audio/wav")})
        reply.raise_for_status()
        paths = client.get("/openapi.json").raise_for_status().json()["paths"]
    uri = base.replace("http", "ws", 1) + "/v1/realtime"
    try:
        async with websockets.connect(uri, proxy=None):
            status = 101
    except InvalidStatus as exc:
        status = exc.response.status_code
    return {"model": model, "transcription": reply.json()["text"],
            "realtime_handshake": status,
            "realtime_http_routes": sorted(p for p in paths if "realtime" in p)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--base", default="http://127.0.0.1:18764")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(probe(args.base.rstrip("/"), args.audio)), indent=2))
