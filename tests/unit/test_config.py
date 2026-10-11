# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Tests for settings and logging setup."""

import json
import logging

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.utils.logging import JsonFormatter, configure_logging


def test_secret_key_too_short_is_rejected():
    with pytest.raises(ValidationError):
        Settings(app_secret_key="short", database_url="postgresql+asyncpg://x")  # type: ignore[arg-type]


def test_is_production_flag():
    settings = Settings(
        app_env="production",
        app_secret_key="x" * 40,  # type: ignore[arg-type]
        database_url="postgresql+asyncpg://x",  # type: ignore[arg-type]
    )
    assert settings.is_production


def test_secrets_not_exposed_in_repr():
    settings = Settings(
        app_secret_key="super-secret-value-" * 3,  # type: ignore[arg-type]
        database_url="postgresql+asyncpg://user:pw@host/db",  # type: ignore[arg-type]
    )
    assert "super-secret-value" not in repr(settings)
    assert "pw@host" not in repr(settings)


def test_json_formatter_outputs_single_line_json():
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    line = JsonFormatter().format(record)
    payload = json.loads(line)
    assert payload["msg"] == "hello world"
    assert payload["level"] == "INFO"


def test_configure_logging_sets_level():
    configure_logging("warning", json_output=True)
    assert logging.getLogger().level == logging.WARNING
    configure_logging("info", json_output=False)
    assert logging.getLogger().level == logging.INFO
