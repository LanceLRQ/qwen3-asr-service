"""在项目 ASRPipeline 中用本机 PocketSphinx 运行 S1 英文对话。"""
import hashlib
import json
import os
import httpx
import sys
from pathlib import Path

os.environ.update(CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
import torch
from app import config
from app.engines.remote_asr_engine import RemoteASREngine
from app.engines.forced_alignment import ForcedAlignment, valid_words
from app.engines.speaker_embedding_engine import SpeakerEmbeddingEngine
from app.engines.vad_engine import VADEngine
from app.pipeline.asr_pipeline import ASRPipeline


if __name__ == "__main__":
    models, audio, output = map(Path, sys.argv[1:4])
    url = sys.argv[4].rstrip("/")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    assert digest == "2de70de9210b9d3ae7bd2584ebd41ca2131a9cd06e830fa1c5e9e8cca11cd230"
    assert torch.cuda.device_count() == 0
    torch.set_num_threads(4)
    config.MODEL_SOURCE = "manual"
    config.MODEL_LOCAL_MAP.update(vad=str(models / "vad/fsmn"),
                                  campplus=str(models / "speaker/campplus"),
                                  aligner=str(models / "align/0.6b"))
    vad = VADEngine()
    vad.load()
    speaker = SpeakerEmbeddingEngine()
    speaker.load()
    engine = RemoteASREngine(url, "pocketsphinx-en-us", None, 30)
    engine.load()
    with httpx.Client(trust_env=False) as client:
        listing = client.get(url + "/models").json()
        with audio.open("rb") as stream:
            rejected = client.post(url + "/audio/transcriptions",
                                   data={"model": engine.model, "language": "zh"},
                                   files={"file": ("dialogue.wav", stream, "audio/wav")})
        assert rejected.status_code == 400
    direct = engine.transcribe(str(audio), "en")
    assert direct.text and direct.words is None
    pipeline = ASRPipeline(engine, vad, speaker_engine=speaker,
                           aligner=ForcedAlignment.load("cpu"))
    evidence = {"models": listing, "zh_status": rejected.status_code,
                "direct_text": direct.text, "sample_sha256": digest, "visible_cuda_devices": torch.cuda.device_count()}
    for enabled, name in [(False, "plain"), (True, "enriched")]:
        result = pipeline.run(str(audio), language="en",
                              options={"with_words": enabled, "diarize": enabled})
        assert not result.get("warnings") and result["full_text"]
        segments = result["segments"]
        assert len(segments) == 3
        assert result["full_text"] == " ".join(s["text"] for s in segments)
        assert all(seg["text"].strip() and seg["text"] != "[识别失败]" for seg in segments)
        if enabled:
            assert all(s["speaker"] in result["speakers"] for s in segments)
            assert all(valid_words(s["text"], s["words"], s["start"], s["end"]) for s in segments)
            assert result["full_text"] == evidence["plain"]["full_text"]
        else:
            assert all("speaker" not in s and "words" not in s for s in segments)
        evidence[name] = result
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
