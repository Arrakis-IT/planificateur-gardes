# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Application settings loaded from environment variables (and `.env` in development)."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SECRET_KEY_MIN_LENGTH = 32
DEV_SECRET_KEY_PREFIX = "dev-only-"  # noqa: S105 - marker of the dev placeholder, not a secret


class Settings(BaseSettings):
    """All runtime configuration. See `.env.example` for documentation of each variable."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Never echo raw input values (secrets) in validation errors / logs.
        hide_input_in_errors=True,
    )

    # App
    app_env: Literal["development", "production", "test"] = "development"
    app_secret_key: SecretStr = Field(min_length=SECRET_KEY_MIN_LENGTH)
    app_base_url: str = "http://localhost:8000"

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
    sentry_environment: str | None = None  # defaults to APP_ENV

    # Rate limiting
    rate_limit_login_per_minute: int = 5
    rate_limit_reset_per_hour: int = 3

    # Account lock: N failed logins within the window lock the account for the same duration.
    login_lock_threshold: int = 10
    login_lock_minutes: int = 15

    # Session cookie
    session_cookie_secure: bool = True
    session_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    session_max_age_days: int = 14

    # Password reset
    password_reset_ttl_minutes: int = 60

    # Logging
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        """True when running in the production environment."""
        return self.app_env == "production"

    @model_validator(mode="after")
    def _reject_weak_secret_in_production(self) -> Self:
        """Refuse to start in production with the dev placeholder secret key.

        Minimum length is enforced for every environment by the field constraint.
        """
        secret = self.app_secret_key.get_secret_value()
        if self.is_production and secret.startswith(DEV_SECRET_KEY_PREFIX):
            raise ValueError(
                'APP_SECRET_KEY must not start with "dev-only-" in production. '
                'Generate one with: python3 -c "import secrets; print(secrets.token_urlsafe(64))"'
            )
        return self

    @model_validator(mode="after")
    def _require_public_base_url_in_production(self) -> Self:
        """Emailed links (reset, schedule) are built from APP_BASE_URL: no localhost in prod."""
        url = self.app_base_url.lower()
        if self.is_production and (
            not url.startswith("https://") or "localhost" in url or "127.0.0.1" in url
        ):
            raise ValueError(
                "APP_BASE_URL must be the public https:// URL in production "
                "(e.g. https://gardes.arrakis.lu): it is used in emailed links."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached)."""
    return Settings()
