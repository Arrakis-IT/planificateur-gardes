# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Group admin role and monthly locks."""

from contextlib import asynccontextmanager
from datetime import date, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models import Assignment, AuditLog, Generation, Holiday, MonthLock, OnsiteDay, User
from app.repositories import scheduling as scheduling_repo
from app.services.locks import OUT_OF_RANGE
from app.services.scheduler import UNASSIGNED, ScheduleRequest
from tests.conftest import TEST_CSRF_COOKIE, login, post_form, with_csrf

pytestmark = pytest.mark.db

TODAY = date(2026, 10, 14)
LOCKED = "Mois avril 2027 verrouillé."


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    monkeypatch.setattr("app.services.calendar.today_local", lambda now=None: TODAY)


async def _active_generation(db, clinic, user, start: date, end: date) -> None:
    """Store an active generation (every day to `user`), archiving the previous one."""
    await scheduling_repo.archive_active_generation(db, clinic.id)
    days = {start + timedelta(days=i): user.id for i in range((end - start).days + 1)}
    await scheduling_repo.create_generation(db, clinic.id, start, end, user.id, days)
    await db.commit()


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    """Clinic with its group admin (logged in), a second doctor and an active generation
    from October 2026 to June 2027 (months can only be locked inside it)."""
    clinic = await make_clinic("Groupe CRT")
    admin = await make_user("admin-doc@ex.lu", "Dr. Jean Dupont", clinic=clinic)
    admin.is_group_admin = True
    doctor = await make_user("doc@ex.lu", "Dr. Marie Richard", clinic=clinic)
    await db.commit()
    await _active_generation(db, clinic, admin, date(2026, 10, 1), date(2027, 6, 30))
    await login(client, "admin-doc@ex.lu")
    return clinic, admin, doctor


async def _lock(client, month: str, action: str = "lock"):
    return await post_form(client, "/parametres/verrous", {"month": month, "action": action})


async def _locked(db, clinic) -> set[date]:
    stmt = select(MonthLock.month).where(MonthLock.clinic_id == clinic.id)
    return set((await db.scalars(stmt)).all())


@asynccontextmanager
async def _superadmin(app, make_user):
    """A second client logged in as a superadmin."""
    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as c:
        c.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(c, "root@ex.lu")
        yield c


# --- Group admin role ---------------------------------------------------------------------


async def test_first_user_of_a_clinic_becomes_group_admin(app, db, make_clinic, make_user):
    clinic = await make_clinic()
    async with _superadmin(app, make_user) as root:
        for email, name in (("a@ex.lu", "Anne Dupont"), ("b@ex.lu", "Bruno Weber")):
            response = await post_form(
                root,
                "/admin/users",
                {"email": email, "full_name": name, "clinic_id": str(clinic.id)},
            )
            assert response.status_code == 303
    users = {u.email: u for u in (await db.scalars(select(User))).all()}
    assert users["a@ex.lu"].is_group_admin and not users["b@ex.lu"].is_group_admin
    assert not users["root@ex.lu"].is_group_admin
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "group_admin_changed"))
    assert entry.meta["automatic"] is True and entry.target_id == str(users["a@ex.lu"].id)


async def test_superadmin_hands_over_the_role(app, db, team, make_user):
    clinic, admin, doctor = team
    async with _superadmin(app, make_user) as root:
        sheet = await root.get(f"/admin/clinic/{clinic.id}")
        assert "Administrateur du groupe" in sheet.text and "Admin du groupe" in sheet.text
        response = await post_form(
            root, f"/admin/clinic/{clinic.id}/group-admin", {"user_id": str(doctor.id)}
        )
        assert response.status_code == 303
    await db.refresh(admin)
    await db.refresh(doctor)
    assert doctor.is_group_admin and not admin.is_group_admin
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "group_admin_changed"))
    assert entry.meta["previous"] == str(admin.id) and entry.meta["new_name"] == doctor.full_name
    admins = await db.scalar(
        select(func.count()).where(User.clinic_id == clinic.id, User.is_group_admin.is_(True))
    )
    assert admins == 1


async def test_role_only_for_active_users_of_the_clinic(app, db, team, make_clinic, make_user):
    clinic, admin, _ = team
    outsider = await make_user("out@ex.lu", "Autre Groupe", clinic=await make_clinic("Autre"))
    inactive = await make_user("off@ex.lu", "Inactif", clinic=clinic, active=False)
    async with _superadmin(app, make_user) as root:
        for user in (outsider, inactive):
            response = await post_form(
                root, f"/admin/clinic/{clinic.id}/group-admin", {"user_id": str(user.id)}
            )
            assert response.status_code == 303
            assert "alert-error" in (await root.get(response.headers["location"])).text
    await db.refresh(admin)
    assert admin.is_group_admin


async def test_group_admin_cannot_be_deleted_before_a_successor(app, db, team, make_user):
    clinic, admin, doctor = team
    async with _superadmin(app, make_user) as root:
        refused = await post_form(root, f"/admin/users/{admin.id}/delete")
        assert refused.headers["location"] == f"/admin/clinic/{clinic.id}"
        assert "désignez-en un autre" in (await root.get(refused.headers["location"])).text
        db.expunge_all()
        assert await db.get(User, admin.id) is not None

        await post_form(root, f"/admin/clinic/{clinic.id}/group-admin", {"user_id": str(doctor.id)})
        await post_form(root, f"/admin/users/{admin.id}/delete")
        db.expunge_all()
        assert await db.get(User, admin.id) is None

        # The last user of a clinic can go, role included.
        await post_form(root, f"/admin/users/{doctor.id}/delete")
        db.expunge_all()
        assert await db.get(User, doctor.id) is None


# --- Lock / unlock -----------------------------------------------------------------------------


async def test_group_admin_locks_and_unlocks_with_audit(client, db, team):
    clinic, admin, _ = team
    page = await client.get("/parametres")
    assert "Verrouillage des mois" in page.text and 'value="2026-10"' in page.text

    response = await _lock(client, "2027-04")
    assert response.status_code == 303
    assert LOCKED in (await client.get("/parametres")).text
    assert await _locked(db, clinic) == {date(2027, 4, 1)}
    assert (await _lock(client, "2027-04")).status_code == 303  # idempotent
    assert await db.scalar(select(func.count()).where(AuditLog.action == "month_locked")) == 1

    await _lock(client, "2027-04", "unlock")
    assert await _locked(db, clinic) == set()
    entries = (
        await db.scalars(
            select(AuditLog)
            .where(AuditLog.action.in_(["month_locked", "month_unlocked"]))
            .order_by(AuditLog.created_at)
        )
    ).all()
    assert [(e.action, e.user_id, e.meta) for e in entries] == [
        ("month_locked", admin.id, {"month": "2027-04"}),
        ("month_unlocked", admin.id, {"month": "2027-04"}),
    ]
    assert all(e.created_at is not None and e.clinic_id == clinic.id for e in entries)


async def test_past_months_cannot_be_locked(client, db, team):
    clinic, _, _ = team
    response = await _lock(client, "2026-09")
    assert response.status_code == 400 and "mois à venir" in response.text
    assert await _locked(db, clinic) == set()
    assert (await _lock(client, "2026-10")).status_code == 303  # current month is fine
    assert await _locked(db, clinic) == {date(2026, 10, 1)}


async def test_only_the_group_admin_manages_locks(client, db, team, app, make_user):
    clinic, _, _ = team
    await login(client, "doc@ex.lu")
    assert "Verrouillage des mois" not in (await client.get("/parametres")).text
    assert (await _lock(client, "2027-04")).status_code == 403
    bad = await post_form(client, "/parametres/verrous", {"month": "avril", "action": "lock"})
    assert bad.status_code == 400

    async with _superadmin(app, make_user) as root:
        response = await post_form(
            root, f"/admin/clinic/{clinic.id}/verrous", {"month": "2027-05", "action": "lock"}
        )
        assert response.status_code == 303
        assert "Verrouillage des mois" in (await root.get(f"/admin/clinic/{clinic.id}")).text
    assert await _locked(db, clinic) == {date(2027, 5, 1)}


async def test_lock_refused_outside_the_active_generation(client, db, team):
    clinic, _, _ = team
    response = await _lock(client, "2027-07")  # after the active generation
    assert response.status_code == 400 and OUT_OF_RANGE in response.text
    assert await _locked(db, clinic) == set()

    await scheduling_repo.archive_active_generation(db, clinic.id)
    await db.commit()
    response = await _lock(client, "2027-01")  # no active generation at all
    assert response.status_code == 400 and OUT_OF_RANGE in response.text
    assert "Aucune planification active" in response.text
    assert await _locked(db, clinic) == set()


async def test_settings_only_list_months_of_the_active_generation(client, db, team):
    clinic, admin, _ = team
    # Partially covered months count: 15/11/2026 → 10/01/2027 gives Nov, Dec, Jan.
    await _active_generation(db, clinic, admin, date(2026, 11, 15), date(2027, 1, 10))
    page = (await client.get("/parametres")).text
    shown = [
        m for m in ("2026-10", "2026-11", "2026-12", "2027-01", "2027-02") if f'value="{m}"' in page
    ]
    assert shown == ["2026-11", "2026-12", "2027-01"]
    # A lock left outside the generation (created before 0.7.0) stays listed to be unlocked.
    db.add(MonthLock(clinic_id=clinic.id, month=date(2027, 5, 1)))
    await db.commit()
    page = (await client.get("/parametres")).text
    row = page[page.index('value="2027-05"') :][:300]
    assert 'value="unlock"' in row and 'value="lock"' not in row


async def test_regeneration_releases_locks_outside_the_new_period(client, db, team, plan):
    clinic, admin, _ = team
    await _lock(client, "2026-12")
    await _lock(client, "2027-03")
    response = await _generate(client, "2027-01-01", "2027-06-30")
    assert response.status_code == 303
    assert await _locked(db, clinic) == {date(2027, 3, 1)}
    flash = (await client.get("/resultats")).text
    assert "Verrouillage retiré (hors de la nouvelle période) : décembre 2026." in flash
    entry = await db.scalar(
        select(AuditLog).where(
            AuditLog.action == "month_unlocked",
            AuditLog.meta["reason"].astext == "regeneration_out_of_range",
        )
    )
    assert entry is not None
    assert entry.meta["month"] == "2026-12" and entry.user_id == admin.id


# --- 403 on changes in a locked month ---------------------------------------------------------


async def test_vacations_and_onsite_refused_in_locked_month(client, db, team):
    await _lock(client, "2027-04")
    await login(client, "doc@ex.lu")
    for url, data in (
        ("/conges/toggle", {"date": "2027-04-12"}),
        ("/conges/periode", {"start": "2027-03-30", "end": "2027-04-02"}),
        ("/sur-place/toggle", {"date": "2027-04-12"}),
    ):
        response = await post_form(client, url, data)
        assert response.status_code == 403 and LOCKED in response.text, url
    assert await db.scalar(select(func.count(Holiday.id))) == 0
    assert await db.scalar(select(func.count(OnsiteDay.id))) == 0
    # Other months are untouched by the lock.
    assert (await post_form(client, "/conges/toggle", {"date": "2027-05-12"})).status_code == 303
    assert (await post_form(client, "/sur-place/toggle", {"date": "2027-03-12"})).status_code == 303


async def test_group_admin_is_blocked_too_until_unlock(client, db, team):
    await _lock(client, "2027-04")
    assert (await post_form(client, "/conges/toggle", {"date": "2027-04-12"})).status_code == 403
    await _lock(client, "2027-04", "unlock")
    assert (await post_form(client, "/conges/toggle", {"date": "2027-04-12"})).status_code == 303


@pytest.fixture
def plan(monkeypatch):
    """Fixed scheduler output: day → doctor name (default: first doctor)."""
    current: dict[date, str | None] = {}

    def fake_schedule(request: ScheduleRequest) -> dict[date, str]:
        code_of = {d.name: d.code for d in request.doctors}
        default = request.doctors[0].name
        days = (request.period_to - request.period_from).days + 1
        result = {}
        for offset in range(days):
            day = request.period_from + timedelta(days=offset)
            name = current.get(day, default)
            result[day] = code_of[name] if name else UNASSIGNED
        return result

    monkeypatch.setattr("app.services.planning.generate_schedule", fake_schedule)
    return current


async def _generate(client, start: str, end: str):
    return await post_form(
        client, "/generer", {"period_from": start, "period_to": end, "confirm_overwrite": "true"}
    )


async def test_assignment_edits_refused_in_locked_month(client, db, team, plan):
    _, admin, doctor = team
    await _generate(client, "2027-04-01", "2027-05-31")
    # A draft cell, then the month gets locked before saving.
    drafted = await post_form(client, "/resultats/jour/2027-04-05", {"doctor": doctor.doctor_code})
    assert drafted.status_code == 303
    await _lock(client, "2027-04")
    for url, data in (
        ("/resultats/jour/2027-04-06", {"doctor": doctor.doctor_code}),
        ("/resultats/jour/2027-04-06/sur-place", {}),
        ("/resultats/enregistrer", {}),
    ):
        response = await post_form(client, url, data)
        assert response.status_code == 403 and LOCKED in response.text, url
    cell = await db.scalar(select(Assignment).where(Assignment.date == date(2027, 4, 5)))
    assert cell.user_id == admin.id  # draft not applied
    assert (
        await post_form(client, "/resultats/jour/2027-05-06", {"doctor": "XX"})
    ).status_code == 303


async def test_locked_months_are_read_only_in_calendars(client, db, team, plan):
    await _generate(client, "2027-04-01", "2027-05-31")
    await _lock(client, "2027-04")
    vacations = await client.get("/conges?debut=2027-04")
    assert 'value="2027-04-12"' not in vacations.text and 'value="2027-05-12"' in vacations.text
    assert "🔒 Verrouillé" in vacations.text and "cal-day in-range locked" in vacations.text
    onsite = await client.get("/sur-place?debut=2027-04")
    assert 'value="2027-04-12"' not in onsite.text and "🔒 Verrouillé" in onsite.text
    month = await client.get("/mois?mois=2027-04")
    assert "/resultats/jour/2027-04-" not in month.text and "locked-cell" in month.text
    assert "/resultats/jour/2027-05-" in (await client.get("/mois?mois=2027-05")).text
    results = await client.get("/resultats")
    assert "/resultats/jour/2027-04-" not in results.text and "🔒 Verrouillé" in results.text
    assert "/resultats/jour/2027-05-" in results.text
    week = await client.get("/semaine?date=2027-04-12")
    assert "🔒 Verrouillé" in week.text


# --- Regeneration ----------------------------------------------------------------------------


async def test_regeneration_keeps_locked_months(client, db, team, mailer, plan):
    _, admin, doctor = team
    await _generate(client, "2027-04-01", "2027-05-31")  # everything to the first doctor
    first = await db.scalar(select(Generation).where(Generation.is_active.is_(True)))
    april = date(2027, 4, 12)
    removed = await db.scalar(
        select(Assignment).where(Assignment.generation_id == first.id, Assignment.date == april)
    )
    removed.onsite_removed = True
    await db.commit()
    await _lock(client, "2027-04")

    fragment = await client.get(
        "/generer/medecins", params={"period_from": "2027-03-15", "period_to": "2027-06-15"}
    )
    assert "Les mois verrouillés (avril 2027) ne seront pas modifiés." in fragment.text

    plan.clear()
    for offset in range(120):
        plan[date(2027, 3, 1) + timedelta(days=offset)] = doctor.full_name
    response = await _generate(client, "2027-03-15", "2027-06-15")
    assert response.status_code == 303
    flash = (await client.get("/resultats")).text
    assert "Les mois verrouillés (avril 2027) ne seront pas modifiés." in flash

    second = await db.scalar(select(Generation).where(Generation.is_active.is_(True)))
    cells = {
        a.date: a
        for a in (
            await db.scalars(select(Assignment).where(Assignment.generation_id == second.id))
        ).all()
    }
    assert all(cells[date(2027, 4, d)].user_id == admin.id for d in range(1, 31))
    assert cells[april].onsite_removed is True
    assert cells[date(2027, 5, 3)].user_id == doctor.id
    assert cells[date(2027, 3, 20)].user_id == doctor.id

    text = mailer.sent[-1].text
    assert "Changements par rapport à la planification précédente" in text
    assert "avril 2027 :" not in text  # locked month left out of the changelog
    assert f"3 mai 2027 : {admin.full_name} → {doctor.full_name}" in text
    meta = await db.scalar(
        select(AuditLog.meta).where(
            AuditLog.action == "generation_created", AuditLog.target_id == str(second.id)
        )
    )
    assert meta["locked_months"] == ["2027-04"]


async def test_htmx_toggle_in_locked_month_is_403(client, db, team):
    await _lock(client, "2027-04")
    response = await client.post(
        "/conges/toggle", data=with_csrf({"date": "2027-04-12"}), headers={"HX-Request": "true"}
    )
    assert response.status_code == 403
