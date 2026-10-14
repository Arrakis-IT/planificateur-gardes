# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Opt-out of notification emails: settings toggle, one-click link, sending rules."""

import json
import re
import time
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.models import AuditLog
from app.services.unsubscribe import unsubscribe_url
from tests.conftest import login, post_form, with_csrf

pytestmark = pytest.mark.db

PERIOD = {"period_from": "2027-04-01", "period_to": "2027-04-30"}


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    clinic = await make_clinic()
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    await login(client, "nr@ex.lu")
    return clinic, nr, dt


async def _toggles(db) -> list[dict]:
    stmt = (
        select(AuditLog.meta)
        .where(AuditLog.action == "email_notifications_toggled")
        .order_by(AuditLog.created_at)
    )
    return list((await db.scalars(stmt)).all())


@contextmanager
def _clock(offset_seconds: float):
    """Create itsdangerous tokens as if it were `offset_seconds` from now."""
    real = time.time
    time.time = lambda: real() + offset_seconds  # type: ignore[assignment]
    try:
        yield
    finally:
        time.time = real  # type: ignore[assignment]


# --- Settings toggle ----------------------------------------------------------------------


async def test_settings_toggle_persists_immediately(client, db, team):
    _, nr, _ = team
    page = (await client.get("/parametres")).text
    assert "Préférences" in page and 'id="email_notifications"' in page
    assert 'hx-post="/parametres/notifications" hx-trigger="change"' in page
    assert re.search(r'id="email_notifications"[^>]*checked', page)

    # Unchecked box: the field is absent.
    off = await client.post(
        "/parametres/notifications", data=with_csrf({}), headers={"HX-Request": "true"}
    )
    assert off.status_code == 204
    toast = json.loads(off.headers["HX-Trigger"])["toast"]
    assert toast["message"] == "Vous ne recevrez plus les emails de notification de planification."
    await db.refresh(nr)
    assert nr.email_notifications_enabled is False
    assert not re.search(
        r'id="email_notifications"[^>]*checked', (await client.get("/parametres")).text
    )

    on = await post_form(client, "/parametres/notifications", {"enabled": "true"})  # no JS
    assert on.status_code == 303
    await db.refresh(nr)
    assert nr.email_notifications_enabled is True
    assert await _toggles(db) == [
        {"enabled": False, "source": "settings"},
        {"enabled": True, "source": "settings"},
    ]


# --- One-click link ----------------------------------------------------------------------


async def test_one_click_link_disables_without_login(client, db, team):
    _, _, dt = team
    path = unsubscribe_url(get_settings(), dt.id).removeprefix("http://testserver")
    assert path.startswith("/desabonnement/")
    await post_form(client, "/logout")  # anonymous
    page = await client.get(path)
    assert page.status_code == 200
    assert "Vous ne recevrez plus d'emails de notification." in page.text
    assert 'href="/parametres">Réactiver depuis Paramètres' in page.text
    await db.refresh(dt)
    assert dt.email_notifications_enabled is False
    assert await _toggles(db) == [{"enabled": False, "source": "one_click_link"}]
    assert (await client.get(path)).status_code == 200  # idempotent, no second audit entry
    assert len(await _toggles(db)) == 1


async def test_tampered_or_expired_link_is_rejected(client, db, team):
    _, _, dt = team
    url = unsubscribe_url(get_settings(), dt.id).removeprefix("http://testserver")
    tampered = url[:-3] + ("aaa" if not url.endswith("aaa") else "bbb")
    assert (await client.get(tampered)).status_code == 400
    assert (await client.get("/desabonnement/not-a-token")).status_code == 400
    with _clock(-366 * 24 * 3600):  # issued a year and a day ago
        old = unsubscribe_url(get_settings(), dt.id).removeprefix("http://testserver")
    expired = await client.get(old)
    assert expired.status_code == 400 and "invalide ou expiré" in expired.text
    with _clock(-300 * 24 * 3600):  # still within the year
        recent = unsubscribe_url(get_settings(), dt.id).removeprefix("http://testserver")
    await db.refresh(dt)
    assert dt.email_notifications_enabled is True
    assert (await client.get(recent)).status_code == 200


# --- Sending rules ---------------------------------------------------------------------------


async def test_schedule_email_skips_opted_out_users(client, db, team, mailer):
    _, nr, dt = team
    dt.email_notifications_enabled = False
    await db.commit()
    response = await post_form(client, "/generer", PERIOD)
    assert response.status_code == 303
    assert [m.to for m in mailer.sent] == ["nr@ex.lu"]
    message = mailer.sent[0]
    assert (
        "Vous recevez cet email car vous êtes inscrit sur Planificateur des gardes." in message.text
    )
    link = re.search(r"Se désabonner : (\S+)", message.text).group(1)
    assert link == unsubscribe_url(get_settings(), nr.id)
    assert f'href="{link}"' in message.html and ">Se désabonner</a>" in message.html
    assert dict(message.headers)["List-Unsubscribe"] == f"<{link}>"
    meta = await db.scalar(select(AuditLog.meta).where(AuditLog.action == "pdf_sent"))
    assert meta["opted_out"] == ["dt@ex.lu"] and meta["sent"] == 1 and meta["failed"] == 0


async def test_transactional_emails_ignore_the_opt_out(client, db, team, mailer, make_user):
    _, nr, _ = team
    nr.email_notifications_enabled = False
    await db.commit()
    await post_form(client, "/logout")
    await post_form(client, "/reset", {"email": "nr@ex.lu"})  # password reset
    assert [m.to for m in mailer.sent] == ["nr@ex.lu"]
    assert "Se désabonner" not in mailer.sent[0].text

    await make_user("root@ex.lu", "Super Admin", is_superadmin=True)
    await login(client, "root@ex.lu")
    await post_form(client, f"/admin/users/{nr.id}/resend-invitation")  # invitation
    assert [m.to for m in mailer.sent] == ["nr@ex.lu", "nr@ex.lu"]
    assert mailer.sent[1].subject.startswith("Bienvenue")
