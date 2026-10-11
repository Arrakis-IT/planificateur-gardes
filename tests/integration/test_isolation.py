# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Cross-clinic data isolation (repositories and HTTP)."""

import pytest

from app.models import Clinic, User
from app.repositories import users as users_repo
from app.repositories.scope import NoClinicScopeError, get_clinic_scope
from tests.conftest import login, with_csrf

pytestmark = pytest.mark.db


@pytest.fixture
async def two_clinics(make_clinic, make_user):
    a, b = await make_clinic("Alpha"), await make_clinic("Beta")
    alice = await make_user("alice@ex.lu", "Alice Martin", clinic=a)
    bob = await make_user("bob@ex.lu", "Bob Weber", clinic=b)
    return a, b, alice, bob


async def test_list_clinic_users_is_scoped(db, two_clinics):
    a, b, alice, bob = two_clinics
    assert [u.id for u in await users_repo.list_clinic_users(db, a.id)] == [alice.id]
    assert [u.id for u in await users_repo.list_clinic_users(db, b.id)] == [bob.id]


async def test_get_user_in_clinic_never_crosses_clinics(db, two_clinics):
    a, b, alice, bob = two_clinics
    assert await users_repo.get_user_in_clinic(db, a.id, alice.id) is not None
    assert await users_repo.get_user_in_clinic(db, a.id, bob.id) is None
    assert await users_repo.get_user_in_clinic(db, b.id, alice.id) is None


async def test_doctor_codes_and_colors_are_per_clinic(db, make_clinic, make_user):
    a, b = await make_clinic("Alpha"), await make_clinic("Beta")
    u1 = await make_user("n1@ex.lu", "Nicolas Rousseau", clinic=a)
    u2 = await make_user("n2@ex.lu", "Nicolas Rousseau", clinic=b)
    u3 = await make_user("n3@ex.lu", "Nadia Roth", clinic=a)
    assert u1.doctor_code == u2.doctor_code == "NR"
    assert u1.color == u2.color
    assert u3.doctor_code == "NRO" and u3.color != u1.color


async def test_clinic_scope_helper(db, two_clinics, make_user):
    a, _, alice, _ = two_clinics
    assert get_clinic_scope(alice) == a.id
    admin = await make_user("admin@ex.lu", "Juan Admin", is_superadmin=True)
    with pytest.raises(NoClinicScopeError):
        get_clinic_scope(admin)


async def test_home_page_only_shows_own_clinic(client, db, two_clinics):
    await login(client, "alice@ex.lu")
    page = await client.get("/")
    assert page.status_code == 200
    assert "Alice Martin" in page.text
    assert "Bob Weber" not in page.text
    assert "Beta" not in page.text


# --- HTTP: a clinic-A user acting on clinic-B resources -------------------------------
# Today the only routes addressing a clinic-scoped resource by id are the /admin ones
# (user, clinic). Holiday / on-site / generation / assignment routes (phase 4+) must be
# added to CROSS_CLINIC_CASES when they exist (see TODO.md).

CROSS_CLINIC_CASES = [
    # (verb, path template, resource)
    ("GET", "/admin/users?clinic={clinic_b}", "user"),
    ("POST", "/admin/users/{user_b}/reset-password", "user"),
    ("POST", "/admin/users/{user_b}/toggle-active", "user"),
    ("POST", "/admin/users/{user_b}/delete", "user"),
    ("PATCH", "/admin/users/{user_b}", "user"),
    ("DELETE", "/admin/users/{user_b}", "user"),
    ("GET", "/admin/clinics", "clinic"),
    ("POST", "/admin/clinics/{clinic_b}/rename", "clinic"),
    ("POST", "/admin/clinics/{clinic_b}/toggle-active", "clinic"),
    ("POST", "/admin/clinics/{clinic_b}/delete", "clinic"),
    ("PATCH", "/admin/clinics/{clinic_b}", "clinic"),
    ("DELETE", "/admin/clinics/{clinic_b}", "clinic"),
]


@pytest.mark.parametrize(("verb", "template", "resource"), CROSS_CLINIC_CASES)
async def test_clinic_user_cannot_reach_other_clinic_resources(
    client, db, two_clinics, verb, template, resource
):
    _, clinic_b, _, bob = two_clinics
    before_hash, before_name = bob.password_hash, clinic_b.name
    await login(client, "alice@ex.lu")

    url = template.format(clinic_b=clinic_b.id, user_b=bob.id)
    response = await client.request(
        verb, url, data=with_csrf({"name": "Hijacked"}) if verb != "GET" else None
    )
    # 403 (guarded route) or 405 (no route for that verb): never a success.
    assert response.status_code in {403, 404, 405}, response.status_code
    assert "Bob Weber" not in response.text

    db.expunge_all()
    bob_after = await db.get(User, bob.id)
    clinic_after = await db.get(Clinic, clinic_b.id)
    assert bob_after is not None and bob_after.active
    assert bob_after.password_hash == before_hash
    assert clinic_after is not None and clinic_after.active
    assert clinic_after.name == before_name
