# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Schedule PDF and email notification (brief §7)."""

import io
from datetime import date, timedelta

import pytest
from pypdf import PdfReader
from sqlalchemy import select

from app.models import AuditLog, Holiday
from tests.conftest import login, post_form

pytestmark = pytest.mark.db

TWO_MONTHS = {"period_from": "2027-04-01", "period_to": "2027-05-31"}
FOOTER = "Made with love by Arrakis IT Services — info@arrakis.lu — www.arrakis.lu"


@pytest.fixture
async def team(client, db, make_clinic, make_user):
    clinic = await make_clinic("Cabinet du Parc")
    nr = await make_user("nr@ex.lu", "Nicolas Rousseau", clinic=clinic)
    dt = await make_user("dt@ex.lu", "Dominique Thill", clinic=clinic)
    await make_user("old@ex.lu", "Ancien Médecin", clinic=clinic, active=False)
    for offset in range(3):
        db.add(
            Holiday(user_id=dt.id, clinic_id=clinic.id, date=date(2027, 4, 5) + timedelta(offset))
        )
    await db.commit()
    await login(client, "nr@ex.lu")
    return clinic, nr, dt


def _pdf_text(pdf: bytes) -> list[str]:
    return [page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages]


async def test_generation_emails_pdf_to_every_active_user(client, db, mailer, team):
    response = await post_form(client, "/generer", TWO_MONTHS)
    assert response.status_code == 303  # background task already ran in-process

    assert sorted(m.to for m in mailer.sent) == ["dt@ex.lu", "nr@ex.lu"]  # inactive excluded
    message = mailer.sent[0]
    assert message.subject == (
        "Planification des gardes — Cabinet du Parc — 01/04/2027 au 31/05/2027"
    )
    assert "Nicolas Rousseau vient de générer la planification des gardes" in message.text
    assert "du 01/04/2027 au 31/05/2027" in message.text
    assert "http://testserver" in message.text and "Arrakis IT Services" in message.text
    assert message.html and "vient de générer" in message.html
    (attachment,) = message.attachments
    assert attachment.filename == "planification-gardes-cabinet-du-parc-20270401-20270531.pdf"
    assert attachment.mime_type == "application/pdf"
    assert attachment.content.startswith(b"%PDF")

    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "pdf_sent"))
    assert entry.meta["sent"] == 2 and entry.meta["failed"] == 0
    assert {r["email"] for r in entry.meta["recipients"]} == {"nr@ex.lu", "dt@ex.lu"}


async def test_pdf_layout(client, db, mailer, team):
    await post_form(client, "/generer", TWO_MONTHS)
    pages = _pdf_text(mailer.sent[0].attachments[0].content)
    assert len(pages) == 2  # one page per month, stats fit on the last one
    for text in pages:
        assert "Cabinet du Parc" in text
        assert "Période du 01/04/2027 au 31/05/2027" in text
        assert "Généré par Nicolas Rousseau" in text
        assert FOOTER in text
    assert "Avril 2027" in pages[0] or "avril 2027" in pages[0]
    assert "Statistiques" in pages[1] and "Statistiques" not in pages[0]
    assert "Lundi de Pentecôte" in pages[1]
    assert pages[1].count("/05/2027") >= 31


async def test_mail_failure_is_audited_per_recipient(client, db, mailer, team):
    mailer.fail_for = {"dt@ex.lu"}
    response = await post_form(client, "/generer", TWO_MONTHS)
    assert response.status_code == 303
    assert [m.to for m in mailer.sent] == ["nr@ex.lu"]
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "pdf_sent"))
    assert entry.meta["sent"] == 1 and entry.meta["failed"] == 1
    failed = [r for r in entry.meta["recipients"] if not r["ok"]]
    assert failed == [
        {"email": "dt@ex.lu", "ok": False, "error": "ConnectionError", "message": "SMTP down"}
    ]


async def test_pdf_download(client, db, mailer, team):
    assert (await client.get("/resultats/pdf")).status_code == 404
    await post_form(client, "/generer", TWO_MONTHS)
    response = await client.get("/resultats/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert (
        "planification-gardes-cabinet-du-parc-20270401-20270531.pdf"
        in (response.headers["content-disposition"])
    )
    assert response.headers["cache-control"] == "no-store"
    assert len(_pdf_text(response.content)) == 2


async def test_pdf_never_contains_other_clinic(client, db, mailer, team, make_clinic, make_user):
    other = await make_clinic("Autre Cabinet")
    await make_user("bob@ex.lu", "Bob Weber", clinic=other)
    await post_form(client, "/generer", TWO_MONTHS)
    text = "\n".join(_pdf_text((await client.get("/resultats/pdf")).content))
    assert "Bob Weber" not in text and "Autre Cabinet" not in text
    assert all(m.to != "bob@ex.lu" for m in mailer.sent)


async def test_pdf_failure_sends_nothing_and_is_audited(client, db, mailer, team, monkeypatch):
    from app.services import notifications

    async def broken(*args, **kwargs):
        raise RuntimeError("weasyprint exploded")

    monkeypatch.setattr(notifications, "build_schedule_pdf", broken)
    response = await post_form(client, "/generer", TWO_MONTHS)
    assert response.status_code == 303  # generation itself succeeded
    assert mailer.sent == []
    entry = await db.scalar(select(AuditLog).where(AuditLog.action == "pdf_sent"))
    assert entry.meta["error"] == "pdf: RuntimeError: weasyprint exploded"
    assert entry.meta["sent"] == 0


def test_edited_email_wording():
    from app.models import Clinic, Generation, User
    from app.services.notifications import build_schedule_email

    clinic = Clinic(name="Cabinet du Parc", slug="cabinet-du-parc")
    generation = Generation(period_from=date(2027, 1, 1), period_to=date(2027, 6, 30))
    user = User(email="dt@ex.lu", full_name="Dominique Thill")
    message = build_schedule_email(user, clinic, generation, "Nicolas Rousseau", "edited", b"%PDF")
    assert "Nicolas Rousseau vient de modifier la planification" in message.text
    assert message.to == "dt@ex.lu"


async def test_any_clinic_user_can_download_pdf(client, db, mailer, team, app):
    """Not only the user who generated: every user of the clinic."""
    from httpx import ASGITransport, AsyncClient

    from tests.conftest import TEST_CSRF_COOKIE

    await post_form(client, "/generer", TWO_MONTHS)  # generated by NR
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as dt:
        dt.cookies.set("csrftoken", TEST_CSRF_COOKIE)
        await login(dt, "dt@ex.lu")
        response = await dt.get("/resultats/pdf")
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
    assert "Généré par Nicolas Rousseau" in _pdf_text(response.content)[0]


async def test_email_links_use_app_base_url(client, db, mailer, team, monkeypatch, app):
    """Links come from APP_BASE_URL, never from the request host or a hardcoded value."""
    from app import config
    from app.services import notifications

    public = config.get_settings().model_copy(update={"app_base_url": "https://gardes.example.lu/"})
    monkeypatch.setattr(notifications, "get_settings", lambda: public)
    app.dependency_overrides[config.get_settings] = lambda: public

    await post_form(client, "/generer", TWO_MONTHS)
    schedule = mailer.sent[-1]
    assert "https://gardes.example.lu." in schedule.text  # end of sentence, no double slash
    assert 'href="https://gardes.example.lu"' in schedule.html
    assert "testserver" not in schedule.text + schedule.html

    await post_form(client, "/reset", {"email": "dt@ex.lu"})
    reset = mailer.sent[-1]
    assert "https://gardes.example.lu/reset/" in reset.text
    assert "testserver" not in reset.text + reset.html
