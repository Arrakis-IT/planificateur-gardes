# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Regeneration email: changelog against the archived generation."""

from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select

from app.models import Assignment, OnsiteDay
from app.services.scheduler import UNASSIGNED, ScheduleRequest
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

DUPONT, RICHARD, WEBER = "Dr. Jean Dupont", "Dr. Marie Richard", "Dr. Paul Weber"
TITLE = "Changements par rapport à la planification précédente"


def apr(day: int) -> date:
    return date(2027, 4, day)


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    clinic = await make_clinic("Groupe CRT")
    for i, name in enumerate((DUPONT, RICHARD, WEBER)):
        await make_user(f"d{i}@ex.lu", name, clinic=clinic)
    await login(client, "d0@ex.lu")
    return clinic


@pytest.fixture
def plan(monkeypatch):
    """Replace the (random) scheduler with a fixed day → doctor name plan."""
    current: dict[date, str | None] = {}

    def fake_schedule(request: ScheduleRequest) -> dict[date, str]:
        code_of = {d.name: d.code for d in request.doctors}
        days = (request.period_to - request.period_from).days + 1
        result = {}
        for offset in range(days):
            day = request.period_from + timedelta(days=offset)
            name = current.get(day)
            result[day] = code_of[name] if name else UNASSIGNED
        return result

    monkeypatch.setattr("app.services.planning.generate_schedule", fake_schedule)
    return current


async def _generate(client, start: date, end: date, *, replace: bool = True) -> None:
    data = {"period_from": start.isoformat(), "period_to": end.isoformat()}
    if replace:
        data["confirm_overwrite"] = "true"
    response = await post_form(client, "/generer", data)
    assert response.status_code == 303, response.text


async def _set_onsite(db, clinic, days: set[date], on: bool) -> None:
    if on:
        db.add_all(OnsiteDay(clinic_id=clinic.id, date=d) for d in days)
    else:
        await db.execute(delete(OnsiteDay).where(OnsiteDay.date.in_(days)))
    await db.commit()


def _last_email(mailer):
    assert mailer.sent
    return mailer.sent[-1]


async def test_first_generation_has_no_changelog(client, db, team, mailer, plan):
    plan.update({apr(d): DUPONT for d in range(1, 11)})
    await _generate(client, apr(1), apr(10), replace=False)
    email = _last_email(mailer)
    assert TITLE not in email.text and TITLE not in email.html


async def test_no_overlap_no_section(client, db, team, mailer, plan):
    plan.update({apr(d): DUPONT for d in range(1, 31)})
    await _generate(client, apr(1), apr(10), replace=False)
    await _generate(client, apr(11), apr(20))
    email = _last_email(mailer)
    assert TITLE not in email.text and TITLE not in email.html


async def test_overlap_without_changes(client, db, team, mailer, plan):
    plan.update({apr(d): DUPONT for d in range(1, 31)})
    await _set_onsite(db, team, {apr(5)}, on=True)
    await _generate(client, apr(1), apr(10), replace=False)
    await _generate(client, apr(5), apr(20))
    email = _last_email(mailer)
    for part in (email.text, email.html):
        assert TITLE in part
        assert "Période précédente : du 01/04/2027 au 10/04/2027" in part
        assert "Nouvelle période : du 05/04/2027 au 20/04/2027" in part
        assert "Aucun changement sur la période partagée." in part


async def test_overlap_with_every_kind_of_change(client, db, team, mailer, plan):
    before = {
        1: DUPONT,  # only in the previous period: ignored
        3: DUPONT,  # → Richard, on-site yes → yes
        4: DUPONT,  # → Richard, no → no
        5: DUPONT,  # → Richard, no → yes
        6: DUPONT,  # → Richard, yes → no
        7: DUPONT,  # same doctor, no → yes
        8: DUPONT,  # same doctor, yes → no
        9: DUPONT,  # same doctor, yes → yes: no entry
        10: None,  # unassigned → Weber
        11: WEBER,  # Weber → unassigned
        12: WEBER,  # unchanged
    }
    plan.update({apr(d): name for d, name in before.items()})
    await _set_onsite(db, team, {apr(3), apr(6), apr(8), apr(9)}, on=True)
    await _generate(client, apr(1), apr(12), replace=False)

    # On-site settings change between the two generations.
    await _set_onsite(db, team, {apr(5), apr(7)}, on=True)
    await _set_onsite(db, team, {apr(6), apr(8)}, on=False)
    plan.clear()
    plan.update({apr(d): RICHARD for d in (3, 4, 5, 6)})
    plan.update({apr(7): DUPONT, apr(8): DUPONT, apr(9): DUPONT, apr(10): WEBER})
    plan.update({apr(11): None, apr(12): WEBER, apr(13): RICHARD})  # 13: new day, ignored
    await _generate(client, apr(3), apr(15))

    expected = [
        ("3 avril 2027", f"{DUPONT} (sur place) → {RICHARD}", True),  # on-site day: red
        ("4 avril 2027", f"{DUPONT} → {RICHARD}", False),
        ("5 avril 2027", f"{DUPONT} → {RICHARD} (sur place)", True),
        ("6 avril 2027", f"{DUPONT} (sur place) → {RICHARD}", True),
        ("7 avril 2027", f"{DUPONT} (devenu sur place)", True),
        ("8 avril 2027", f"{DUPONT} (n'est plus sur place)", True),
        ("10 avril 2027", f"non assigné → {WEBER}", False),
        ("11 avril 2027", f"{WEBER} → non assigné", False),
    ]
    email = _last_email(mailer)
    text_lines = [line for line in email.text.splitlines() if line.startswith(("- ", "! "))]
    assert text_lines == [f"{'!' if red else '-'} {day} : {entry}" for day, entry, red in expected]
    assert "Période précédente : du 01/04/2027 au 12/04/2027" in email.text
    assert "« ! »" in email.text  # legend shown when some lines are marked

    html_items = [line.strip() for line in email.html.splitlines() if "<li " in line]
    assert len(html_items) == len(expected)
    for item, (day, entry, red) in zip(html_items, expected, strict=True):
        assert f"{day} : {entry}".replace("'", "&#39;") in item
        assert ("#f8d7da" in item) is red
    for ignored in ("1er avril", "9 avril", "12 avril", "13 avril"):
        assert ignored not in email.text
    assert "(DT)" not in email.text and "Dr. Jean Dupont (JD)" not in email.text


async def test_snapshot_follows_saved_edits(client, db, team, mailer, plan):
    """A cell edited and saved after the generation is compared as last published."""
    plan.update({apr(d): DUPONT for d in range(1, 6)})
    await _set_onsite(db, team, {apr(2)}, on=True)
    await _generate(client, apr(1), apr(5), replace=False)
    toggled = await post_form(client, f"/resultats/jour/{apr(2).isoformat()}/sur-place")
    assert toggled.status_code in (200, 303)
    assert (await post_form(client, "/resultats/enregistrer")).status_code == 303
    cell = await db.scalar(select(Assignment).where(Assignment.date == apr(2)))
    await db.refresh(cell)
    assert cell.onsite is False and cell.onsite_removed is True

    await _generate(client, apr(1), apr(5))
    email = _last_email(mailer)
    assert f"! 2 avril 2027 : {DUPONT} (devenu sur place)" in email.text


async def test_legacy_generation_without_snapshot_uses_settings(client, db, team, mailer, plan):
    plan.update({apr(d): DUPONT for d in range(1, 6)})
    await _set_onsite(db, team, {apr(3)}, on=True)
    await _generate(client, apr(1), apr(5), replace=False)
    for cell in (await db.scalars(select(Assignment))).all():
        cell.onsite = None  # as generated before 0.4.0
    await db.commit()
    plan[apr(3)] = RICHARD
    await _generate(client, apr(1), apr(5))
    assert f"! 3 avril 2027 : {DUPONT} (sur place) → {RICHARD}" in _last_email(mailer).text
