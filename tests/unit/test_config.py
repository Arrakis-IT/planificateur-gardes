# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Tests for settings and logging setup."""

import json
import logging
import secrets

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
        app_base_url="https://gardes.arrakis.lu",
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


DEV_KEY = "dev-only-DO-NOT-USE-IN-PRODUCTION-replace-this-string-with-generated-one"
DB_URL = "postgresql+asyncpg://user:pw@host/db"


def _set_env(
    monkeypatch, app_env: str, secret: str, base_url: str = "https://gardes.arrakis.lu"
) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("APP_SECRET_KEY", secret)
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setenv("APP_BASE_URL", base_url)


def test_dev_secret_key_rejected_in_production(monkeypatch):
    _set_env(monkeypatch, "production", DEV_KEY)
    with pytest.raises(ValidationError) as exc_info:
        Settings()
    message = str(exc_info.value)
    assert 'APP_SECRET_KEY must not start with "dev-only-" in production' in message
    assert "secrets.token_urlsafe(64)" in message


def test_dev_secret_key_accepted_in_development(monkeypatch):
    _set_env(monkeypatch, "development", DEV_KEY)
    settings = Settings()
    assert settings.app_env == "development"
    assert settings.app_secret_key.get_secret_value() == DEV_KEY


def test_strong_secret_key_accepted_in_production(monkeypatch):
    strong_key = secrets.token_hex(32)  # 64 random hex chars
    assert len(strong_key) == 64
    _set_env(monkeypatch, "production", strong_key)
    settings = Settings()
    assert settings.is_production
    assert settings.app_secret_key.get_secret_value() == strong_key


@pytest.mark.parametrize(
    "base_url", ["http://localhost:8000", "http://gardes.arrakis.lu", "https://127.0.0.1"]
)
def test_production_requires_public_https_base_url(monkeypatch, base_url):
    _set_env(monkeypatch, "production", secrets.token_hex(32), base_url)
    with pytest.raises(ValidationError, match="APP_BASE_URL must be the public https"):
        Settings()


def test_dev_bootstrap_password_rejected_in_production(monkeypatch):
    _set_env(monkeypatch, "production", secrets.token_hex(32))
    monkeypatch.setenv("INITIAL_SUPERADMIN_PASSWORD", "dev-only-admin-password")
    with pytest.raises(ValidationError, match="INITIAL_SUPERADMIN_PASSWORD"):
        Settings()
    monkeypatch.setenv("APP_ENV", "development")
    assert Settings().initial_superadmin_password is not None


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
    assert logging.getLogger("weasyprint").level == logging.WARNING


def test_env_example_has_no_inline_comment_after_empty_value():
    """Docker Compose keeps "# …" as the value of `KEY=   # comment` (it broke SENTRY_DSN)."""
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert not re.findall(r"^[A-Z_]+=[ \t]+#", text, flags=re.M)
