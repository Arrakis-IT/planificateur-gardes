# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Application settings loaded from environment variables (and `.env` in development)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration. See `.env.example` for documentation of each variable."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # App
    app_env: Literal["development", "production", "test"] = "development"
    app_secret_key: SecretStr = Field(min_length=32)
    app_base_url: str = "http://localhost:8000"
    app_release: str = "dev"

    # Database
    database_url: SecretStr

    # Bootstrap superadmin
    initial_superadmin_email: str | None = None
    initial_superadmin_password: SecretStr | None = None
    initial_superadmin_name: str | None = None

    # Email (Brevo SMTP)
    brevo_smtp_host: str = "smtp-relay.brevo.com"
    brevo_smtp_port: int = 587
    brevo_smtp_user: str | None = None
    brevo_smtp_password: SecretStr | None = None
    brevo_sender_email: str = "noreply@arrakis.lu"
    brevo_sender_name: str = "Planificateur des gardes"

    # Dev email override (Mailpit)
    mail_dev_host: str | None = None
    mail_dev_port: int | None = None

    # Cloudflare Turnstile
    turnstile_enabled: bool = True
    turnstile_site_key: str | None = None
    turnstile_secret_key: SecretStr | None = None

    # Sentry
    sentry_dsn: SecretStr | None = None
    sentry_environment: str = "production"

    # Rate limiting
    rate_limit_login_per_minute: int = 5
    rate_limit_reset_per_hour: int = 3

    # Session cookie
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"

    # Logging
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
