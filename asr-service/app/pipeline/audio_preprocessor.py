import os
import subprocess
import logging
import soundfile as sf

logger = logging.getLogger(__name__)


def check_ffmpeg():
    """检查 ffmpeg 是否可用，不可用则抛出异常"""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise RuntimeError(
            "ffmpeg 未安装或不可用，请先安装 ffmpeg: apt install ffmpeg"
        )


def convert_to_wav(input_path: str, output_path: str) -> None:
    """将任意格式音频转换为 16kHz 单声道 WAV"""
    try:
        subprocess.run(
            [
                "ffmpeg", "-i", input_path,
                "-ar", "16000", "-ac", "1", "-f", "wav",
                "-y", output_path,
            ],
            check=True,
            capture_output=True,
        )
        logger.info(f"音频已转换: {input_path} -> {output_path}")
    except subprocess.CalledProcessError as e:
        stderr = e.stderr.decode("utf-8", errors="replace") if e.stderr else ""
        raise ValueError(f"音频格式转换失败: {stderr}")


def get_audio_duration(wav_path: str) -> float:
    """获取 WAV 文件时长（秒）"""
    info = sf.info(wav_path)
    return info.duration


def get_file_size_mb(file_path: str) -> float:
    """获取文件大小（MB）"""
    return os.path.getsize(file_path) / (1024 * 1024)
