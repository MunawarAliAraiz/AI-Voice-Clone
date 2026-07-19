"""
AI Voice Clone Studio — Structured Logging Setup
"""

import logging
import sys
from pathlib import Path
from datetime import datetime
from ..config import settings


def setup_logger(name: str = "voiceclone") -> logging.Logger:
    """Set up a structured logger with file and console handlers."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)

    # Console handler — colorful output
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    console_fmt = logging.Formatter(
        "%(asctime)s │ %(levelname)-8s │ %(name)-20s │ %(message)s",
        datefmt="%H:%M:%S",
    )
    console.setFormatter(console_fmt)
    logger.addHandler(console)

    # File handler — detailed log file
    settings.ensure_directories()
    log_file = settings.logs_dir / f"voiceclone_{datetime.now():%Y%m%d}.log"
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s:%(lineno)d | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_fmt)
    logger.addHandler(file_handler)

    return logger


# Default logger
logger = setup_logger()
