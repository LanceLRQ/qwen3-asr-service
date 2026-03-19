import os
import logging
from app.config import LOG_FILE, LOG_DIR


def setup_logger():
    """配置全局日志"""
    os.makedirs(LOG_DIR, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    root.handlers.clear()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.WARNING)
    access_logger.propagate = False
