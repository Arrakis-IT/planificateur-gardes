# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Personal iCal feed: token lifecycle, VCALENDAR content, access rules."""

import logging
import re
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from app.models import Assignment, AuditLog, OnsiteDay
from app.repositories import scheduling as scheduling_repo
from app.services.ical import Duty, build_calendar
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

APR = [date(2027, 4, d) for d in range(1, 6)]


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    clinic = await make_clinic("Groupe CRT")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    await login(client, "nr@ex.lu")
    return clinic, nr, dt


async def _generation(db, clinic, nr, dt) -> None:
    """1, 3, 5 April to NR (3: on-site; 5: on-site removed in the results), 2, 4 to DT."""
    owners = {d: (nr.id if d.day % 2 else dt.id) for d in APR}
    generation = await scheduling_repo.create_generation(
        db, clinic.id, APR[0], APR[-1], nr.id, owners
    )
    db.add_all(OnsiteDay(clinic_id=clinic.id, date=d) for d in (APR[2], APR[4]))
    cell = await db.scalar(
        select(Assignment).where(
            Assignment.generation_id == generation.id, Assignment.date == APR[4]
        )
    )
    cell.onsite_removed = True
    await db.commit()


async def _token(client, db, user) -> str:
    response = await post_form(client, "/parametres/calendrier")
    assert response.status_code == 303
    await db.refresh(user)
    return user.ical_token


def _events(body: str) -> list[dict[str, str]]:
    events = []
    for block in re.findall(r"BEGIN:VEVENT\r\n(.*?)END:VEVENT\r\n", body, re.S):
        fields = {}
        for line in block.split("\r\n"):
            if line:
                key, _, value = line.partition(":")
                fields[key] = value
        events.append(fields)
    return events


async def test_token_is_created_on_demand_and_audited(client, db, team):
    _, nr, _ = team
    page = (await client.get("/parametres")).text
    assert "Mon calendrier" in page and "Générer mon lien d'abonnement" in page
    assert nr.ical_token is None
    token = await _token(client, db, nr)
    assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token)  # secrets.token_urlsafe(32)
    page = (await client.get("/parametres")).text
    assert f'value="http://testserver/ical/{token}" readonly' in page
    assert 'data-copy-target="ical-url"' in page and "Régénérer le lien" in page
    entry = await db.scalar(select(AuditLog).where(AuditLog.action.like("ical_token%")))
    assert entry.action == "ical_token_generated" and entry.user_id == nr.id


async def test_feed_content(client, db, team):
    clinic, nr, dt = team
    await _generation(db, clinic, nr, dt)
    token = await _token(client, db, nr)
    await post_form(client, "/logout")  # the feed needs no session
    response = await client.get(f"/ical/{token}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/calendar; charset=utf-8"
    assert response.headers["cache-control"] == "public, max-age=3600"
    body = response.text
    assert body.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n") and body.endswith(
        "END:VCALENDAR\r\n"
    )
    for line in (
        "PRODID:-//Arrakis IT Services//Planificateur des gardes//FR",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Gardes Groupe CRT",
        "X-WR-TIMEZONE:Europe/Luxembourg",
    ):
        assert f"\r\n{line}\r\n" in body, line
    events = _events(body)
    assert [e["DTSTART;VALUE=DATE"] for e in events] == ["20270401", "20270403", "20270405"]
    # All-day events: dates only, exclusive end on the next day.
    assert [e["DTEND;VALUE=DATE"] for e in events] == ["20270402", "20270404", "20270406"]
    assert all("T" not in e["DTSTART;VALUE=DATE"] for e in events)
    assert [e["SUMMARY"] for e in events] == ["Garde", "Garde (sur place)", "Garde"]
    assert [e["UID"] for e in events] == [
        f"2027-04-0{d}-{nr.id}@gardes.arrakis.lu" for d in (1, 3, 5)
    ]
    assert all(e["DESCRIPTION"] == "Garde planifiée dans le groupe Groupe CRT." for e in events)
    assert all(re.fullmatch(r"\d{8}T\d{6}Z", e["DTSTAMP"]) for e in events)
    # Stable UIDs: a second read gives the same events.
    assert _events((await client.get(f"/ical/{token}")).text) == events


async def test_unknown_token_is_404(client, db, team):
    assert (await client.get("/ical/not-a-token")).status_code == 404
    assert (await client.get(f"/ical/{uuid.uuid4()}")).status_code == 404


async def test_no_active_generation_gives_an_empty_calendar(client, db, team):
    _, nr, _ = team
    token = await _token(client, db, nr)
    response = await client.get(f"/ical/{token}")
    assert response.status_code == 200
    assert "BEGIN:VCALENDAR" in response.text and "BEGIN:VEVENT" not in response.text
    assert response.text.endswith("END:VCALENDAR\r\n")


async def test_regenerating_invalidates_the_previous_link(client, db, team):
    _, nr, _ = team
    first = await _token(client, db, nr)
    second = await _token(client, db, nr)
    assert first != second
    assert (await client.get(f"/ical/{first}")).status_code == 404
    assert (await client.get(f"/ical/{second}")).status_code == 200
    actions = (
        await db.scalars(
            select(AuditLog.action)
            .where(AuditLog.action.like("ical_token%"))
            .order_by(AuditLog.created_at)
        )
    ).all()
    assert actions == ["ical_token_generated", "ical_token_regenerated"]


async def test_inactive_user_feed_is_404(client, db, team):
    _, nr, _ = team
    token = await _token(client, db, nr)
    nr.active = False
    await db.commit()
    assert (await client.get(f"/ical/{token}")).status_code == 404


async def test_only_clinic_users_get_a_link(client, db, make_user):
    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    await login(client, "root@ex.lu")
    assert "Mon calendrier" not in (await client.get("/parametres")).text
    assert (await post_form(client, "/parametres/calendrier")).status_code == 403


async def test_feed_rate_limit_is_lax(client, db, team):
    _, nr, _ = team
    token = await _token(client, db, nr)
    codes = [(await client.get(f"/ical/{token}")).status_code for _ in range(61)]
    assert codes[:60] == [200] * 60 and codes[60] == 429


def test_long_lines_are_folded_and_text_escaped():
    body = build_calendar(
        "Groupe médical du Parc, Kirchberg; Luxembourg-Ville et environs proches",
        uuid.uuid4(),
        [Duty(date(2027, 4, 1), False)],
        datetime(2027, 1, 1, tzinfo=UTC),
    )
    assert all(len(line.encode()) <= 75 for line in body.split("\r\n"))
    unfolded = body.replace("\r\n ", "")
    assert "Groupe médical du Parc\\, Kirchberg\\; Luxembourg-Ville" in unfolded


def test_feed_token_is_masked_in_the_access_log():
    from app.utils.logging import QuietPathsFilter

    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", "/ical/s3cr3t-token", "1.1", 200), None,
    )  # fmt: skip
    assert QuietPathsFilter().filter(record) is True
    assert "s3cr3t" not in record.getMessage() and "/ical/[token]" in record.getMessage()
