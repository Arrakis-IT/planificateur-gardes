# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Uncommitted work must never persist when a request fails."""

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.dependencies import DbSession
from app.models import Clinic

pytestmark = pytest.mark.db


class BoomError(RuntimeError):
    """Unhandled error raised by the test route."""


@pytest.fixture
async def failing_client(app):
    """App with test-only routes that write without committing, then crash."""

    @app.get("/__test__/add-then-crash")
    async def add_then_crash(db: DbSession) -> None:
        db.add(Clinic(name="Ghost (add)", slug="ghost-add"))
        raise BoomError

    @app.get("/__test__/flush-then-crash")
    async def flush_then_crash(db: DbSession) -> None:
        db.add(Clinic(name="Ghost (flush)", slug="ghost-flush"))
        await db.flush()  # INSERT sent to PostgreSQL inside the open transaction
        raise BoomError

    @app.get("/__test__/commit-then-crash")
    async def commit_then_crash(db: DbSession) -> None:
        db.add(Clinic(name="Committed", slug="committed"))
        await db.commit()
        raise BoomError

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.mark.parametrize("route", ["add-then-crash", "flush-then-crash"])
async def test_unhandled_exception_discards_uncommitted_changes(failing_client, db, route):
    response = await failing_client.get(f"/__test__/{route}")
    assert response.status_code == 500
    remaining = (await db.scalars(select(Clinic.slug).where(Clinic.slug.like("ghost%")))).all()
    assert remaining == []


async def test_control_committed_changes_survive_the_crash(failing_client, db):
    """Control case: proves the assertion above would detect a persisted row."""
    response = await failing_client.get("/__test__/commit-then-crash")
    assert response.status_code == 500
    assert (await db.scalars(select(Clinic.slug))).all() == ["committed"]


# --- Error pages with expired ORM objects (0.13.1) --------------------------------------------


@pytest.fixture
async def logged_failing_client(app, make_user):
    """Logged-in superadmin on test routes that roll the session back (which expires the
    loaded user) and then fail or render."""
    from app.dependencies import OptionalAuth
    from app.middleware.csrf import CSRF_COOKIE_NAME
    from app.templating import render
    from tests.conftest import TEST_CSRF_COOKIE, login

    @app.get("/__test__/rollback-then-crash")
    async def rollback_then_crash(db: DbSession, auth: OptionalAuth) -> None:
        await db.rollback()
        raise BoomError

    @app.get("/__test__/crash")
    async def crash(auth: OptionalAuth) -> None:
        raise BoomError

    @app.get("/__test__/rollback-then-render")
    async def rollback_then_render(request: Request, db: DbSession, auth: OptionalAuth):  # type: ignore[no-untyped-def]
        await db.rollback()
        return render(
            request, "errors/error.html", {"status_code": 409, "message": "Conflit."}, 409
        )

    await make_user("root@ex.lu", "Root Admin", is_superadmin=True)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        client.cookies.set(CSRF_COOKIE_NAME, TEST_CSRF_COOKIE)
        assert (await login(client, "root@ex.lu")).status_code == 303
        yield client


async def test_any_500_renders_the_error_page(logged_failing_client):
    response = await logged_failing_client.get("/__test__/crash")
    assert response.status_code == 500
    assert "<h1>Erreur 500</h1>" in response.text
    assert "Une erreur inattendue s&#39;est produite." in response.text
    assert 'href="/">Retour' in response.text
    assert "Déconnexion" in response.text  # full layout: the user is still loaded


async def test_500_after_a_rollback_renders_without_missing_greenlet(logged_failing_client):
    response = await logged_failing_client.get("/__test__/rollback-then-crash")
    assert response.status_code == 500
    assert "<h1>Erreur 500</h1>" in response.text
    assert "MissingGreenlet" not in response.text


async def test_page_after_a_rollback_renders_as_anonymous(logged_failing_client):
    response = await logged_failing_client.get("/__test__/rollback-then-render")
    assert response.status_code == 409 and "Conflit." in response.text
    assert "Déconnexion" not in response.text  # no auth context: nothing lazy-loaded
