"""Logging configuration for Douga backend.

In production (ENVIRONMENT=production) structured JSON logs are emitted to
stdout so that Cloud Logging can index the ``severity`` and ``message`` fields
natively.  In every other environment the conventional human-readable format
is used.

Usage (called once at application startup, before any logger.* calls):
    from src.logging_config import configure_logging
    configure_logging()

Individual modules should then obtain their logger in the usual way:
    logger = logging.getLogger(__name__)
"""

import json
import logging
import os
from typing import Any


class _CloudLoggingFormatter(logging.Formatter):
    """Emit one JSON object per log record, compatible with Cloud Logging.

    Cloud Logging parses ``severity`` (uppercase) and ``message`` from the
    structured-JSON payload written to stdout.  Additional fields set via
    ``extra={"key": value}`` are included at the top level so they become
    queryable JSON payload fields.

    Reference:
      https://cloud.google.com/logging/docs/structured-logging
    """

    _LEVEL_TO_SEVERITY: dict[int, str] = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRITICAL",
    }

    # Keys that are already part of the LogRecord and should not be
    # re-emitted as extra fields.
    _RESERVED_ATTRS: frozenset[str] = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "message",
            "module",
            "msecs",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()

        entry: dict[str, Any] = {
            "severity": self._LEVEL_TO_SEVERITY.get(record.levelno, record.levelname),
            "message": message,
            "logger": record.name,
            "module": record.module,
            "funcName": record.funcName,
            "lineno": record.lineno,
        }

        # Include exception information if present.
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            entry["stack_info"] = self.formatStack(record.stack_info)

        # Include any user-supplied extra fields.
        for key, value in record.__dict__.items():
            if key not in self._RESERVED_ATTRS:
                entry[key] = value

        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Set up the root logger for the application.

    - production  → JSON formatter on stdout (Cloud Logging structured log)
    - everything else → human-readable formatter (uvicorn-compatible)

    Calling this function multiple times is safe; duplicate handlers are not
    added.  The *force=False* flag ensures we do not discard handlers that
    uvicorn may already have installed.
    """
    environment = os.environ.get("ENVIRONMENT", "development")
    is_production = environment == "production"

    root_logger = logging.getLogger()

    if is_production:
        formatter: logging.Formatter = _CloudLoggingFormatter()
    else:
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
        )

    # Add a StreamHandler only when the root logger has no handlers yet
    # (i.e., basicConfig has not been called, and uvicorn hasn't attached one).
    if not root_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)
        root_logger.setLevel(logging.INFO)
    else:
        # Handlers already exist (e.g. uvicorn installed them).
        # Just replace the formatter on each existing handler so that the
        # right format is used without disturbing the handler configuration.
        for existing_handler in root_logger.handlers:
            existing_handler.setFormatter(formatter)
