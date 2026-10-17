# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""/desiderata, desiderata conflicts in the calendar and the results, iCal unchanged."""

from datetime import date

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, DesiderataConflict, DesiderataDay, Holiday, MonthLock
from app.repositories import scheduling as scheduling_repo
from tests.conftest import login, post_form, with_csrf

pytestmark = pytest.mark.db

HTMX = {"HX-Request": "true"}
APR = [date(2027, 4, d) for d in range(1, 6)]


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    clinic = await make_clinic("Groupe CRT")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    await login(client, "nr@ex.lu")
    return clinic, nr, dt


async def _desiderata(db, user) -> set[date]:
    stmt = select(DesiderataDay.date).where(DesiderataDay.user_id == user.id)
    return set((await db.scalars(stmt)).all())


async def _generation(db, clinic, nr, dt) -> None:
    """April 1–5 alternately NR / DT; NR's 1st forced by the generator, DT's 2nd manual."""
    owners = {d: (nr.id if d.day % 2 else dt.id) for d in APR}
    conflicts = {APR[0]: DesiderataConflict.GENERATED, APR[1]: DesiderataConflict.MANUAL}
    await scheduling_repo.create_generation(
        db, clinic.id, APR[0], APR[-1], nr.id, owners, desiderata_conflicts=conflicts
    )
    await db.commit()


# --- /desiderata ------------------------------------------------------------------------------


async def test_page_shows_eight_months_and_navigation(client, db, team):
    page = await client.get("/desiderata?debut=2027-01")
    assert page.status_code == 200
    assert "Mes desiderata" in page.text and "janvier – août 2027" in page.text
    assert page.text.count('class="mini-month"') == 8
    assert 'href="/desiderata?debut=2027-09"' in page.text
    assert 'href="/desiderata"' in page.text  # navigation link


async def test_toggle_htmx_and_plain_audited(client, db, team):
    _, nr, _ = team
    fragment = await client.post(
        "/desiderata/toggle",
        data=with_csrf({"date": "2027-02-15", "debut": "2027-01"}),
        headers=HTMX,
    )
    assert fragment.status_code == 200
    assert fragment.text.lstrip().startswith('<div id="desiderata-calendar">')
    assert 'class="cal-day in-range desiderata"' in fragment.text
    assert "1 jour de desiderata sur cette période." in fragment.text
    assert await _desiderata(db, nr) == {date(2027, 2, 15)}
    assert await db.scalar(select(func.count(Holiday.id))) == 0  # not a vacation

    plain = await post_form(
        client, "/desiderata/toggle", {"date": "2027-02-15", "debut": "2027-01"}
    )
    assert plain.status_code == 303 and plain.headers["location"] == "/desiderata?debut=2027-01"
    assert await _desiderata(db, nr) == set()
    actions = (
        await db.scalars(
            select(AuditLog.action).where(AuditLog.action.like("desiderata%")).order_by(AuditLog.id)
        )
    ).all()
    assert sorted(actions) == ["desiderata_added", "desiderata_removed"]


async def test_range_add_and_remove(client, db, team):
    _, nr, _ = team
    await post_form(
        client, "/desiderata/periode", {"start": "2027-04-12", "end": "2027-04-16", "action": "add"}
    )
    assert len(await _desiderata(db, nr)) == 5
    removed = await post_form(
        client,
        "/desiderata/periode",
        {"start": "2027-04-14", "end": "2027-04-20", "action": "remove"},
    )
    assert (
        "3 jour(s) de desiderata retiré(s)." in (await client.get(removed.headers["location"])).text
    )
    assert await _desiderata(db, nr) == {date(2027, 4, 12), date(2027, 4, 13)}
    meta = await db.scalar(
        select(AuditLog.meta).where(AuditLog.action == "desiderata_range_removed")
    )
    assert meta == {"start": "2027-04-14", "end": "2027-04-20", "days": 3}


async def test_each_doctor_sees_only_their_own(app, client, db, team):
    clinic, _, dt = team
    db.add(DesiderataDay(user_id=dt.id, clinic_id=clinic.id, date=date(2027, 2, 15)))
    await db.commit()
    page = await client.get("/desiderata?debut=2027-01")
    assert "0 jour de desiderata" in page.text
    assert "in-range desiderata" not in page.text


async def test_locked_month_refused(client, db, team):
    clinic, nr, _ = team
    db.add(MonthLock(clinic_id=clinic.id, month=date(2027, 2, 1)))
    await db.commit()
    for url, data in (
        ("/desiderata/toggle", {"date": "2027-02-15"}),
        ("/desiderata/periode", {"start": "2027-01-30", "end": "2027-02-02"}),
    ):
        response = await post_form(client, url, data)
        assert response.status_code == 403 and "verrouillé" in response.text, url
    assert await _desiderata(db, nr) == set()


# --- Calendar and results -----------------------------------------------------------------------


async def test_conflicts_striped_with_tooltip_in_calendar(client, db, team):
    clinic, nr, dt = team
    await _generation(db, clinic, nr, dt)
    page = (await client.get("/mois?mois=2027-04")).text
    assert page.count(" desiderata-conflict") == 2
    assert page.count('class="conflict-icon"') == 2
    assert (
        "Nicolas Rousseau avait demandé à ne pas être de garde ce jour (desiderata). "
        "Aucune alternative disponible." in page
    )
    assert (
        "Dominique Thill avait demandé à ne pas être de garde ce jour (desiderata). "
        "Affectation manuelle." in page
    )
    assert 'id="jour-2027-04-03"' in page  # anchors of the conflicts panel
    assert "Desiderata non respectée" in page  # legend


async def test_results_list_conflicts_with_anchor(client, db, team):
    clinic, nr, dt = team
    await _generation(db, clinic, nr, dt)
    page = (await client.get("/resultats")).text
    assert "Conflits avec desiderata" in page
    panel = page.split('id="conflits-desiderata"')[1].split("</section>")[0]
    assert "jeudi 1 avril 2027" in panel and "Nicolas Rousseau" in panel
    assert "vendredi 2 avril 2027" in panel and "(affectation manuelle)" in panel
    assert 'href="#jour-2027-04-01"' in panel and "Voir dans le calendrier" in panel
    assert 'id="jour-2027-04-01"' in page


async def test_no_conflicts_panel_without_conflicts(client, db, team):
    clinic, nr, _ = team
    owners = {d: nr.id for d in APR}
    await scheduling_repo.create_generation(db, clinic.id, APR[0], APR[-1], nr.id, owners)
    await db.commit()
    page = (await client.get("/resultats")).text
    assert "Conflits avec desiderata" not in page
    assert " desiderata-conflict" not in page and "conflict-icon" not in page


async def test_generation_flash_counts_conflicts(client, db, team):
    clinic, nr, dt = team
    day = date(2027, 5, 12)
    for user in (nr, dt):
        db.add(DesiderataDay(user_id=user.id, clinic_id=clinic.id, date=day))
    await db.commit()
    await post_form(client, "/generer", {"period_from": "2027-05-01", "period_to": "2027-05-31"})
    page = (await client.get("/resultats")).text
    assert "1 jour(s) attribué(s) malgré une desiderata" in page
    assert 'href="#jour-2027-05-12"' in page


async def test_ical_feed_has_duties_only(client, db, team):
    clinic, nr, dt = team
    await _generation(db, clinic, nr, dt)
    db.add(DesiderataDay(user_id=nr.id, clinic_id=clinic.id, date=APR[1]))
    await db.commit()
    await post_form(client, "/parametres/calendrier")
    await db.refresh(nr)
    body = (await client.get(f"/ical/{nr.ical_token}")).text
    assert body.count("BEGIN:VEVENT") == 3  # April 1, 3, 5: NR's duties, conflict included
    assert "desiderata" not in body.lower()
    assert "DTSTART;VALUE=DATE:20270402" not in body


# --- Statistics ---------------------------------------------------------------------------------


async def test_desiderata_column_in_statistics(client, db, team):
    clinic, nr, dt = team
    await _generation(db, clinic, nr, dt)
    for day in (APR[0], APR[3], date(2027, 6, 1)):  # June: outside the period
        db.add(DesiderataDay(user_id=nr.id, clinic_id=clinic.id, date=day))
    db.add(Holiday(user_id=dt.id, clinic_id=clinic.id, date=APR[4]))
    await db.commit()
    page = (await client.get("/resultats")).text
    table = page.split('class="table stats-table"')[1].split("</table>")[0]
    assert '<th class="num">Congés</th>\n        <th class="num">Desiderata</th>' in table
    rows = {
        name: row
        for row in table.split("<tr>")[2:]
        for name in ("Nicolas Rousseau", "Dominique Thill")
        if name in row
    }
    assert (
        '<td class="num">0</td>\n          <td class="num">2</td>\n        </tr>'
        in rows["Nicolas Rousseau"]
    )
    assert (
        '<td class="num">1</td>\n          <td class="num">0</td>\n        </tr>'
        in rows["Dominique Thill"]
    )
    fragment = await client.get(
        "/generer/medecins", params={"period_from": "2027-04-01", "period_to": "2027-06-30"}
    )
    assert '<th class="num">Desiderata</th>' in fragment.text
    assert '<td class="num">0</td>\n        <td class="num">3</td>' in fragment.text  # NR
