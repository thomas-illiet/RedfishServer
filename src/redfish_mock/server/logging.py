"""Structured server logging that excludes request secrets."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

LOGGER = logging.getLogger("redfish_mock")


class JsonFormatter(logging.Formatter):
    """Format log records as JSON with UTC timestamps and structured fields."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize a log record and its structured fields into one JSON line."""
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(*, quiet: bool = False) -> None:
    """Configure application and Uvicorn logs with the same JSON formatter."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(
        level=logging.WARNING if quiet else logging.INFO, handlers=[handler], force=True
    )
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
