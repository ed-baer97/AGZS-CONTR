"""Структурный журнал без паролей, токенов и номеров карт."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import TextIO

from agzs_station.config import Settings

_SECRET = re.compile(
    r"(?i)\b(password|token|secret|api_key|authorization|pan)\b\s*[:=]\s*([^\s,;]+)"
)
_CORRELATION = ("sale_id", "pump_id", "station_id")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        for key in _CORRELATION:
            value = getattr(record, key, None)
            if value:
                payload[key] = str(value)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def redact(text: str) -> str:
    return _SECRET.sub(lambda match: f"{match.group(1)}=***", text)


def configure_logging(settings: Settings, stream: TextIO | None = None) -> None:
    logger = logging.getLogger("agzs")
    logger.handlers.clear()
    logger.disabled = False
    logger.setLevel(settings.log_level)
    logger.propagate = False

    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(JsonFormatter())
    logger.addHandler(console)

    settings.log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(settings.log_dir / "station.log", encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    logger.addHandler(file_handler)
