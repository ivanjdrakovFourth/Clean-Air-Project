from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler

from .settings import Settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(settings: Settings) -> None:
    settings.prepare_directories()
    os.environ["TMP"] = str(settings.var / "tmp")
    os.environ["TEMP"] = str(settings.var / "tmp")
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if any(getattr(handler, "clean_air_handler", False) for handler in logger.handlers):
        return
    file_handler = RotatingFileHandler(
        settings.var / "logs" / "application.jsonl",
        maxBytes=5_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    console = logging.StreamHandler()
    for handler in (file_handler, console):
        handler.setFormatter(JsonFormatter())
        handler.clean_air_handler = True
        logger.addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)
