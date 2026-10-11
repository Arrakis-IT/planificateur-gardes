# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Shared pytest fixtures.

Environment is set up *before* the app is imported so settings resolve to test values.
Tests always run against TEST_DATABASE_URL, never against the dev/prod database.
"""

import os
from collections.abc import AsyncIterator

import pytest

os.environ["APP_ENV"] = "test"
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-production-0123456789")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://planificateur_gardes:planificateur_gardes@localhost:5432/planificateur_gardes_test",
)
os.environ["TURNSTILE_ENABLED"] = "false"
os.environ["SESSION_COOKIE_SECURE"] = "false"

from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.fixture
def app():  # type: ignore[no-untyped-def]
    """A fresh FastAPI app per test (dependency overrides do not leak)."""
    return create_app()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    """HTTP client talking to the app in-process."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        yield ac
