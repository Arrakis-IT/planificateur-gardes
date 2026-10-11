# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Cloudflare Turnstile server-side verification."""

import logging
from typing import Protocol

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FORM_FIELD = "cf-turnstile-response"


class TurnstileVerifier(Protocol):
    """Verifies a Turnstile response token."""

    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        """Return True if the challenge was passed (or verification is disabled)."""
        ...


class CloudflareTurnstileVerifier:
    """Calls the Cloudflare siteverify API. Fails closed on any error."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        """Return True if Cloudflare accepts the token; always True when disabled."""
        if not self._settings.turnstile_enabled:
            return True
        secret = self._settings.turnstile_secret_key
        if not token or secret is None:
            return False
        data = {"secret": secret.get_secret_value(), "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(VERIFY_URL, data=data)
                response.raise_for_status()
                return bool(response.json().get("success", False))
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Turnstile verification error: %s", type(exc).__name__)
            return False


def get_turnstile_verifier() -> TurnstileVerifier:
    """FastAPI dependency; overridden in tests."""
    return CloudflareTurnstileVerifier(get_settings())
