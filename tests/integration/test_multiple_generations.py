# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Several active generations per clinic: overlap (transfer / recalculate), locked months,
shrinking, automatic and manual archiving, calendars, editing and results."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models import Assignment, AuditLog, DesiderataConflict, Generation, MonthLock
from app.repositories import scheduling as repo
from app.services import planning
from app.services.planning import OverlapConfirmationRequired, find_overlap, run_generation
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 14)
OCT, NOV, DEC = date(2026, 10, 1), date(2026, 11, 1), date(2026, 12, 1)
JAN, JUN_END = date(2027, 1, 1), date(2027, 6, 30)
DEC_END = date(2026, 12, 31)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: TODAY)


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    """Clinic, two doctors (NR logged in) and generation A: October–December 2026, all NR,
    with a manual edit on 10 November (DT, desiderata conflict)."""
    clinic = await make_clinic("Groupe CRT")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    first = await repo.create_generation(
        db, clinic.id, OCT, DEC_END, nr.id, {d: nr.id for d in _days(OCT, DEC_END)}
    )
    edited = await _cell(db, first.id, date(2026, 11, 10))
    edited.user_id = dt.id
    edited.edited_by = nr.id
    edited.edited_at = datetime.now(UTC)
    edited.desiderata_conflict = DesiderataConflict.MANUAL
    await db.commit()
    await login(client, "nr@ex.lu")
    return clinic, nr, dt, first


async def _cell(db, generation_id, day) -> Assignment:
    return await db.scalar(
        select(Assignment).where(Assignment.generation_id == generation_id, Assignment.date == day)
    )


async def _lock(db, clinic, month: date) -> None:
    db.add(MonthLock(clinic_id=clinic.id, month=month))
    await db.commit()


async def _generate(db, clinic, actor, start, end, mode=None):
    run = await run_generation(db, clinic.id, actor, start, end, overlap_mode=mode)
    await db.commit()
    await db.refresh(run.generation)
    return run


async def _dates(db, generation) -> list[date]:
    return await repo.assignment_dates(db, generation.id)


# --- Overlap detection and confirmation -------------------------------------------------------


async def test_overlap_lists_transferable_and_locked_months(db, team):
    clinic, _, _, first = team
    await _lock(db, clinic, OCT)
    overlap = await find_overlap(db, clinic.id, OCT, JUN_END)
    (part,) = overlap.parts
    assert part.generation.id == first.id
    assert part.months == [NOV, DEC] and part.kept_months == [OCT]


async def test_overlap_requires_a_choice(db, team):
    clinic, nr, _, _ = team
    with pytest.raises(OverlapConfirmationRequired) as exc:
        await run_generation(db, clinic.id, nr, NOV, JUN_END)
    assert [p.months for p in exc.value.overlap.transfers] == [[NOV, DEC]]


async def test_overlap_preview_endpoint_and_confirmation_dialog(client, db, team):
    preview = await client.get(
        "/generer/chevauchement", params={"period_from": "2026-11-01", "period_to": "2027-06-30"}
    )
    assert "Attention : cette nouvelle génération inclut les mois" in preview.text
    assert "novembre 2026, décembre 2026" in preview.text
    assert "déjà couverts par la génération du" in preview.text
    none = await client.get(
        "/generer/chevauchement", params={"period_from": "2027-01-01", "period_to": "2027-06-30"}
    )
    assert "Attention" not in none.text

    dialog = await post_form(
        client, "/generer", {"period_from": "2026-11-01", "period_to": "2027-06-30"}
    )
    assert dialog.status_code == 409
    assert 'name="overlap_mode" value="move"' in dialog.text
    assert 'name="overlap_mode" value="recalc"' in dialog.text
    assert ">Confirmer</button>" in dialog.text and 'href="/generer">Annuler</a>' in dialog.text
    assert await db.scalar(select(func.count(Generation.id))) == 1  # nothing created


# --- Scenario 1: A (Oct–Dec, October locked), B (Nov–Jun) --------------------------------------


async def test_transfer_with_locked_month_shrinks_the_old_generation(db, team):
    clinic, nr, dt, first = team
    await _lock(db, clinic, OCT)
    moved_id = (await _cell(db, first.id, date(2026, 11, 10))).id
    run = await _generate(db, clinic, nr, NOV, JUN_END, "move")
    second = run.generation

    await db.refresh(first)
    assert first.active and (first.period_from, first.period_to) == (OCT, date(2026, 10, 31))
    assert await _dates(db, first) == _days(OCT, date(2026, 10, 31))
    assert await _dates(db, second) == _days(NOV, JUN_END)
    moved = await _cell(db, second.id, date(2026, 11, 10))
    assert moved.id == moved_id  # the very row, with its manual edit and flag
    assert moved.user_id == dt.id and moved.edited_by == nr.id
    assert moved.desiderata_conflict == DesiderataConflict.MANUAL
    assert run.previous_ids == ()  # nothing recalculated: no changelog
    assert [g.id for g in await repo.active_generations(db, clinic.id)] == [first.id, second.id]


async def test_recalculate_with_locked_month_archives_the_replaced_days(db, team):
    clinic, nr, _, first = team
    await _lock(db, clinic, OCT)
    run = await _generate(db, clinic, nr, NOV, JUN_END, "recalc")
    second = run.generation

    await db.refresh(first)
    assert first.active and first.period_to == date(2026, 10, 31)
    assert await _dates(db, second) == _days(NOV, JUN_END)
    assert (await _cell(db, second.id, date(2026, 11, 10))).edited_by is None  # recalculated
    (split_id,) = run.previous_ids
    split = await db.get(Generation, split_id)
    assert not split.active and (split.period_from, split.period_to) == (NOV, DEC_END)
    assert await _dates(db, split) == _days(NOV, DEC_END)  # history kept
    assert split.generated_at == first.generated_at
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "generation_split"))


# --- Scenario 2: A (Oct–Dec) without locks, B (Oct–Jun) ----------------------------------------


async def test_full_transfer_archives_the_emptied_generation(db, team):
    clinic, nr, _, first = team
    run = await _generate(db, clinic, nr, OCT, JUN_END, "move")
    await db.refresh(first)
    assert not first.active and await _dates(db, first) == []
    assert await _dates(db, run.generation) == _days(OCT, JUN_END)
    assert [g.id for g in run.archived] == [first.id]
    reason = await db.scalar(
        select(AuditLog.meta["reason"].astext).where(
            AuditLog.action == "generation_archived", AuditLog.target_id == str(first.id)
        )
    )
    assert reason == "emptied"


async def test_full_recalculation_archives_the_old_generation_with_its_rows(db, team):
    clinic, nr, _, first = team
    run = await _generate(db, clinic, nr, OCT, JUN_END, "recalc")
    await db.refresh(first)
    assert not first.active and await _dates(db, first) == _days(OCT, DEC_END)
    assert run.previous_ids == (first.id,)
    assert await repo.active_generations(db, clinic.id) == [run.generation]


# --- Scenario 3: no overlap ---------------------------------------------------------------------


async def test_no_overlap_keeps_both_active(db, team):
    clinic, nr, _, first = team
    run = await _generate(db, clinic, nr, JAN, JUN_END)  # no confirmation needed
    await db.refresh(first)
    assert first.active and (first.period_from, first.period_to) == (OCT, DEC_END)
    assert await _dates(db, first) == _days(OCT, DEC_END)
    assert await _dates(db, run.generation) == _days(JAN, JUN_END)
    assert run.overlap.parts == [] and run.archived == ()


# --- Scenario 4: automatic archiving of past generations ------------------------------------------


async def test_expired_generations_are_archived_and_stay_visible(client, db, team, monkeypatch):
    clinic, _, dt, first = team
    later = date(2027, 1, 5)
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: later)
    archived = await planning.archive_expired(db, later)
    await db.commit()
    assert [g.id for g in archived] == [first.id]
    await db.refresh(first)
    assert not first.active
    reason = await db.scalar(
        select(AuditLog.meta["reason"].astext).where(AuditLog.action == "generation_archived")
    )
    assert reason == "expired"
    assert await planning.archive_expired(db, later) == []  # idempotent

    # History: November still shows its doctors, read-only.
    page = (await client.get("/mois?mois=2026-11")).text
    assert page.count('class="cell-doctor"') == 30
    assert "planification archivée" in page
    assert "/resultats/jour/2026-11-" not in page  # no edit links
    days = await planning.build_days(db, clinic.id, date(2026, 11, 10), date(2026, 11, 10))
    assert days[date(2026, 11, 10)].doctor.id == dt.id and days[date(2026, 11, 10)].archived


async def test_generation_running_today_is_not_expired(db, team):
    _clinic, *_ = team
    assert await planning.archive_expired(db, date(2026, 12, 31)) == []


async def test_startup_job_archives_expired_generations(db, team, monkeypatch):
    from app.main import archive_expired_generations

    _clinic, _, _, first = team
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: date(2027, 2, 1))
    assert await archive_expired_generations() == 1
    db.expunge_all()
    assert not (await db.get(Generation, first.id)).active


# --- Scenario 5: manual archiving ----------------------------------------------------------------


async def test_superadmin_archives_one_generation_without_way_back(client, db, team, make_user):
    clinic, nr, _, first = team
    second = (await _generate(db, clinic, nr, JAN, JUN_END)).generation
    await make_user("root@ex.lu", "Root", is_superadmin=True)
    await login(client, "root@ex.lu")
    page = (await client.get(f"/admin/clinic/{clinic.id}/generations")).text
    assert "Planifications actives" in page
    assert page.count("<summary>Archiver</summary>") == 2
    assert "01/10/2026 – 31/12/2026" in page and "01/01/2027 – 30/06/2027" in page
    response = await post_form(client, f"/admin/generations/{first.id}/archive")
    assert response.status_code == 303
    # No route to reactivate: its days may belong to another generation since.
    back = await post_form(client, f"/admin/generations/{first.id}/unarchive")
    assert back.status_code in (404, 405)
    db.expunge_all()
    assert not (await db.get(Generation, first.id)).active
    assert (await db.get(Generation, second.id)).active
    reason = await db.scalar(
        select(AuditLog.meta["reason"].astext).where(AuditLog.action == "generation_archived")
    )
    assert reason == "manual"


# --- Calendars, editing, results with several active generations ---------------------------------


async def test_calendar_shows_the_union_and_edits_reach_each_generation(client, db, team, mailer):
    clinic, nr, dt, first = team
    second = (await _generate(db, clinic, nr, JAN, JUN_END)).generation
    (await _cell(db, second.id, date(2027, 1, 15))).user_id = nr.id  # scheduler's choice aside
    await db.commit()
    december = (await client.get("/mois?mois=2026-12")).text
    january = (await client.get("/mois?mois=2027-01")).text
    assert (
        december.count('class="cell-doctor"') == 31 and january.count('class="cell-doctor"') == 31
    )

    for day in ("2026-12-15", "2027-01-15"):
        assert (
            await post_form(client, f"/resultats/jour/{day}", {"doctor": "DT"})
        ).status_code == 303
    await post_form(client, "/resultats/enregistrer", {"retour": "/resultats"})
    assert (await _cell(db, first.id, date(2026, 12, 15))).user_id == dt.id
    assert (await _cell(db, second.id, date(2027, 1, 15))).user_id == dt.id
    # One PDF per changed generation.
    subjects = sorted({m.subject for m in mailer.sent})
    assert subjects == [
        "Gardes Groupe CRT du 01/01/2027 au 30/06/2027",
        "Gardes Groupe CRT du 01/10/2026 au 31/12/2026",
    ]


async def test_results_select_one_generation_with_its_own_stats(client, db, team):
    clinic, nr, _, _first = team
    second = (await _generate(db, clinic, nr, JAN, JUN_END)).generation
    default = (await client.get("/resultats")).text  # covers today (October 2026)
    assert "Planification du 01/10/2026 au 31/12/2026" in default
    assert f'href="/resultats?generation={second.id}"' in default
    other = (await client.get(f"/resultats?generation={second.id}")).text
    assert "Planification du 01/01/2027 au 30/06/2027" in other
    assert f'href="/resultats/pdf?generation={second.id}"' in other
    stats = await planning.results_stats(db, clinic.id, second)
    assert sum(r.week + r.weekend for r in stats.stats.doctors) == len(_days(JAN, JUN_END))
    unknown = await client.get("/resultats?generation=not-a-uuid")
    assert "Planification du 01/10/2026 au 31/12/2026" in unknown.text


async def test_stats_ignore_days_held_by_another_generation(db, team):
    """Locked October stays in A inside B's period: B's statistics skip it."""
    clinic, nr, _, _ = team
    await _lock(db, clinic, OCT)
    second = (await _generate(db, clinic, nr, OCT, JUN_END, "move")).generation
    stats = (await planning.results_stats(db, clinic.id, second)).stats
    assert stats.unassigned == 0
    assert stats.total_week + stats.total_weekend == len(_days(NOV, JUN_END))


# --- Regression (0.13.1): confirmation dialog after a refused run -------------------------------


@pytest.fixture
async def two_actives(db, team):
    """A: October–December (October locked), B: January–March 2027, both active."""
    clinic, nr, _, first = team
    second = await repo.create_generation(
        db,
        clinic.id,
        JAN,
        date(2027, 3, 31),
        nr.id,
        {d: nr.id for d in _days(JAN, date(2027, 3, 31))},
    )
    await _lock(db, clinic, OCT)
    return clinic, first, second


async def _as(client, who, make_user, nr):
    if who == "superadmin":  # its User row is not reloaded by the clinic queries
        await make_user("root@ex.lu", "Root", is_superadmin=True)
        await login(client, "root@ex.lu")
        assert (await post_form(client, f"/admin/users/{nr.id}/impersonate")).status_code == 303


@pytest.mark.parametrize("who", ["doctor", "superadmin"])
async def test_overlap_of_two_generations_answers_409_with_both_periods(
    client, db, team, two_actives, make_user, who
):
    _, nr, _, _ = team
    clinic, _, _ = two_actives
    await _as(client, who, make_user, nr)
    period = {"period_from": "2026-10-01", "period_to": "2027-06-30"}
    response = await post_form(client, "/generer", period)
    assert response.status_code == 409
    assert "Chevauchement avec une planification active" in response.text
    assert "novembre 2026, décembre 2026" in response.text
    assert "(période du 01/10/2026 au 31/12/2026)" in response.text
    assert "janvier 2027, février 2027, mars 2027" in response.text
    assert "(période du 01/01/2027 au 31/03/2027)" in response.text
    assert "🔒 Les mois verrouillés (octobre 2026)" in response.text
    # The dialog posts the same period back with the chosen mode.
    assert 'name="period_from" value="2026-10-01"' in response.text
    assert 'name="period_to" value="2027-06-30"' in response.text
    db.expunge_all()
    assert len(await repo.active_generations(db, clinic.id)) == 2  # nothing changed


@pytest.mark.parametrize("who", ["doctor", "superadmin"])
async def test_overlap_of_two_generations_transferred(
    client, db, team, two_actives, make_user, who
):
    _, nr, _, _ = team
    clinic, first, second = two_actives
    await _as(client, who, make_user, nr)
    period = {"period_from": "2026-10-01", "period_to": "2027-06-30", "overlap_mode": "move"}
    response = await post_form(client, "/generer", period)
    assert response.status_code == 303
    db.expunge_all()
    (kept, third) = await repo.active_generations(db, clinic.id)
    assert kept.id == first.id and (kept.period_from, kept.period_to) == (
        OCT,
        date(2026, 10, 31),
    )
    assert not (await db.get(Generation, second.id)).active  # emptied
    assert await _dates(db, third) == _days(NOV, JUN_END)
    # Transferred as they are, including the manual edit of 10 November.
    assert (await _cell(db, third.id, date(2026, 11, 10))).edited_by == nr.id


@pytest.mark.parametrize("who", ["doctor", "superadmin"])
async def test_overlap_of_two_generations_recalculated(
    client, db, team, two_actives, make_user, who
):
    _, nr, _, _ = team
    clinic, first, second = two_actives
    await _as(client, who, make_user, nr)
    period = {"period_from": "2026-10-01", "period_to": "2027-06-30", "overlap_mode": "recalc"}
    response = await post_form(client, "/generer", period)
    assert response.status_code == 303
    db.expunge_all()
    (kept, third) = await repo.active_generations(db, clinic.id)
    assert kept.id == first.id and kept.period_to == date(2026, 10, 31)
    old = await db.get(Generation, second.id)
    assert not old.active and await _dates(db, old) == _days(JAN, date(2027, 3, 31))
    assert await _dates(db, third) == _days(NOV, JUN_END)
    assert (await _cell(db, third.id, date(2026, 11, 10))).edited_by is None  # recalculated
