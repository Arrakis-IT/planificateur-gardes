# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Tests for the /healthz endpoint."""

import os

import pytest

from app.db import check_database


async def test_healthz_ok_when_db_reachable(app, client):
    app.dependency_overrides[check_database] = lambda: True
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}


async def test_healthz_503_when_db_down(app, client):
    app.dependency_overrides[check_database] = lambda: False
    response = await client.get("/healthz")
    assert response.status_code == 503
    assert response.json()["db"] == "error"


@pytest.mark.db
async def test_healthz_against_real_database(client):
    """Round-trip to PostgreSQL. Mandatory in CI (TEST_DATABASE_URL set), optional locally."""
    if not await check_database():
        if os.environ.get("TEST_DATABASE_URL"):
            pytest.fail("TEST_DATABASE_URL is set but the database is unreachable")
        pytest.skip("No test database available")
    response = await client.get("/healthz")
    assert response.status_code == 200


# --- /health (liveness for UptimeRobot) ------------------------------------------------


async def test_health_ok_without_session_or_database(app, client):
    from app.db import get_session

    def unavailable():  # /health must not touch the database at all
        raise AssertionError("database used")

    app.dependency_overrides[check_database] = unavailable
    app.dependency_overrides[get_session] = unavailable
    response = await client.get("/health")  # anonymous client: no session cookie
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["content-type"] == "application/json"
    head = await client.head("/health")
    assert head.status_code == 200 and head.content == b""


async def test_health_is_not_rate_limited(client):
    responses = [await client.get("/health") for _ in range(50)]
    assert {r.status_code for r in responses} == {200}


def test_health_pings_are_left_out_of_the_access_log():
    import logging

    from app.utils.logging import QuietPathsFilter

    def record(path: str) -> logging.LogRecord:
        return logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1,
            '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", path, "1.1", 200), None,
        )  # fmt: skip

    quiet = QuietPathsFilter()
    assert quiet.filter(record("/health")) is False
    assert quiet.filter(record("/health?probe=1")) is False
    assert quiet.filter(record("/healthz")) is True
    assert quiet.filter(record("/resultats")) is True
    assert any(isinstance(f, QuietPathsFilter) for f in logging.getLogger("uvicorn.access").filters)
