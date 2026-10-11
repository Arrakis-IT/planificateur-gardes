# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Dashboard, week and month views."""

from datetime import date

import pytest

from app.models import OnsiteDay
from app.repositories import scheduling as repo
from app.services import calendar as cal
from tests.conftest import login

pytestmark = pytest.mark.db

TODAY = date(2027, 5, 16)  # Sunday before Whit Monday


@pytest.fixture
def fixed_today(monkeypatch):
    monkeypatch.setattr(cal, "today_local", lambda now=None: TODAY)
    return TODAY


@pytest.fixture
async def clinic_with_plan(db, make_clinic, make_user):
    clinic = await make_clinic()
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    await repo.create_generation(
        db,
        clinic.id,
        date(2027, 5, 1),
        date(2027, 5, 31),
        nr.id,
        {date(2027, 5, d): (nr.id if d % 2 else dt.id) for d in range(1, 32)}
        | {date(2027, 5, 18): None},
    )
    db.add(OnsiteDay(clinic_id=clinic.id, date=date(2027, 5, 17)))
    await db.commit()
    return clinic, nr, dt


async def test_dashboard_today_and_tomorrow(client, db, fixed_today, clinic_with_plan):
    await login(client, "nr@ex.lu")
    page = await client.get("/")
    assert page.status_code == 200
    text = page.text
    assert "dimanche 16 mai 2027" in text and "lundi 17 mai 2027" in text
    # 16 May → DT (even day), 17 May → NR; Whit Monday is a holiday and on-site.
    assert "Dominique Thill" in text and "Nicolas Rousseau" in text
    assert "Lundi de Pentecôte" in text and "Sur place" in text and "Week-end" in text


async def test_dashboard_without_planning(client, db, make_clinic, make_user, fixed_today):
    clinic = await make_clinic()
    await make_user("nr@ex.lu", clinic=clinic)
    await login(client, "nr@ex.lu")
    assert "Aucune planification pour ce jour" in (await client.get("/")).text


async def test_week_view_and_navigation(client, db, fixed_today, clinic_with_plan):
    await login(client, "nr@ex.lu")
    page = await client.get("/semaine?date=2027-05-19")
    assert page.status_code == 200
    assert page.text.count('class="week-row') == 7
    assert "Aucun médecin disponible" in page.text  # 18 May unassigned
    assert "/semaine?date=2027-05-10" in page.text and "/semaine?date=2027-05-24" in page.text
    assert (await client.get("/semaine?date=nonsense")).status_code == 200


async def test_month_view(client, db, fixed_today, clinic_with_plan):
    await login(client, "nr@ex.lu")
    page = await client.get("/mois?mois=2027-05")
    assert page.status_code == 200
    assert page.text.count('class="cell-doctor"') == 30  # 31 days - 1 unassigned
    assert 'class="cell-doctor xx"' in page.text
    assert "mois=2027-04" in page.text and "mois=2027-06" in page.text
    outside = await client.get("/mois?mois=2027-07")
    assert outside.text.count('class="cell-doctor none"') == 31
