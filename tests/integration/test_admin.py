# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Superadmin panel: access control, clinics and users management."""

import re

import pytest
from sqlalchemy import func, select

from app.models import AuditLog, Clinic, User, UserSession
from app.services.doctors import DOCTOR_PALETTE
from tests.conftest import login, post_form

pytestmark = pytest.mark.db


@pytest.fixture
async def admin_client(client, make_user):
    admin = await make_user("admin@example.lu", "Juan Admin", is_superadmin=True)
    await login(client, "admin@example.lu")
    client.admin = admin
    return client


# --- Access control ---------------------------------------------------------------------


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


# --- Clinics --------------------------------------------------------------------------


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


# --- Users -----------------------------------------------------------------------------


async def test_create_users_assigns_codes_and_colors(admin_client, db, make_clinic):
    clinic = await make_clinic()
    for email, name in [("a@ex.lu", "Nicolas Rousseau"), ("b@ex.lu", "Nadia Roth")]:
        response = await post_form(
            admin_client,
            "/admin/users",
            {
                "email": email,
                "full_name": name,
                "password": "initial-password-1",
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
    assert all(u.must_change_password for u in users)


async def test_create_user_validation_errors(admin_client, db, make_clinic):
    clinic = await make_clinic()
    base = {"email": "a@ex.lu", "full_name": "Anne Dupont", "password": "initial-password-1"}

    no_clinic = await post_form(admin_client, "/admin/users", base)
    assert no_clinic.status_code == 400 and "Choisissez un cabinet" in no_clinic.text

    weak = await post_form(
        admin_client, "/admin/users", base | {"password": "short", "clinic_id": str(clinic.id)}
    )
    assert weak.status_code == 400 and "au moins 12" in weak.text

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
            "password": "initial-password-1",
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


async def test_admin_created_and_reset_users_must_change_password(
    admin_client, app, db, make_clinic
):
    """Both admin-issued passwords force a change at next login (like the bootstrap admin)."""
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import TEST_CSRF_COOKIE

    clinic = await make_clinic()
    await post_form(
        admin_client,
        "/admin/users",
        {
            "email": "new@ex.lu",
            "full_name": "Nouveau Médecin",
            "password": "initial-password-1",
            "clinic_id": str(clinic.id),
        },
    )
    user = await db.scalar(select(User).where(User.email == "new@ex.lu"))
    assert user.must_change_password is True

    async def first_login(password: str) -> str:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://testserver") as doctor:
            doctor.cookies.set("csrftoken", TEST_CSRF_COOKIE)
            response = await login(doctor, "new@ex.lu", password)
            assert response.headers["location"] == "/parametres"
            blocked = await doctor.get("/")
            return blocked.headers["location"]

    assert await first_login("initial-password-1") == "/parametres"

    # Simulate the doctor having changed it, then the admin resets it again.
    user.must_change_password = False
    await db.commit()
    page = await post_form(admin_client, f"/admin/users/{user.id}/reset-password")
    temporary = re.search(r'<code class="secret">([^<]+)</code>', page.text).group(1)
    await db.refresh(user)
    assert user.must_change_password is True
    assert await first_login(temporary) == "/parametres"
