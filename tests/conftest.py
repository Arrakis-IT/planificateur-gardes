# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Shared pytest fixtures.

Environment is set up *before* the app is imported so settings resolve to test values.
Tests always run against TEST_DATABASE_URL, never against the dev/prod database.
"""

import asyncio
import os
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import pytest

os.environ["APP_ENV"] = "test"
os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-not-for-production-0123456789")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://planificateur_gardes:planificateur_gardes@localhost:5432/planificateur_gardes_test",
)
os.environ["TURNSTILE_ENABLED"] = "false"
os.environ["SESSION_COOKIE_SECURE"] = "false"
os.environ["INITIAL_SUPERADMIN_EMAIL"] = ""
os.environ["APP_BASE_URL"] = "http://testserver"

from alembic.config import Config
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.db import check_database, get_sessionmaker
from app.main import create_app
from app.middleware.csrf import CSRF_COOKIE_NAME, CSRF_FORM_FIELD, form_token
from app.middleware.rate_limit import limiter
from app.models import Clinic, User
from app.repositories import clinics as clinics_repo
from app.repositories import users as users_repo
from app.services.mail import OutgoingEmail, get_mail_sender
from app.services.turnstile import get_turnstile_verifier

TEST_CSRF_COOKIE = "test-csrf-cookie-value-0123456789abcdef"
DEFAULT_PASSWORD = "correct-horse-battery-staple"
TABLES = ["audit_log", "session", "password_reset_token", '"user"', "clinic"]


# --- Fakes ----------------------------------------------------------------------------


@dataclass
class FakeMailer:
    """Captures outgoing emails instead of sending them."""

    sent: list[OutgoingEmail] = field(default_factory=list)
    fail: bool = False
    fail_for: set[str] = field(default_factory=set)

    async def send(self, message: OutgoingEmail) -> None:
        if self.fail or message.to in self.fail_for:
            raise ConnectionError("SMTP down")
        self.sent.append(message)

    def last_link(self) -> str:
        match = re.search(r"http://testserver(/reset/\S+)", self.sent[-1].text)
        assert match, "no reset link in email"
        return match.group(1)


@dataclass
class FakeTurnstile:
    """Accepts any token unless `accept` is False."""

    accept: bool = True
    calls: int = 0

    async def verify(self, token: str | None, remote_ip: str | None) -> bool:
        self.calls += 1
        return self.accept


# --- App / client -----------------------------------------------------------------------


@pytest.fixture
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture
def turnstile() -> FakeTurnstile:
    return FakeTurnstile()


@pytest.fixture(autouse=True)
def _reset_presence():
    from app.services.presence import hub

    hub.reset()
    yield
    hub.reset()


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def app(mailer, turnstile):  # type: ignore[no-untyped-def]
    """A fresh FastAPI app per test with mail and Turnstile faked."""
    application = create_app()
    application.dependency_overrides[get_mail_sender] = lambda: mailer
    application.dependency_overrides[get_turnstile_verifier] = lambda: turnstile
    return application


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:  # type: ignore[no-untyped-def]
    """HTTP client talking to the app in-process, with a known CSRF cookie."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as ac:
        ac.cookies.set(CSRF_COOKIE_NAME, TEST_CSRF_COOKIE)
        yield ac


def with_csrf(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Form data including the CSRF token matching TEST_CSRF_COOKIE."""
    return {CSRF_FORM_FIELD: form_token(TEST_CSRF_COOKIE), **(data or {})}


async def post_form(client: AsyncClient, url: str, data: dict[str, Any] | None = None) -> Response:
    """POST a form with a valid CSRF token."""
    return await client.post(url, data=with_csrf(data))


async def login(client: AsyncClient, email: str, password: str = DEFAULT_PASSWORD) -> Response:
    """Log in through the real login form."""
    return await post_form(client, "/login", {"email": email, "password": password})


# --- Database ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
async def migrated_db() -> None:
    """Apply migrations once. Mandatory in CI (TEST_DATABASE_URL set), skipped locally if down."""
    if not await check_database():
        if os.environ.get("TEST_DATABASE_URL"):
            pytest.fail("TEST_DATABASE_URL is set but the database is unreachable")
        pytest.skip("No test database available")
    config = Config("alembic.ini")
    # env.py runs its own event loop; keep it off the test loop.
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def db(migrated_db) -> AsyncIterator[AsyncSession]:  # type: ignore[no-untyped-def]
    """A session on a clean database (tables truncated after each test)."""
    async with get_sessionmaker()() as session:
        yield session
    async with get_sessionmaker()() as cleanup:
        await cleanup.execute(text(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE"))
        await cleanup.commit()


@pytest.fixture
def make_clinic(db: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(name: str = "Cabinet Test", active: bool = True) -> Clinic:
        clinic = await clinics_repo.create_clinic(db, name)
        clinic.active = active
        await db.commit()
        return clinic

    return _make


@pytest.fixture
def make_user(db: AsyncSession):  # type: ignore[no-untyped-def]
    async def _make(
        email: str,
        full_name: str = "Nicolas Rousseau",
        clinic: Clinic | None = None,
        *,
        is_superadmin: bool = False,
        must_change_password: bool = False,
        password: str = DEFAULT_PASSWORD,
        active: bool = True,
    ) -> User:
        user = await users_repo.create_user(
            db,
            email=email,
            full_name=full_name,
            password=password,
            clinic_id=clinic.id if clinic else None,
            is_superadmin=is_superadmin,
            must_change_password=must_change_password,
        )
        user.active = active
        await db.commit()
        return user

    return _make
