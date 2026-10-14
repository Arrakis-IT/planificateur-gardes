# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""/conges and /sur-place."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Holiday, OnsiteDay
from tests.conftest import login, post_form, with_csrf

pytestmark = pytest.mark.db

HTMX = {"HX-Request": "true"}


@pytest.fixture
async def doctor(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    await login(client, "nr@ex.lu")
    return user


async def _vacations(db, user):
    return set((await db.scalars(select(Holiday.date).where(Holiday.user_id == user.id))).all())


async def test_vacation_page_shows_eight_months(client, db, doctor):
    page = await client.get("/conges?debut=2027-01")
    assert page.status_code == 200
    assert "janvier – août 2027" in page.text
    assert page.text.count('class="mini-month"') == 8
    assert 'value="2026-12-31"' not in page.text and 'value="2027-01-01"' in page.text
    assert 'value="2027-08-31"' in page.text and 'value="2027-09-01"' not in page.text
    # One month per step.
    assert 'href="/conges?debut=2026-12"' in page.text and "Précédent<" in page.text
    assert 'href="/conges?debut=2027-02"' in page.text and ">Suivant" in page.text


@pytest.mark.parametrize(
    "url", ["/conges", "/sur-place", "/conges?debut=nope", "/sur-place?semestre=2027-1"]
)
async def test_calendar_pages_start_at_current_month(client, db, doctor, monkeypatch, url):
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: date(2026, 10, 14))
    page = await client.get(url)
    assert page.status_code == 200
    assert "octobre 2026 – mai 2027" in page.text
    assert page.text.count('class="mini-month"') == 8
    assert 'value="2026-10-01"' in page.text and 'value="2027-05-31"' in page.text
    assert 'name="debut" value="2026-10"' in page.text
    assert "semestre" not in page.text.lower()


async def test_toggle_vacation_htmx_and_plain(client, db, doctor):
    fragment = await client.post(
        "/conges/toggle",
        data=with_csrf({"date": "2027-02-15", "debut": "2027-01"}),
        headers=HTMX,
    )
    assert fragment.status_code == 200
    assert fragment.text.lstrip().startswith('<div id="vacation-calendar">')
    assert "<html" not in fragment.text
    assert await _vacations(db, doctor) == {date(2027, 2, 15)}

    plain = await post_form(client, "/conges/toggle", {"date": "2027-02-15", "debut": "2027-01"})
    assert plain.status_code == 303 and plain.headers["location"] == "/conges?debut=2027-01"
    assert await _vacations(db, doctor) == set()
    assert (
        await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action.like("vacation%")))
        == 2
    )


async def test_vacation_range_add_and_remove(client, db, doctor):
    await post_form(client, "/conges/toggle", {"date": "2027-04-13"})
    added = await post_form(
        client, "/conges/periode", {"start": "2027-04-12", "end": "2027-04-16", "action": "add"}
    )
    assert added.status_code == 303
    assert len(await _vacations(db, doctor)) == 5
    await post_form(
        client, "/conges/periode", {"start": "2027-04-14", "end": "2027-04-20", "action": "remove"}
    )
    assert await _vacations(db, doctor) == {date(2027, 4, 12), date(2027, 4, 13)}
    bad = await post_form(client, "/conges/periode", {"start": "2027-04-20", "end": "2027-04-10"})
    assert bad.status_code == 303
    assert "précéder" in (await client.get(bad.headers["location"])).text


async def test_onsite_toggle(client, db, doctor):
    response = await post_form(client, "/sur-place/toggle", {"date": "2027-03-10"})
    assert response.status_code == 303
    rows = (await db.scalars(select(OnsiteDay))).all()
    assert [(r.date, r.created_by) for r in rows] == [(date(2027, 3, 10), doctor.id)]
    fragment = await client.post(
        "/sur-place/toggle",
        data=with_csrf({"date": "2027-03-10", "debut": "2027-01"}),
        headers=HTMX,
    )
    assert 'id="onsite-calendar"' in fragment.text
    assert await db.scalar(select(func.count(OnsiteDay.id))) == 0


async def test_invalid_date_rejected(client, db, doctor):
    response = await post_form(client, "/conges/toggle", {"date": "not-a-date"})
    assert response.status_code == 400


async def test_superadmin_cannot_use_clinic_pages(client, db, make_user):
    await make_user("admin@ex.lu", "Juan Admin", is_superadmin=True)
    await login(client, "admin@ex.lu")
    for url in ("/conges", "/sur-place", "/generer", "/resultats", "/semaine", "/mois"):
        response = await client.get(url)
        assert response.status_code == 303 and response.headers["location"] == "/admin"
