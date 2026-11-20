"""在指定目录生成 16 kHz 单声道 PCM，输出参考标注到标准输出。"""
import hashlib
import json
import random
import struct
import subprocess
import sys
import wave
from pathlib import Path

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
rate = 16000
lines = [
    ("slt", "The blue train leaves at noon."),
    ("rms", "Please meet me at the station."),
    ("slt", "I will bring the yellow bag."),
]
clips = [subprocess.check_output([
    "ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"flite=voice={voice}:text={text}",
    "-ar", str(rate), "-ac", "1", "-f", "s16le", "-"
]) for voice, text in lines]
manifest = {}
for name, order in {"short": [0], "dialogue": [0, 1, 2], "silence": [],
                    "noise": [], "long": [0, 1, 2] * 10}.items():
    pcm = bytearray()
    turns = []
    for index in order:
        pcm.extend(bytes(rate * 2))
        start = len(pcm) / (2 * rate)
        pcm.extend(clips[index])
        voice, text = lines[index]
        turns.append(dict(start=start, end=len(pcm)/(2*rate), voice=voice, text=text))
    pcm.extend(bytes(rate * 2 * (1 if order else 5)))
    if name == "noise":
        rng = random.Random(103)
        pcm = b"".join(struct.pack("<h", rng.randint(-1600, 1600)) for _ in range(rate*5))
    path = out / f"{name}.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        wav.writeframes(pcm)
    manifest[name] = dict(seconds=len(pcm)/(rate*2), turns=turns,
                          sha256=hashlib.sha256(path.read_bytes()).hexdigest())
print(json.dumps(manifest, indent=2))
