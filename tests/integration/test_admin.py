# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Superadmin panel: access control, clinics and users management."""

import re
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.models import (
    Assignment,
    AuditLog,
    Clinic,
    CustomHoliday,
    Generation,
    Holiday,
    User,
    UserSession,
)
from app.repositories import scheduling as scheduling_repo
from app.repositories.admin import platform_stats
from app.services.doctors import DOCTOR_PALETTE
from app.services.holidays import effective_holidays
from app.services.planning import results_stats
from tests.conftest import DEFAULT_PASSWORD, TEST_CSRF_COOKIE, login, post_form

pytestmark = pytest.mark.db


@pytest.fixture
async def admin_client(client, make_user):
    admin = await make_user("admin@example.lu", "Juan Admin", is_superadmin=True)
    await login(client, "admin@example.lu")
    client.admin = admin
    return client


# --- Access control -----------------------------------------------------------------------


async def test_admin_requires_login(client, db):
    response = await client.get("/admin/clinics")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


async def test_clinic_user_gets_403_on_admin(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await login(client, "doc@example.lu")
    for url in ("/admin/clinics", "/admin/users"):
        assert (await client.get(url)).status_code == 403
    response = await post_form(client, "/admin/clinics", {"name": "Pirate"})
    assert response.status_code == 403


async def test_superadmin_is_redirected_away_from_clinic_app(admin_client, db):
    response = await admin_client.get("/")
    assert response.status_code == 303 and response.headers["location"] == "/admin"


# --- Clinics ------------------------------------------------------------------------------


async def test_create_rename_toggle_clinic(admin_client, db):
    response = await post_form(admin_client, "/admin/clinics", {"name": "Cabinet du Parc"})
    assert response.status_code == 303
    clinic = await db.scalar(select(Clinic))
    assert clinic.slug == "cabinet-du-parc"

    await post_form(admin_client, "/admin/clinics", {"name": "Cabinet du Parc"})
    slugs = set((await db.scalars(select(Clinic.slug))).all())
    assert slugs == {"cabinet-du-parc", "cabinet-du-parc-2"}

    await post_form(admin_client, f"/admin/clinics/{clinic.id}/rename", {"name": "Parc Médical"})
    await post_form(admin_client, f"/admin/clinics/{clinic.id}/toggle-active")
    await db.refresh(clinic)
    assert clinic.name == "Parc Médical" and clinic.slug == "cabinet-du-parc"
    assert clinic.active is False

    page = await admin_client.get("/admin/clinics")
    assert "Parc Médical" in page.text
    actions = set((await db.scalars(select(AuditLog.action))).all())
    assert {"clinic_created", "clinic_renamed", "clinic_deactivated"} <= actions


async def test_clinic_with_users_cannot_be_deleted(admin_client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    response = await post_form(admin_client, f"/admin/clinics/{clinic.id}/delete")
    assert response.status_code == 303
    assert await db.get(Clinic, clinic.id) is not None
    page = await admin_client.get("/admin/clinics")
    assert "encore des utilisateurs" in page.text

    empty = await make_clinic("Vide")
    await post_form(admin_client, f"/admin/clinics/{empty.id}/delete")
    db.expunge_all()
    assert await db.get(Clinic, empty.id) is None


async def test_unknown_clinic_is_404(admin_client, db):
    response = await post_form(
        admin_client, "/admin/clinics/00000000-0000-0000-0000-000000000000/toggle-active"
    )
    assert response.status_code == 404


async def test_empty_clinic_name_is_rejected(admin_client, db):
    response = await post_form(admin_client, "/admin/clinics", {"name": "   "})
    assert response.status_code == 400


# --- Users --------------------------------------------------------------------------------


async def test_create_users_assigns_codes_and_colors(admin_client, db, make_clinic):
    clinic = await make_clinic()
    for email, name in [("a@ex.lu", "Nicolas Rousseau"), ("b@ex.lu", "Nadia Roth")]:
        response = await post_form(
            admin_client,
            "/admin/users",
            {
                "email": email,
                "full_name": name,
                "clinic_id": str(clinic.id),
            },
        )
        assert response.status_code == 303
    users = (await db.scalars(select(User).where(User.clinic_id == clinic.id))).all()
    by_email = {u.email: u for u in users}
    assert by_email["a@ex.lu"].doctor_code == "NR"
    assert by_email["b@ex.lu"].doctor_code == "NRO"
    assert by_email["a@ex.lu"].color == DOCTOR_PALETTE[0]
    assert by_email["b@ex.lu"].color == DOCTOR_PALETTE[1]
    assert not any(u.must_change_password for u in users)  # they choose it via the invitation


async def test_duplicate_email_race_renders_the_form(admin_client, db, make_clinic, monkeypatch):
    """The unique index catches an e-mail created meanwhile (pre-check passed): the form is
    shown again (400), not a 500 from the superadmin expired by a full rollback."""
    clinic = await make_clinic()
    data = {"email": "race@ex.lu", "full_name": "Anne Dupont", "clinic_id": str(clinic.id)}
    assert (await post_form(admin_client, "/admin/users", data)).status_code == 303

    async def no_user(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.routers.admin.users_repo.get_user_by_email", no_user)
    response = await post_form(admin_client, "/admin/users", data)
    assert response.status_code == 400 and "existe déjà" in response.text
    assert "Juan Admin" in response.text or "Déconnexion" in response.text  # full layout


async def test_create_user_validation_errors(admin_client, db, make_clinic):
    clinic = await make_clinic()
    base = {"email": "a@ex.lu", "full_name": "Anne Dupont"}

    no_clinic = await post_form(admin_client, "/admin/users", base)
    assert no_clinic.status_code == 400 and "Choisissez un groupe" in no_clinic.text

    both = await post_form(
        admin_client,
        "/admin/users",
        base | {"clinic_id": str(clinic.id), "is_superadmin": "true"},
    )
    assert both.status_code == 400

    ok = await post_form(admin_client, "/admin/users", base | {"clinic_id": str(clinic.id)})
    assert ok.status_code == 303
    dup = await post_form(
        admin_client, "/admin/users", base | {"email": "A@EX.LU", "clinic_id": str(clinic.id)}
    )
    assert dup.status_code == 400 and "existe déjà" in dup.text

    bad_email = await post_form(
        admin_client, "/admin/users", base | {"email": "nope", "clinic_id": str(clinic.id)}
    )
    assert bad_email.status_code == 400


async def test_create_superadmin(admin_client, db):
    response = await post_form(
        admin_client,
        "/admin/users",
        {
            "email": "second@ex.lu",
            "full_name": "Second Admin",
            "is_superadmin": "true",
        },
    )
    assert response.status_code == 303
    user = await db.scalar(select(User).where(User.email == "second@ex.lu"))
    assert user.is_superadmin and user.clinic_id is None


async def test_admin_password_reset_shows_temp_password_once(
    admin_client, client, db, make_clinic, make_user
):
    clinic = await make_clinic()
    user = await make_user("doc@example.lu", clinic=clinic)
    db.add(UserSession(token_hash="x" * 64, user_id=user.id, expires_at=func.now()))
    await db.commit()

    response = await post_form(admin_client, f"/admin/users/{user.id}/reset-password")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Mot de passe temporaire" in response.text
    await db.refresh(user)
    assert user.must_change_password
    sessions = await db.scalar(
        select(func.count(UserSession.id)).where(UserSession.user_id == user.id)
    )
    assert sessions == 0
    # Not shown again on the next page load.
    assert "Mot de passe temporaire" not in (await admin_client.get("/admin/users")).text


async def test_toggle_and_delete_user(admin_client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("doc@example.lu", clinic=clinic)
    await post_form(admin_client, f"/admin/users/{user.id}/toggle-active")
    await db.refresh(user)
    assert user.active is False
    await post_form(admin_client, f"/admin/users/{user.id}/delete")
    db.expunge_all()
    assert await db.get(User, user.id) is None
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "user_deleted"))


async def test_admin_cannot_deactivate_or_delete_self(admin_client, db):
    admin = admin_client.admin
    await post_form(admin_client, f"/admin/users/{admin.id}/toggle-active")
    await post_form(admin_client, f"/admin/users/{admin.id}/delete")
    db.expunge_all()
    refreshed = await db.get(User, admin.id)
    assert refreshed is not None and refreshed.active


async def test_users_list_filters(admin_client, db, make_clinic, make_user):
    a, b = await make_clinic("Alpha"), await make_clinic("Beta")
    await make_user("a@ex.lu", "Alice Alpha", clinic=a)
    await make_user("b@ex.lu", "Bob Beta", clinic=b, active=False)

    only_a = await admin_client.get(f"/admin/users?clinic={a.id}")
    assert "Alice Alpha" in only_a.text and "Bob Beta" not in only_a.text
    admins = await admin_client.get("/admin/users?clinic=superadmins")
    assert "Juan Admin" in admins.text and "Alice Alpha" not in admins.text
    inactive = await admin_client.get("/admin/users?status=inactive")
    assert "Bob Beta" in inactive.text and "Alice Alpha" not in inactive.text
    garbage = await admin_client.get("/admin/users?clinic=not-a-uuid")
    assert garbage.status_code == 200


async def test_admin_reset_user_must_change_password(admin_client, app, db, make_clinic, make_user):
    """An admin-issued temporary password forces a change at next login."""
    clinic = await make_clinic()
    user = await make_user("new@ex.lu", "Nouveau Médecin", clinic=clinic)
    page = await post_form(admin_client, f"/admin/users/{user.id}/reset-password")
    temporary = re.search(r'<code class="secret">([^<]+)</code>', page.text).group(1)
    await db.refresh(user)
    assert user.must_change_password is True

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as doc:
        doc.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        response = await login(doc, "new@ex.lu", temporary)
        assert response.headers["location"] == "/parametres"
        assert (await doc.get("/")).headers["location"] == "/parametres"


# --- Invitations ----------------------------------------------------------------------------


async def _create_invited_doctor(admin_client, clinic) -> None:
    response = await post_form(
        admin_client,
        "/admin/users",
        {"email": "invite@ex.lu", "full_name": "Juan Manuel", "clinic_id": str(clinic.id)},
    )
    assert response.status_code == 303


async def test_invitation_flow_end_to_end(admin_client, app, db, mailer, make_clinic):
    """Create user → invitation emailed → link consumed → first login works."""
    clinic = await make_clinic()
    await _create_invited_doctor(admin_client, clinic)
    assert "Invitation envoyée à invite@ex.lu" in (await admin_client.get("/admin/users")).text

    [email] = mailer.sent
    assert email.to == "invite@ex.lu" and email.subject.startswith("Bienvenue")
    assert "Bonjour Juan Manuel" in email.text and "72 heures" in email.text
    link = mailer.last_link()
    assert link.startswith("/invitation/")

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as doc:
        doc.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        # Not usable before the invitation: no password was ever disclosed.
        assert (await login(doc, "invite@ex.lu", DEFAULT_PASSWORD)).status_code == 401

        form = await doc.get(link)
        assert form.status_code == 200 and "Bienvenue" in form.text
        assert 'name="accept_terms"' in form.text
        assert 'href="/cgu"' in form.text and 'href="/politique-confidentialite"' in form.text
        good = {"password": "first-password-42", "password_confirm": "first-password-42"}
        mismatch = await post_form(
            doc, link, good | {"password_confirm": "other-password-42", "accept_terms": "true"}
        )
        assert mismatch.status_code == 400
        # The terms checkbox is mandatory: nothing is stored without it.
        no_terms = await post_form(doc, link, good)
        assert no_terms.status_code == 400 and "Vous devez accepter" in no_terms.text
        assert (await doc.get(link)).status_code == 200  # token still valid
        done = await post_form(doc, link, good | {"accept_terms": "true"})
        assert done.status_code == 303 and done.headers["location"] == "/login"
        assert (await doc.get(link)).status_code == 400  # single use

        response = await login(doc, "invite@ex.lu", "first-password-42")
        assert response.status_code == 303 and response.headers["location"] == "/"
        assert (await doc.get("/")).status_code == 200

    user = await db.scalar(select(User).where(User.email == "invite@ex.lu"))
    assert not user.must_change_password
    assert user.accepted_terms_at is not None
    actions = (
        await db.scalars(select(AuditLog.action).where(AuditLog.target_id == str(user.id)))
    ).all()
    assert {
        "user_created",
        "invitation_sent",
        "password_changed",
        "terms_accepted",
        "login",
    } <= set(actions)
    via = await db.scalar(
        select(AuditLog.meta).where(
            AuditLog.action == "password_changed", AuditLog.target_id == str(user.id)
        )
    )
    assert via == {"via": "invitation"}


async def test_resend_invitation_replaces_previous_link(admin_client, app, db, mailer, make_clinic):
    clinic = await make_clinic()
    await _create_invited_doctor(admin_client, clinic)
    first_link = mailer.last_link()
    user = await db.scalar(select(User).where(User.email == "invite@ex.lu"))

    profile = await admin_client.get(f"/admin/users/{user.id}")
    assert "Renvoyer l&#39;invitation" in profile.text or "Renvoyer l'invitation" in profile.text
    response = await post_form(admin_client, f"/admin/users/{user.id}/resend-invitation")
    assert response.status_code == 303 and response.headers["location"] == f"/admin/users/{user.id}"
    assert len(mailer.sent) == 2
    assert mailer.last_link() != first_link
    assert (await admin_client.get(first_link)).status_code == 400
    assert (await admin_client.get(mailer.last_link())).status_code == 200


async def test_invitation_email_failure_is_reported(admin_client, db, mailer, make_clinic):
    clinic = await make_clinic()
    mailer.fail = True
    await _create_invited_doctor(admin_client, clinic)
    page = await admin_client.get("/admin/users")
    assert "pas pu être envoyée" in page.text
    user = await db.scalar(select(User).where(User.email == "invite@ex.lu"))
    assert user is not None  # the account exists; the admin can resend

    resend = await post_form(admin_client, f"/admin/users/{user.id}/resend-invitation")
    assert "pas pu être envoyée" in (await admin_client.get(resend.headers["location"])).text


async def test_no_invitation_for_inactive_user(admin_client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("off@ex.lu", clinic=clinic, active=False)
    response = await post_form(admin_client, f"/admin/users/{user.id}/resend-invitation")
    assert "inactif" in (await admin_client.get(response.headers["location"])).text
    assert mailer.sent == []


async def test_invalid_invitation_link(client, db):
    page = await client.get("/invitation/not-a-token")
    assert page.status_code == 400 and "renvoyer l" in page.text
    submit = await post_form(
        client, "/invitation/not-a-token", {"password": "x" * 12, "password_confirm": "x" * 12}
    )
    assert submit.status_code == 400


# --- User name -----------------------------------------------------------------------------


async def test_admin_renames_user(admin_client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    sheet = await admin_client.get(f"/admin/users/{user.id}")
    assert f'action="/admin/users/{user.id}/rename"' in sheet.text

    response = await post_form(
        admin_client, f"/admin/users/{user.id}/rename", {"full_name": "  Nicolas Roussel  "}
    )
    assert response.status_code == 303 and response.headers["location"] == f"/admin/users/{user.id}"
    assert "Nom modifié." in (await admin_client.get(response.headers["location"])).text
    await db.refresh(user)
    assert user.full_name == "Nicolas Roussel" and user.doctor_code == "NR"  # code is stable
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "user_renamed"))
    assert entry.user_id == admin_client.admin.id and entry.target_id == str(user.id)
    assert entry.meta == {"old": "Nicolas Rousseau", "new": "Nicolas Roussel"}

    same = await post_form(
        admin_client, f"/admin/users/{user.id}/rename", {"full_name": "Nicolas Roussel"}
    )
    assert same.status_code == 303
    assert await db.scalar(select(func.count()).where(AuditLog.action == "user_renamed")) == 1


@pytest.mark.parametrize("name", ["", "   ", "x", "y" * 201])
async def test_admin_rename_validation(admin_client, db, make_clinic, make_user, name):
    user = await make_user("nr@ex.lu", clinic=await make_clinic())
    response = await post_form(admin_client, f"/admin/users/{user.id}/rename", {"full_name": name})
    assert response.status_code == 400
    await db.refresh(user)
    assert user.full_name == "Nicolas Rousseau"


async def test_admin_rename_unknown_user_is_404(admin_client, db):
    response = await post_form(
        admin_client,
        "/admin/users/00000000-0000-0000-0000-000000000000/rename",
        {"full_name": "Personne"},
    )
    assert response.status_code == 404


async def test_rename_requires_superadmin(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    other = await make_user("other@ex.lu", "Autre Médecin", clinic=clinic)
    await make_user("doc@ex.lu", clinic=clinic)
    await login(client, "doc@ex.lu")
    response = await post_form(client, f"/admin/users/{other.id}/rename", {"full_name": "Pirate"})
    assert response.status_code == 403
    await db.refresh(other)
    assert other.full_name == "Autre Médecin"


# =========================================================================================
# Phase 8 — admin extras
# =========================================================================================


CLINIC_PAGES = [
    "/",
    "/semaine",
    "/mois?mois=2027-05",
    "/conges",
    "/sur-place",
    "/generer",
    "/resultats",
    "/parametres",
]


@pytest.fixture
async def cabinet(db, make_clinic, make_user):
    """Clinic with two doctors and an active May 2027 generation."""
    clinic = await make_clinic("Cabinet du Parc")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    generation = await scheduling_repo.create_generation(
        db,
        clinic.id,
        date(2027, 5, 1),
        date(2027, 5, 31),
        nr.id,
        {date(2027, 5, d): (nr.id if d % 2 else dt.id) for d in range(1, 32)},
    )
    await db.commit()
    return clinic, nr, dt, generation


async def _session_row(db):
    db.expunge_all()
    return await db.scalar(select(UserSession))


# --- Impersonation ------------------------------------------------------------------------


async def test_impersonation_full_flow(admin_client, db, cabinet):
    _clinic, nr, _, _ = cabinet
    start = await post_form(admin_client, f"/admin/users/{nr.id}/impersonate")
    assert start.status_code == 303 and start.headers["location"] == "/"
    assert (await _session_row(db)).impersonating_user_id == nr.id

    # Effective user is NR: same pages as NR, with the banner.
    home = await admin_client.get("/")
    assert home.status_code == 200
    assert "Vous agissez en tant que <strong>Nicolas Rousseau</strong>" in home.text
    assert "Cabinet du Parc" in home.text
    assert 'data-self="NR"' in home.text  # presence as NR, like the real user would be
    conges = await admin_client.get("/conges")
    assert "Mes congés" in conges.text

    # Admin pages stay reachable while impersonating, banner still there.
    admin_page = await admin_client.get("/admin/users")
    assert admin_page.status_code == 200
    assert "Vous agissez en tant que" in admin_page.text

    # Actions while impersonating are attributed and traced to the superadmin.
    await post_form(admin_client, "/conges/toggle", {"date": "2027-05-10"})
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "vacation_added"))
    assert entry.user_id == nr.id
    assert entry.meta["impersonated_by"] == str(admin_client.admin.id)
    holiday = await db.scalar(select(Holiday))
    assert holiday.user_id == nr.id

    exit_ = await post_form(admin_client, "/admin/impersonate/exit")
    assert exit_.status_code == 303 and exit_.headers["location"] == "/admin/users"
    assert (await _session_row(db)).impersonating_user_id is None
    back = await admin_client.get("/")
    assert back.status_code == 303 and back.headers["location"] == "/admin"
    actions = (await db.scalars(select(AuditLog.action).order_by(AuditLog.id))).all()
    assert "impersonation_started" in actions and "impersonation_ended" in actions


async def test_banner_on_every_clinic_page_and_in_plus_sheet(admin_client, db, cabinet):
    _, nr, _, _ = cabinet
    await post_form(admin_client, f"/admin/users/{nr.id}/impersonate")
    for url in CLINIC_PAGES:
        page = await admin_client.get(url)
        assert page.status_code == 200, url
        assert 'class="mode-banner impersonating"' in page.text, url
        sheet = page.text[page.text.index('class="sheet"') :]
        assert "Quitter l'identité de NR" in sheet, url


async def test_cannot_impersonate_superadmin_or_inactive(admin_client, db, cabinet, make_user):
    other_admin = await make_user("root2@ex.lu", "Autre Admin", is_superadmin=True)
    _, _, dt, _ = cabinet
    dt.active = False
    await db.commit()
    for target in (other_admin, dt):
        response = await post_form(admin_client, f"/admin/users/{target.id}/impersonate")
        assert response.status_code == 303 and response.headers["location"] == "/admin/users"
        assert (await _session_row(db)).impersonating_user_id is None


async def test_password_change_blocked_while_impersonating(admin_client, db, cabinet):
    _, nr, _, _ = cabinet
    await post_form(admin_client, f"/admin/users/{nr.id}/impersonate")
    response = await post_form(
        admin_client,
        "/parametres/mot-de-passe",
        {
            "current_password": DEFAULT_PASSWORD,
            "password": "hijacked-password",
            "password_confirm": "hijacked-password",
        },
    )
    assert response.status_code == 403


async def test_profile_change_blocked_while_impersonating(admin_client, db, cabinet):
    _, nr, _, _ = cabinet
    await post_form(admin_client, f"/admin/users/{nr.id}/impersonate")
    assert "/parametres/profil" not in (await admin_client.get("/parametres")).text
    response = await post_form(admin_client, "/parametres/profil", {"full_name": "Hijacked"})
    assert response.status_code == 403
    await db.refresh(nr)
    assert nr.full_name != "Hijacked"


async def test_clinic_user_cannot_impersonate(client, db, cabinet):
    _, _nr, dt, _ = cabinet
    await login(client, "nr@ex.lu")
    response = await post_form(client, f"/admin/users/{dt.id}/impersonate")
    assert response.status_code == 403
    assert (await _session_row(db)).impersonating_user_id is None


async def test_impersonation_column_ignored_for_clinic_users(
    client, db, cabinet, make_clinic, make_user
):
    """Even if the columns were set (impossible through the app), a clinic user stays themselves."""
    _, _nr, _, _ = cabinet
    other = await make_clinic("Autre")
    bob = await make_user("bob@ex.lu", "Bob Weber", clinic=other)
    await login(client, "nr@ex.lu")
    session = await _session_row(db)
    session.impersonating_user_id = bob.id
    session.viewing_clinic_id = other.id
    db.add(session)
    await db.commit()
    page = await client.get("/")
    assert "Cabinet du Parc" in page.text and "Autre" not in page.text
    assert "mode-banner" not in page.text
    assert (await client.get("/admin/users")).status_code == 403


# --- Read-only clinic view ----------------------------------------------------------------


async def test_read_only_view_gets_ok_posts_forbidden(admin_client, db, cabinet):
    clinic, *_ = cabinet
    sheet = await admin_client.get(f"/admin/clinic/{clinic.id}")
    assert sheet.status_code == 200 and "Ouvrir en lecture seule" in sheet.text
    start = await post_form(admin_client, f"/admin/clinic/{clinic.id}/view")
    assert start.headers["location"] == "/"

    for url in [*CLINIC_PAGES[:-1], "/resultats/pdf", "/generer/medecins"]:
        page = await admin_client.get(url)
        assert page.status_code == 200, url
        if "pdf" not in url and "medecins" not in url:  # full pages only (not PDF / fragment)
            assert 'class="mode-banner read-only"' in page.text, url
    month = await admin_client.get("/mois?mois=2027-05")
    assert "Cabinet du Parc" in month.text and "/resultats/jour/" not in month.text  # no edit links

    for url, data in [
        ("/conges/toggle", {"date": "2027-05-10"}),
        ("/sur-place/toggle", {"date": "2027-05-10"}),
        ("/generer", {"period_from": "2027-05-01", "period_to": "2027-05-31"}),
        ("/resultats/jour/2027-05-03", {"doctor": "DT"}),
        ("/resultats/enregistrer", {}),
        ("/resultats/annuler", {}),
        ("/sse/heartbeat", {}),
    ]:
        response = await post_form(admin_client, url, data)
        assert response.status_code == 403, url
    assert await db.scalar(select(func.count(Generation.id))) == 1

    exit_ = await post_form(admin_client, "/admin/view/exit")
    assert exit_.headers["location"] == f"/admin/clinic/{clinic.id}"
    assert (await admin_client.get("/")).headers["location"] == "/admin"


# --- Custom holidays ----------------------------------------------------------------------


async def test_custom_holidays_add_merge_render_and_delete(admin_client, app, db, cabinet):
    clinic, *_ = cabinet
    tuesday = date(2027, 5, 11)
    response = await post_form(
        admin_client,
        f"/admin/clinic/{clinic.id}/holidays",
        {"date": tuesday.isoformat(), "label": "Fermeture du cabinet"},
    )
    assert response.status_code == 303
    duplicate = await post_form(
        admin_client,
        f"/admin/clinic/{clinic.id}/holidays",
        {"date": tuesday.isoformat(), "label": "Doublon"},
    )
    assert duplicate.status_code == 303
    holiday = await db.scalar(select(CustomHoliday))
    assert holiday.label == "Fermeture du cabinet"
    assert await db.scalar(select(func.count(CustomHoliday.id))) == 1

    merged = effective_holidays(2027, {holiday.date: holiday.label})
    assert merged[tuesday] == "Fermeture du cabinet" and len(merged) == 12
    page = await admin_client.get(f"/admin/clinic/{clinic.id}/holidays?year=2027")
    assert "Fermeture du cabinet" in page.text and "Lundi de Pentecôte" in page.text

    # The clinic sees it grey in the month and counted as week-end in the stats.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as dt:
        dt.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(dt, "dt@ex.lu")
        month = (await dt.get("/mois?mois=2027-05")).text
    cell = month[month.index("mardi 11 mai 2027") - 200 : month.index("mardi 11 mai 2027")]
    assert "weekend" in cell
    # May 2027: 10 weekend days + Ascension + Whit Monday + custom Tuesday = 13
    stats = await scheduling_stats(db, clinic)
    assert stats.total_weekend == 13 and stats.total_week == 18

    await post_form(admin_client, f"/admin/clinic/{clinic.id}/holidays/{holiday.id}/delete")
    assert await db.scalar(select(func.count(CustomHoliday.id))) == 0
    assert (await scheduling_stats(db, clinic)).total_weekend == 12
    actions = set((await db.scalars(select(AuditLog.action))).all())
    assert {"custom_holiday_added", "custom_holiday_removed"} <= actions


async def scheduling_stats(db, clinic):
    db.expunge_all()
    (generation,) = await scheduling_repo.active_generations(db, clinic.id)
    return (await results_stats(db, clinic.id, generation)).stats


async def test_custom_holiday_of_other_clinic_cannot_be_deleted_here(
    admin_client, db, cabinet, make_clinic
):
    clinic, *_ = cabinet
    other = await make_clinic("Autre")
    foreign = CustomHoliday(clinic_id=other.id, date=date(2027, 5, 11), label="Ailleurs")
    db.add(foreign)
    await db.commit()
    response = await post_form(
        admin_client, f"/admin/clinic/{clinic.id}/holidays/{foreign.id}/delete"
    )
    assert response.status_code == 404


async def test_custom_holidays_feed_the_scheduler_stats_on_generation(client, db, cabinet):
    clinic, *_ = cabinet
    db.add(CustomHoliday(clinic_id=clinic.id, date=date(2027, 5, 11), label="Fermeture"))
    await db.commit()
    await login(client, "nr@ex.lu")
    await post_form(
        client,
        "/generer",
        {"period_from": "2027-05-01", "period_to": "2027-05-31", "overlap_mode": "recalc"},
    )
    assert (await scheduling_stats(db, clinic)).total_weekend == 13


# --- Generations --------------------------------------------------------------------------


async def test_archive_active_generation(admin_client, db, cabinet):
    clinic, _, _, generation = cabinet
    page = await admin_client.get(f"/admin/clinic/{clinic.id}/generations")
    assert "Archiver" in page.text and "Supprimer définitivement" in page.text
    await post_form(admin_client, f"/admin/generations/{generation.id}/archive")
    db.expunge_all()
    archived = await db.get(Generation, generation.id)
    assert archived.is_active is False and archived.archived_at is not None
    assert await scheduling_repo.active_generations(db, clinic.id) == []
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "generation_archived"))
    recent = await admin_client.get("/admin/generations")
    assert "Cabinet du Parc" in recent.text and "Archivée" in recent.text


@pytest.mark.parametrize("password", [None, "", "wrong-password-123"])
async def test_delete_generation_requires_password(admin_client, db, cabinet, password):
    _, _, _, generation = cabinet
    data = {} if password is None else {"password": password}
    response = await post_form(admin_client, f"/admin/generations/{generation.id}/delete", data)
    assert response.status_code == 403
    db.expunge_all()
    assert await db.get(Generation, generation.id) is not None
    assert await db.scalar(select(AuditLog).where(AuditLog.action == "reauth_failed"))


async def test_delete_generation_with_password(admin_client, db, cabinet):
    clinic, _, _, generation = cabinet
    response = await post_form(
        admin_client, f"/admin/generations/{generation.id}/delete", {"password": DEFAULT_PASSWORD}
    )
    assert response.status_code == 303
    db.expunge_all()
    assert await db.get(Generation, generation.id) is None
    assert await db.scalar(select(func.count(Assignment.id))) == 0
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "generation_deleted"))
    assert entry.meta["was_active"] is True and entry.clinic_id == clinic.id


# --- Audit viewer -------------------------------------------------------------------------


@pytest.fixture
async def audit_dataset(db, cabinet, admin_client):
    clinic, nr, dt, _ = cabinet
    old = datetime(2027, 1, 15, 10, 0, tzinfo=UTC)
    db.add_all(
        [
            AuditLog(action="login", user_id=nr.id, clinic_id=clinic.id, created_at=old),
            AuditLog(
                action="login",
                user_id=dt.id,
                clinic_id=clinic.id,
                created_at=old + timedelta(days=1),
            ),
            AuditLog(
                action="login_failed",
                user_id=nr.id,
                clinic_id=clinic.id,
                created_at=old + timedelta(days=2),
                meta={"reason": "bad_password"},
                ip_address="203.0.113.7",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                    "Chrome/126.0 Safari/537.36"
                ),
            ),
            AuditLog(
                action="assignment_edited",
                user_id=dt.id,
                clinic_id=clinic.id,
                created_at=old + timedelta(days=3),
                target_type="assignment",
                meta={"date": "2027-05-03", "old": "NR", "new": "DT"},
            ),
        ]
    )
    await db.commit()
    return clinic, nr, dt


async def test_audit_filter_by_user_and_action(admin_client, db, audit_dataset):
    _, nr, _ = audit_dataset
    page = await admin_client.get(f"/admin/audit?user={nr.id}&action=login_failed")
    assert page.status_code == 200
    assert page.text.count('<span class="badge action warn">login_failed</span>') == 1
    assert 'badge action ">login</span>' not in page.text
    assert "assignment_edited</span>" not in page.text
    assert "Chrome · macOS" in page.text and "203.0.113.7" in page.text
    assert f'href="/admin/users/{nr.id}"' in page.text


async def test_audit_filter_by_clinic_and_dates(admin_client, db, audit_dataset):
    clinic, _, _ = audit_dataset
    page = await admin_client.get(f"/admin/audit?clinic={clinic.id}&du=2027-01-16&au=2027-01-17")
    assert "2 entrées" in page.text
    assert "16/01/2027" in page.text and "17/01/2027" in page.text and "15/01/2027" not in page.text


async def test_audit_filter_action_shows_pretty_metadata(admin_client, db, audit_dataset):
    page = await admin_client.get("/admin/audit?action=assignment_edited&du=2027-01-01")
    assert "1 entrée" in page.text
    assert "&#34;new&#34;: &#34;DT&#34;" in page.text  # indented JSON, autoescaped
    options = page.text[page.text.index('id="f-action"') :]
    assert '<option value="assignment_edited" selected>' in options


async def test_audit_pagination(admin_client, db, cabinet):
    clinic, nr, *_ = cabinet
    db.add_all(
        AuditLog(action="heartbeat_test", user_id=nr.id, clinic_id=clinic.id) for _ in range(120)
    )
    await db.commit()
    first = await admin_client.get("/admin/audit?action=heartbeat_test")
    assert first.text.count("heartbeat_test</span>") == 50 and "Page 1 / 3" in first.text
    last = await admin_client.get("/admin/audit?action=heartbeat_test&page=3")
    assert last.text.count("heartbeat_test</span>") == 20
    assert (await admin_client.get("/admin/audit?user=garbage&du=nope")).status_code == 200


# --- Stats --------------------------------------------------------------------------------


async def test_stats_numbers(admin_client, db, cabinet, make_clinic, make_user):
    clinic, nr, *_ = cabinet
    closed = await make_clinic("Fermé", active=False)
    await make_user("old@ex.lu", "Ancien Médecin", clinic=closed, active=False)
    db.add_all(
        [
            AuditLog(
                action="pdf_sent",
                clinic_id=clinic.id,
                meta={
                    "sent": 2,
                    "failed": 1,
                    "recipients": [
                        {"email": "a@ex.lu", "ok": True},
                        {"email": "b@ex.lu", "ok": True},
                        {
                            "email": "c@ex.lu",
                            "ok": False,
                            "error": "SMTPRecipientsRefused",
                            "message": "550 mailbox unavailable",
                        },
                    ],
                },
            ),
            AuditLog(
                action="pdf_sent",
                clinic_id=clinic.id,
                meta={"sent": 0, "failed": 0, "recipients": [], "error": "pdf: OSError: disk full"},
            ),
            AuditLog(
                action="pdf_sent",
                clinic_id=clinic.id,
                created_at=datetime.now(UTC) - timedelta(days=40),
                meta={"sent": 5, "failed": 5, "recipients": []},
            ),
            AuditLog(
                action="login_failed",
                meta={"reason": "unknown_email", "email": "intrus@ex.lu"},
                ip_address="198.51.100.9",
            ),
        ]
    )
    old_generation = Generation(
        clinic_id=clinic.id,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 6, 30),
        is_active=False,
        generated_at=datetime.now(UTC) - timedelta(days=60),
        archived_at=datetime.now(UTC) - timedelta(days=30),
    )
    db.add(old_generation)
    await db.commit()

    s = await platform_stats(db)
    assert (s.clinics_active, s.clinics_inactive) == (1, 1)
    assert (s.users_active, s.users_inactive, s.superadmins) == (2, 1, 1)
    assert [(c.name, n) for c, n in s.generations_by_clinic] == [("Cabinet du Parc", 1)]
    assert (s.emails_sent, s.emails_failed) == (2, 1)
    assert sorted(r["message"] for _, r in s.email_errors) == [
        "550 mailbox unavailable",
        "pdf: OSError: disk full",
    ]
    assert [e.meta["email"] for e in s.failed_logins] == ["intrus@ex.lu"]

    page = await admin_client.get("/admin/stats")
    assert page.status_code == 200
    assert "550 mailbox unavailable" in page.text and "intrus@ex.lu" in page.text
    db.add(AuditLog(action="login_failed", user_id=nr.id, meta={"reason": "bad_password"}))
    await db.commit()
    page = await admin_client.get("/admin/stats")
    assert f'<a href="/admin/users/{nr.id}">Nicolas Rousseau</a>' in page.text
    assert "groupes actifs" in page.text


async def test_user_profile_page(admin_client, db, cabinet):
    _, nr, _, _ = cabinet
    page = await admin_client.get(f"/admin/users/{nr.id}")
    assert page.status_code == 200
    assert "Se connecter en tant que" in page.text and "Activité récente" in page.text
    assert (
        await admin_client.get("/admin/users/00000000-0000-0000-0000-000000000000")
    ).status_code == 404


# --- Open redirect hardening of the admin redirect helpers (CodeQL py/url-redirection) ---------


@pytest.mark.parametrize("module", ["app.routers.admin", "app.routers.admin_ops"])
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://evil.com", "/admin"),
        ("//evil.com", "/admin"),
        ("/admin/clinic/x?u=https://evil.com", "/admin"),
        ("/admin/users", "/admin/users"),
    ],
)
def test_admin_redirect_helpers_stay_local(module, url, expected):
    import importlib

    redirect = importlib.import_module(module)._redirect
    assert redirect(url).headers["location"] == expected


async def test_admin_redirects_still_reach_their_pages(admin_client, db, make_clinic):
    clinic = await make_clinic()
    response = await post_form(admin_client, f"/admin/clinics/{clinic.id}/toggle-active")
    assert response.status_code == 303 and response.headers["location"] == "/admin/clinics"
