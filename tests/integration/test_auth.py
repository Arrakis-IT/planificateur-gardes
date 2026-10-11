# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Login, logout, account lock, password change and reset flows."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.middleware.rate_limit import limiter
from app.models import AuditLog, PasswordResetToken, UserSession
from app.services.auth import SESSION_COOKIE_NAME
from tests.conftest import DEFAULT_PASSWORD, login, post_form

pytestmark = pytest.mark.db


async def _actions(db, action: str) -> int:
    return int(await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == action)))


# --- Login / logout ---------------------------------------------------------------------


async def test_login_page_renders_with_csrf_field(client, db):
    response = await client.get("/login")
    assert response.status_code == 200
    assert 'name="csrf_token"' in response.text
    assert "Connexion" in response.text


async def test_anonymous_is_redirected_to_login_with_next(client, db):
    response = await client.get("/parametres")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/parametres"


async def test_login_success_sets_hardened_cookie_and_audits(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("doc@example.lu", clinic=clinic)
    response = await login(client, "DOC@example.lu")  # email is case-insensitive
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    set_cookie = response.headers["set-cookie"].lower()
    assert SESSION_COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie
    assert await _actions(db, "login") == 1

    home = await client.get("/")
    assert home.status_code == 200
    assert clinic.name in home.text and "Déconnexion" in home.text
    await db.refresh(user)
    assert user.last_login_at is not None


async def test_login_follows_safe_next_only(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    response = await post_form(
        client,
        "/login",
        {"email": "doc@example.lu", "password": DEFAULT_PASSWORD, "next": "//evil.example"},
    )
    assert response.headers["location"] == "/"


async def test_superadmin_lands_on_admin(client, db, make_user):
    await make_user("admin@example.lu", "Juan Admin", is_superadmin=True)
    response = await login(client, "admin@example.lu")
    assert response.headers["location"] == "/admin"


async def test_wrong_password_is_generic_and_audited(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    bad = await login(client, "doc@example.lu", "wrong-password-xyz")
    unknown = await login(client, "nobody@example.lu", "wrong-password-xyz")
    assert bad.status_code == unknown.status_code == 401
    assert "Identifiants invalides" in bad.text and "Identifiants invalides" in unknown.text
    assert await _actions(db, "login_failed") == 2
    assert SESSION_COOKIE_NAME not in client.cookies


@pytest.mark.parametrize("what", ["user", "clinic"])
async def test_inactive_user_or_clinic_cannot_login(client, db, make_clinic, make_user, what):
    clinic = await make_clinic(active=what != "clinic")
    await make_user("doc@example.lu", clinic=clinic, active=what != "user")
    response = await login(client, "doc@example.lu")
    assert response.status_code == 401


async def test_account_locks_after_threshold_failures(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("doc@example.lu", clinic=clinic)
    # Bypass the per-IP limit (5/min) to reach the per-account threshold (10).
    limiter.enabled = False
    try:
        for _ in range(10):
            await login(client, "doc@example.lu", "wrong-password-xyz")
        assert await _actions(db, "account_locked") == 1
        # Even the right password is refused while locked.
        response = await login(client, "doc@example.lu")
        assert response.status_code == 401

        # Lock expires after LOGIN_LOCK_MINUTES.
        past = datetime.now(UTC) - timedelta(minutes=16)
        await db.execute(
            update(AuditLog).where(AuditLog.user_id == user.id).values(created_at=past)
        )
        await db.commit()
        response = await login(client, "doc@example.lu")
        assert response.status_code == 303
    finally:
        limiter.enabled = True


async def test_login_rate_limited_per_ip(client, db):
    for _ in range(5):
        response = await login(client, "nobody@example.lu", "wrong-password-xyz")
        assert response.status_code == 401
    response = await login(client, "nobody@example.lu", "wrong-password-xyz")
    assert response.status_code == 429


async def test_turnstile_failure_blocks_login(client, db, turnstile, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    turnstile.accept = False
    response = await login(client, "doc@example.lu")
    assert response.status_code == 400
    assert "anti-robot" in response.text
    assert turnstile.calls == 1


async def test_logout_revokes_session(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await login(client, "doc@example.lu")
    stolen_cookie = client.cookies[SESSION_COOKIE_NAME]
    response = await post_form(client, "/logout")
    assert response.status_code == 303
    assert await db.scalar(select(func.count(UserSession.id))) == 0
    # Replaying the old cookie no longer works.
    client.cookies.set(SESSION_COOKIE_NAME, stolen_cookie)
    assert (await client.get("/")).status_code == 303


async def test_login_rotates_existing_session(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await login(client, "doc@example.lu")
    first = client.cookies[SESSION_COOKIE_NAME]
    await login(client, "doc@example.lu")
    assert client.cookies[SESSION_COOKIE_NAME] != first
    assert await db.scalar(select(func.count(UserSession.id))) == 1


async def test_tampered_or_expired_session_is_rejected(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await login(client, "doc@example.lu")
    await db.execute(update(UserSession).values(expires_at=datetime.now(UTC) - timedelta(1)))
    await db.commit()
    assert (await client.get("/")).status_code == 303
    client.cookies.set(SESSION_COOKIE_NAME, "forged.value")
    assert (await client.get("/")).status_code == 303


# --- CSRF -------------------------------------------------------------------------------


async def test_post_without_csrf_token_is_rejected(client, db):
    response = await client.post("/login", data={"email": "a@b.lu", "password": "x"})
    assert response.status_code == 403


async def test_post_with_token_for_other_cookie_is_rejected(client, db):
    client.cookies.set("csrftoken", "another-cookie-value-0123456789abcdefgh")
    response = await post_form(client, "/login", {"email": "a@b.lu", "password": "x"})
    assert response.status_code == 403


async def test_csrf_cookie_issued_on_first_visit(app, db):
    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as fresh:
        response = await fresh.get("/login")
        assert "csrftoken=" in response.headers["set-cookie"]
        assert "httponly" in response.headers["set-cookie"].lower()


# --- Forced password change -------------------------------------------------------------


async def test_must_change_password_blocks_other_routes(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic, must_change_password=True)
    response = await login(client, "doc@example.lu")
    assert response.headers["location"] == "/parametres"
    blocked = await client.get("/")
    assert blocked.status_code == 303 and blocked.headers["location"] == "/parametres"
    page = await client.get("/parametres")
    assert "Vous devez choisir un nouveau mot de passe" in page.text


async def test_change_password_rotates_sessions(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    user = await make_user("doc@example.lu", clinic=clinic, must_change_password=True)
    await login(client, "doc@example.lu")
    old_cookie = client.cookies[SESSION_COOKIE_NAME]

    bad = await post_form(
        client,
        "/parametres/mot-de-passe",
        {"current_password": "nope", "password": "x" * 12, "password_confirm": "x" * 12},
    )
    assert bad.status_code == 400 and "actuel est incorrect" in bad.text

    same = await post_form(
        client,
        "/parametres/mot-de-passe",
        {
            "current_password": DEFAULT_PASSWORD,
            "password": DEFAULT_PASSWORD,
            "password_confirm": DEFAULT_PASSWORD,
        },
    )
    assert same.status_code == 400 and "différent" in same.text

    ok = await post_form(
        client,
        "/parametres/mot-de-passe",
        {
            "current_password": DEFAULT_PASSWORD,
            "password": "brand-new-passphrase",
            "password_confirm": "brand-new-passphrase",
        },
    )
    assert ok.status_code == 303 and ok.headers["location"] == "/"
    assert client.cookies[SESSION_COOKIE_NAME] != old_cookie
    assert (await client.get("/")).status_code == 200
    await db.refresh(user)
    assert user.must_change_password is False
    assert await _actions(db, "password_changed") == 1


# --- Password reset ---------------------------------------------------------------------


async def test_reset_flow_end_to_end(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    response = await post_form(client, "/reset", {"email": "doc@example.lu"})
    assert response.status_code == 200 and "Vérifiez votre messagerie" in response.text
    assert len(mailer.sent) == 1 and mailer.sent[0].to == "doc@example.lu"
    link = mailer.last_link()
    # Only the hash is stored.
    raw_token = link.rsplit("/", 1)[1]
    stored = await db.scalar(select(PasswordResetToken.token_hash))
    assert stored and raw_token not in stored

    form = await client.get(link)
    assert form.status_code == 200 and "Nouveau mot de passe" in form.text
    mismatch = await post_form(client, link, {"password": "a" * 12, "password_confirm": "b" * 12})
    assert mismatch.status_code == 400
    done = await post_form(
        client, link, {"password": "reset-passphrase!", "password_confirm": "reset-passphrase!"}
    )
    assert done.status_code == 303 and done.headers["location"] == "/login"

    # Single use.
    assert (await client.get(link)).status_code == 400
    assert (await login(client, "doc@example.lu", "reset-passphrase!")).status_code == 303


async def test_reset_unknown_email_looks_identical(client, db, mailer):
    response = await post_form(client, "/reset", {"email": "nobody@example.lu"})
    assert response.status_code == 200 and "Vérifiez votre messagerie" in response.text
    assert mailer.sent == []


async def test_reset_throttled_per_account(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    for _ in range(4):
        response = await post_form(client, "/reset", {"email": "doc@example.lu"})
        assert response.status_code == 200
    assert len(mailer.sent) == 3
    assert await _actions(db, "password_reset_throttled") == 1


async def test_reset_new_link_invalidates_previous(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await post_form(client, "/reset", {"email": "doc@example.lu"})
    first = mailer.last_link()
    await post_form(client, "/reset", {"email": "doc@example.lu"})
    assert (await client.get(first)).status_code == 400
    assert (await client.get(mailer.last_link())).status_code == 200


async def test_reset_expired_link_rejected(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await post_form(client, "/reset", {"email": "doc@example.lu"})
    await db.execute(
        update(PasswordResetToken).values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
    )
    await db.commit()
    assert (await client.get(mailer.last_link())).status_code == 400


async def test_reset_mail_failure_is_not_disclosed(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    mailer.fail = True
    response = await post_form(client, "/reset", {"email": "doc@example.lu"})
    assert response.status_code == 200 and "Vérifiez votre messagerie" in response.text


async def test_reset_revokes_existing_sessions(client, db, mailer, make_clinic, make_user):
    clinic = await make_clinic()
    await make_user("doc@example.lu", clinic=clinic)
    await login(client, "doc@example.lu")
    await post_form(client, "/reset", {"email": "doc@example.lu"})
    await post_form(
        client,
        mailer.last_link(),
        {"password": "reset-passphrase!", "password_confirm": "reset-passphrase!"},
    )
    assert await db.scalar(select(func.count(UserSession.id))) == 0
    assert (await client.get("/")).status_code == 303
