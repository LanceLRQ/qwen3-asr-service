"""只监听本机的 PocketSphinx 英文 WAV 转写样例。"""
import subprocess

from fastapi import FastAPI, File, Form, HTTPException, UploadFile

app = FastAPI()
MODEL = "pocketsphinx-en-us"
ROOT = "/usr/share/pocketsphinx/model/en-us"


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": MODEL, "object": "model"}]}


@app.post("/v1/audio/transcriptions")
def transcribe(file: UploadFile = File(...), model: str = Form(...),
               language: str = Form("en")):
    if model != MODEL:
        raise HTTPException(404, "Unknown model")
    if language != "en":
        raise HTTPException(400, "Only English (en) is supported")
    # 追加静音，使解码器在 VAD 裁剪的块末尾输出最后一句。
    filters = (f"aresample=16000,aformat=channel_layouts=mono,apad=pad_dur=1,"
               f"asr=hmm={ROOT}/en-us:dict={ROOT}/cmudict-en-us.dict:lm={ROOT}/en-us.lm.bin,"
               "ametadata=print:key=lavfi.asr.text:file=-")
    decoded = subprocess.run([
        "ffmpeg", "-v", "error", "-f", "wav", "-i", "pipe:0",
        "-af", filters, "-f", "null", "-"
    ], stdin=file.file, capture_output=True)
    if decoded.returncode:
        raise HTTPException(422, "WAV decoding or recognition failed")
    key = "lavfi.asr.text="
    lines = decoded.stdout.decode().splitlines()
    return {"text": " ".join(line[len(key):] for line in lines if line.startswith(key))}
