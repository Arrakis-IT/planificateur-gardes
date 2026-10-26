# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""/generer → /resultats."""

from datetime import date, timedelta

import pytest
from sqlalchemy import func, select

from app.models import (
    Assignment,
    AuditLog,
    DesiderataConflict,
    DesiderataDay,
    Generation,
    Holiday,
    OnsiteDay,
)
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

PERIOD = {"period_from": "2027-05-01", "period_to": "2027-05-31"}


@pytest.fixture
async def clinic_team(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    mc = await make_user("mc@ex.lu", "Marie Curie", clinic=clinic)
    await make_user("old@ex.lu", "Ancien Médecin", clinic=clinic, active=False)
    for offset in range(5):
        db.add(
            Holiday(user_id=nr.id, clinic_id=clinic.id, date=date(2027, 5, 10) + timedelta(offset))
        )
    db.add(OnsiteDay(clinic_id=clinic.id, date=date(2027, 5, 12)))
    await db.commit()
    await login(client, "nr@ex.lu")
    return clinic, nr, dt, mc


async def test_generate_page_defaults_and_doctor_counts(client, db, clinic_team, monkeypatch):
    from app.services import calendar as cal

    monkeypatch.setattr(cal, "today_local", lambda now=None: date(2026, 10, 14))
    page = await client.get("/generer")
    assert page.status_code == 200
    assert 'value="2026-10-14"' in page.text and 'value="2027-04-30"' in page.text
    default_fragment = await client.get("/generer/medecins")
    assert "199 jours" in default_fragment.text  # 14/10/2026 → 30/04/2027
    assert "Ancien Médecin" not in page.text  # inactive users don't take part
    fragment = await client.get("/generer/medecins", params=PERIOD)
    assert "31 jours" in fragment.text and "1 jour sur place" in fragment.text
    assert '<td class="num">5</td>' in fragment.text  # NR's vacation days in May


async def test_generate_creates_active_generation(client, db, clinic_team):
    _, nr, *_ = clinic_team
    response = await post_form(client, "/generer", PERIOD)
    generation = await db.scalar(select(Generation))
    assert response.status_code == 303
    assert response.headers["location"] == f"/resultats?generation={generation.id}"
    assert generation.is_active and generation.generated_by == nr.id
    assignments = (await db.scalars(select(Assignment))).all()
    assert len(assignments) == 31
    on_vacation = {date(2027, 5, 10) + timedelta(i) for i in range(5)}
    assert not [a for a in assignments if a.user_id == nr.id and a.date in on_vacation]
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "generation_created"))
    assert entry.meta["doctors"] == 3 and entry.meta["period_to"] == "2027-05-31"

    results = await client.get("/resultats")
    assert results.status_code == 200
    assert "Planification du 01/05/2027 au 31/05/2027" in results.text
    assert results.text.count('class="cell-doctor"') == 31
    assert "Généré" in results.text and "Nicolas Rousseau" in results.text
    filtered = await client.get("/resultats?medecin=NR")
    assert f'filter-chip active" href="/resultats?generation={generation.id}&amp;medecin=NR"' in (
        filtered.text
    )
    assert "filtered-out" in filtered.text


async def test_regenerate_requires_confirmation_and_archives(client, db, clinic_team):
    await post_form(client, "/generer", PERIOD)
    refused = await post_form(client, "/generer", PERIOD)
    assert refused.status_code == 409 and "Chevauchement avec une planification active" in (
        refused.text
    )
    assert "Attention : cette nouvelle génération inclut les mois" in refused.text
    assert await db.scalar(select(func.count(Generation.id))) == 1

    ok = await post_form(client, "/generer", PERIOD | {"overlap_mode": "recalc"})
    assert ok.status_code == 303
    generations = (await db.scalars(select(Generation).order_by(Generation.generated_at))).all()
    assert [g.is_active for g in generations] == [False, True]
    assert generations[0].archived_at is not None
    assert await db.scalar(select(func.count(Assignment.id))) == 62  # archived rows kept


@pytest.mark.parametrize(
    ("period", "message"),
    [
        ({"period_from": "2027-05-31", "period_to": "2027-05-01"}, "précéder"),
        ({"period_from": "2027-05-01", "period_to": "2027-05-01"}, "précéder"),
        ({"period_from": "2027-01-01", "period_to": "2028-12-31"}, "dépasser"),
    ],
)
async def test_invalid_periods(client, db, clinic_team, period, message):
    response = await post_form(client, "/generer", period)
    assert response.status_code == 400 and message in response.text
    assert await db.scalar(select(func.count(Generation.id))) == 0


async def test_unassigned_days_stored_as_null(client, db, clinic_team):
    clinic, _, dt, mc = clinic_team
    for user in (dt, mc):
        db.add(Holiday(user_id=user.id, clinic_id=clinic.id, date=date(2027, 5, 11)))
    await db.commit()  # 11 May: NR already away → nobody available
    await post_form(client, "/generer", PERIOD)
    xx = await db.scalar(select(Assignment).where(Assignment.date == date(2027, 5, 11)))
    assert xx.user_id is None
    results = await client.get("/resultats")
    assert "1 jour(s) sans médecin disponible" in results.text


async def test_results_without_generation(client, db, clinic_team):
    page = await client.get("/resultats")
    assert page.status_code == 200 and "Aucune planification générée" in page.text


async def test_generation_avoids_desiderata_and_flags_forced_ones(client, db, clinic_team):
    clinic, nr, dt, mc = clinic_team
    avoided, forced = date(2027, 5, 19), date(2027, 5, 20)
    db.add(DesiderataDay(user_id=nr.id, clinic_id=clinic.id, date=avoided))
    for user in (nr, dt, mc):
        db.add(DesiderataDay(user_id=user.id, clinic_id=clinic.id, date=forced))
    await db.commit()
    await post_form(client, "/generer", PERIOD)
    assignments = {a.date: a for a in (await db.scalars(select(Assignment))).all()}
    assert assignments[avoided].user_id != nr.id
    assert assignments[avoided].desiderata_conflict is None
    assert assignments[forced].user_id is not None
    assert assignments[forced].desiderata_conflict == DesiderataConflict.GENERATED
    assert [d for d, a in assignments.items() if a.desiderata_violation] == [forced]
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "generation_created"))
    assert entry.meta["desiderata_conflicts"] == 1
