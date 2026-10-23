"""Configure console and rotating-file logging for the application."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging() -> None:
    """Set up application logging once, writing to logs/pipeline.log."""
    app_logger = logging.getLogger("app")
    if app_logger.handlers:
        return

    log_directory = Path(__file__).resolve().parent.parent / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    file_handler = RotatingFileHandler(
        log_directory / "pipeline.log",
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)

    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False
    app_logger.addHandler(console_handler)
    app_logger.addHandler(file_handler)
