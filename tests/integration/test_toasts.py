# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Toasts: flash messages and HTMX lock buttons (no page reload, so no scroll jump).

The scroll position itself needs a browser: see tests/e2e/test_toast_scroll.py.
"""

import json
from datetime import date, timedelta

import pytest

from app.repositories import scheduling as scheduling_repo
from app.services.locks import OUT_OF_RANGE
from tests.conftest import login, post_form, with_csrf

pytestmark = pytest.mark.db

HTMX = {"HX-Request": "true"}


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: date(2026, 10, 14))


@pytest.fixture
async def group_admin(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    admin = await make_user("admin-doc@ex.lu", "Dr. Jean Dupont", clinic=clinic)
    admin.is_group_admin = True
    start, end = date(2026, 10, 1), date(2027, 6, 30)
    days = {start + timedelta(days=i): admin.id for i in range((end - start).days + 1)}
    await scheduling_repo.create_generation(db, clinic.id, start, end, admin.id, days)
    await db.commit()
    await login(client, "admin-doc@ex.lu")
    return clinic


def _toast(response) -> dict:
    return json.loads(response.headers["HX-Trigger"])["toast"]


async def test_htmx_lock_swaps_the_list_and_sends_a_toast(client, db, group_admin):
    response = await client.post(
        "/parametres/verrous", data=with_csrf({"month": "2027-01", "action": "lock"}), headers=HTMX
    )
    assert response.status_code == 200
    assert response.text.lstrip().startswith('<section id="month-locks">')
    assert "<html" not in response.text  # fragment only: the page is not reloaded
    assert "🔒 Verrouillé" in response.text
    assert _toast(response) == {"message": "Mois janvier 2027 verrouillé.", "level": "success"}
    assert response.headers["HX-Trigger"].isascii()  # header-safe JSON

    unlock = await client.post(
        "/parametres/verrous",
        data=with_csrf({"month": "2027-01", "action": "unlock"}),
        headers=HTMX,
    )
    assert _toast(unlock)["message"] == "Mois janvier 2027 déverrouillé."


async def test_htmx_lock_error_is_a_400_error_toast(client, db, group_admin):
    response = await client.post(
        "/parametres/verrous", data=with_csrf({"month": "2027-09", "action": "lock"}), headers=HTMX
    )
    assert response.status_code == 400
    assert _toast(response) == {"message": OUT_OF_RANGE, "level": "error"}


async def test_lock_buttons_are_htmx_forms(client, db, group_admin):
    page = (await client.get("/parametres")).text
    assert 'hx-post="/parametres/verrous" hx-target="#month-locks" hx-swap="outerHTML"' in page


async def test_flash_messages_render_as_toasts(client, db, group_admin):
    response = await post_form(
        client, "/parametres/verrous", {"month": "2027-02", "action": "lock"}
    )
    assert response.status_code == 303  # without JavaScript: redirect + flash
    page = (await client.get(response.headers["location"])).text
    assert '<div id="toasts" class="toasts">' in page
    assert (
        'class="toast toast-success" role="status">Mois février 2027 verrouillé.</output>' in page
    )
    assert 'class="alert alert-success"' not in page


async def test_superadmin_lock_buttons_use_toasts_too(app, db, group_admin, make_user):
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import TEST_CSRF_COOKIE

    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as root:
        root.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(root, "root@ex.lu")
        url = f"/admin/clinic/{group_admin.id}/verrous"
        ok = await root.post(
            url, data=with_csrf({"month": "2027-03", "action": "lock"}), headers=HTMX
        )
        assert ok.status_code == 200 and ok.text.lstrip().startswith('<section id="month-locks">')
        assert _toast(ok)["message"] == "Mois mars 2027 verrouillé."
        bad = await root.post(
            url, data=with_csrf({"month": "2025-01", "action": "lock"}), headers=HTMX
        )
        assert bad.status_code == 400 and _toast(bad)["level"] == "error"
        page = await post_form(root, url, {"month": "2027-09", "action": "lock"})
        assert page.status_code == 400 and OUT_OF_RANGE in page.text  # no-JS fallback
