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
