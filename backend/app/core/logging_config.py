import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.core.config import clean_env


_CONFIGURED = False


def configure_logging():
    global _CONFIGURED

    log_dir = Path(clean_env("LOG_DIR", "logs") or "logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    level_name = (clean_env("LOG_LEVEL", "INFO") or "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    if not _CONFIGURED:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(formatter)

        file_handler = RotatingFileHandler(
            log_dir / "smart_inventory.log",
            maxBytes=5_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)

        root_logger.addHandler(stream_handler)
        root_logger.addHandler(file_handler)
        _CONFIGURED = True

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return logging.getLogger("smart_inventory")
