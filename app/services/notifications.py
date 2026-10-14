# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Email the schedule PDF to every active user of the clinic (brief §7.2).

Runs as a background task after the response: it opens its own database session,
renders the PDF once, sends one email per recipient (addresses are not disclosed to
each other) and writes a `pdf_sent` audit entry with the outcome per recipient.

After a regeneration, the email also lists the day-by-day changes against the archived
generation on the days both cover (see app/services/changelog.py).
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models import Clinic, Generation, User
from app.repositories import users as users_repo
from app.services import audit
from app.services.changelog import Changelog, load_changelog
from app.services.mail import Attachment, MailSender, OutgoingEmail
from app.services.pdf import build_schedule_pdf, pdf_filename
from app.templating import render_email

logger = logging.getLogger(__name__)

Action = Literal["generated", "edited"]


def build_schedule_email(
    recipient: User,
    clinic: Clinic,
    generation: Generation,
    actor_name: str,
    action: Action,
    pdf: bytes,
    changelog: Changelog | None = None,
) -> OutgoingEmail:
    """The French notification email with the PDF attached (and the changelog, if any)."""
    period_from = generation.period_from.strftime("%d/%m/%Y")
    period_to = generation.period_to.strftime("%d/%m/%Y")
    context = {
        "actor_name": actor_name,
        "verb": "modifier" if action == "edited" else "générer",
        "period_from": period_from,
        "period_to": period_to,
        "app_url": get_settings().app_base_url.rstrip("/"),
        "changelog": changelog,
    }
    return OutgoingEmail(
        to=recipient.email,
        subject=(f"Planification des gardes — {clinic.name} — {period_from} au {period_to}"),
        text=render_email("schedule.txt", context),
        html=render_email("schedule.html", context),
        attachments=(Attachment(pdf_filename(clinic, generation), pdf),),
    )


async def send_schedule_notification(
    sessionmaker: async_sessionmaker[AsyncSession],
    mailer: MailSender,
    clinic_id: uuid.UUID,
    generation_id: uuid.UUID,
    actor_id: uuid.UUID,
    action: Action,
    *,
    previous_generation_id: uuid.UUID | None = None,
) -> None:
    """Render the PDF and email it to all active users of the clinic, auditing the result.

    `previous_generation_id`: the generation archived by this one (regeneration changelog).
    """
    async with sessionmaker() as db:
        clinic = await db.get(Clinic, clinic_id)
        generation = await db.get(Generation, generation_id)
        actor = await users_repo.get_user_in_clinic(db, clinic_id, actor_id)
        if clinic is None or generation is None or generation.clinic_id != clinic_id:
            logger.error("Schedule notification skipped: generation %s not found", generation_id)
            return
        actor_name = actor.full_name if actor else "Un utilisateur"
        recipients = await users_repo.list_clinic_users(db, clinic_id)
        changelog: Changelog | None = None
        if previous_generation_id is not None:
            try:
                changelog = await load_changelog(db, clinic_id, previous_generation_id, generation)
            except Exception:
                # The schedule itself matters more than the changelog: send without it.
                logger.exception("Changelog failed (generation=%s)", generation_id)

        results: list[dict[str, object]] = []
        error: str | None = None
        try:
            pdf = await build_schedule_pdf(
                db,
                clinic,
                generation,
                actor_name,
                edited=action == "edited",
                when=datetime.now(UTC),
            )
        except Exception as exc:
            logger.exception("Schedule PDF rendering failed (generation=%s)", generation_id)
            pdf, error = b"", f"pdf: {type(exc).__name__}: {str(exc)[:300]}"

        if pdf:
            for recipient in recipients:
                message = build_schedule_email(
                    recipient, clinic, generation, actor_name, action, pdf, changelog
                )
                try:
                    await mailer.send(message)
                    results.append({"email": recipient.email, "ok": True})
                except Exception as exc:
                    logger.exception("Schedule email failed (user_id=%s)", recipient.id)
                    results.append(
                        {
                            "email": recipient.email,
                            "ok": False,
                            "error": type(exc).__name__,
                            # SMTP reply / reason, shown to superadmins in /admin/stats.
                            "message": str(exc)[:300],
                        }
                    )

        audit.record(
            db,
            "pdf_sent",
            user_id=actor_id,
            clinic_id=clinic_id,
            target_type="generation",
            target_id=generation_id,
            meta={
                "action": action,
                "recipients": results,
                "sent": sum(1 for r in results if r["ok"]),
                "failed": sum(1 for r in results if not r["ok"]),
                "pdf_bytes": len(pdf),
                "error": error,
            },
        )
        await db.commit()
        logger.info(
            "Schedule notification done (generation=%s, sent=%s/%s)",
            generation_id,
            sum(1 for r in results if r["ok"]),
            len(recipients),
        )
