# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Uncommitted work must never persist when a request fails."""

import pytest
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
