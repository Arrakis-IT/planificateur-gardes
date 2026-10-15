# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Stdout logging configuration (Docker captures stdout)."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

NOISY_LOGGERS = ("weasyprint", "fontTools")
# Uptime monitor pings (every few minutes) are not worth an access log line.
QUIET_ACCESS_PATHS = frozenset({"/health"})
# Paths whose last segment is a secret (iCal feed token): masked in the access log.
SECRET_PATH_PREFIXES = ("/ical/",)


class QuietPathsFilter(logging.Filter):
    """Drop uvicorn access log lines for QUIET_ACCESS_PATHS (query string ignored) and mask
    the token of SECRET_PATH_PREFIXES paths.

    uvicorn's access record args are (client, method, path, http_version, status).
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3 and isinstance(args[2], str):
            path = args[2].split("?", 1)[0]
            if path in QUIET_ACCESS_PATHS:
                return False
            for prefix in SECRET_PATH_PREFIXES:
                if path.startswith(prefix):
                    record.args = (*args[:2], f"{prefix}[token]", *args[3:])
        return True


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str, json_output: bool) -> None:
    """Configure the root logger: JSON in production, human-readable otherwise."""
    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Chatty third-party loggers (WeasyPrint logs every rendering step at INFO).
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, QuietPathsFilter) for f in access.filters):
        access.addFilter(QuietPathsFilter())
