"""在 asr-service 目录执行 python -m examples.offline input.wav。

使用 standard 环境、ffmpeg 和项目配置的模型目录。项目内接口，尚未承诺独立
SDK 的兼容周期。多文件调用可复用已加载的引擎与 ASRPipeline 实例。
"""
import argparse
import json

from app.pipeline.asr_pipeline import ASRPipeline


def main():
    parser = argparse.ArgumentParser(description="直接调用离线 ASR，输出结构化识别结果")
    parser.add_argument("audio")
    args = parser.parse_args()

    from app.engines.qwen_asr_engine import QwenASREngine
    from app.engines.vad_engine import VADEngine

    asr = QwenASREngine(device="cpu", enable_align=False)
    vad = VADEngine()
    asr.load()
    vad.load()
    result = ASRPipeline(asr, vad).run(args.audio, options={"with_words": False})
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
