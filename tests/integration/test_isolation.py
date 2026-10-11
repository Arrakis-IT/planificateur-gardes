# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Cross-clinic data isolation (repositories and HTTP)."""

import pytest

from app.repositories import users as users_repo
from app.repositories.scope import NoClinicScopeError, get_clinic_scope
from tests.conftest import login

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
