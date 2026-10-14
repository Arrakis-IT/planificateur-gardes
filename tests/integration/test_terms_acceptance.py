# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Users created before 0.6.0 (accepted_terms_at NULL) must accept the terms first."""

import pytest
from sqlalchemy import func, select

from app.models import AuditLog
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

PAGE = "/acceptation-cgu"


@pytest.fixture
async def legacy_user(db, make_clinic, make_user):
    return await make_user("old@ex.lu", clinic=await make_clinic(), accepted_terms=False)


async def test_login_leads_to_the_blocking_acceptance_page(client, db, legacy_user):
    response = await login(client, "old@ex.lu")
    assert response.status_code == 303
    for url in ("/", "/conges", "/parametres", "/resultats"):
        blocked = await client.get(url)
        assert blocked.status_code == 303 and blocked.headers["location"] == PAGE, url
    page = await client.get(PAGE)
    assert page.status_code == 200
    assert 'name="accept_terms"' in page.text and "required" in page.text
    assert 'href="/cgu"' in page.text and 'href="/politique-confidentialite"' in page.text
    # No live features (SSE / heartbeat would only bounce back here) and no clinic navigation.
    assert "data-live" not in page.text and 'href="/conges"' not in page.text
    assert 'href="/parametres"' not in page.text and 'action="/logout"' in page.text
    assert (await client.get("/cgu")).status_code == 200  # the terms stay readable


async def test_refused_without_the_checkbox(client, db, legacy_user):
    await login(client, "old@ex.lu")
    response = await post_form(client, PAGE, {})
    assert response.status_code == 400 and "Vous devez accepter" in response.text
    await db.refresh(legacy_user)
    assert legacy_user.accepted_terms_at is None
    assert (await client.get("/")).headers["location"] == PAGE


async def test_accepting_lets_the_user_in_and_is_audited(client, db, legacy_user):
    await login(client, "old@ex.lu")
    response = await post_form(client, PAGE, {"accept_terms": "true"})
    assert response.status_code == 303 and response.headers["location"] == "/"
    assert (await client.get("/")).status_code == 200
    await db.refresh(legacy_user)
    assert legacy_user.accepted_terms_at is not None
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "terms_accepted_retroactive"))
    assert entry.user_id == legacy_user.id and entry.target_id == str(legacy_user.id)
    # Once accepted: the page sends home, next logins go straight in.
    assert (await client.get(PAGE)).headers["location"] == "/"
    await post_form(client, "/logout")
    assert (await login(client, "old@ex.lu")).headers["location"] == "/"
    assert (await client.get("/")).status_code == 200


async def test_logout_without_accepting_asks_again_next_time(client, db, legacy_user):
    await login(client, "old@ex.lu")
    assert (await post_form(client, "/logout")).headers["location"] == "/login"
    await login(client, "old@ex.lu")
    assert (await client.get("/")).headers["location"] == PAGE
    count = await db.scalar(
        select(func.count()).where(AuditLog.action == "terms_accepted_retroactive")
    )
    assert count == 0


async def test_users_who_accepted_are_not_bothered(client, db, make_clinic, make_user):
    await make_user("new@ex.lu", clinic=await make_clinic())
    await login(client, "new@ex.lu")
    assert (await client.get("/")).status_code == 200
