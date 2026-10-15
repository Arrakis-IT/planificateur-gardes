# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Personal iCal feed (RFC 5545) of a doctor's duties, for calendar subscriptions.

`GET /ical/{token}` serves one all-day VEVENT per day of the clinic's active generation
assigned to the token's owner. The token (secrets.token_urlsafe(32)) is created from the
settings page and can be regenerated, which invalidates the previous URL.

UIDs are `{YYYY-MM-DD}-{user_id}@gardes.arrakis.lu`: stable across reads and regenerations,
so subscribed calendars update events instead of duplicating them.
"""

import secrets
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.repositories import scheduling as repo
from app.services import audit
from app.services.auth import is_user_allowed

PRODID = "-//Arrakis IT Services//Planificateur des gardes//FR"
UID_DOMAIN = "gardes.arrakis.lu"
TIMEZONE = "Europe/Luxembourg"
ICAL_PATH = "/ical"


@dataclass(frozen=True)
class Duty:
    """One duty day of the doctor."""

    day: date
    onsite: bool


def _escape(text: str) -> str:
    """TEXT value escaping (RFC 5545 §3.3.11)."""
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Fold content lines longer than 75 octets (RFC 5545 §3.1), on UTF-8 boundaries."""
    out: list[str] = []
    current, size = "", 0
    for char in line:
        width = len(char.encode())
        if size + width > 75:
            out.append(current)
            current, size = " ", 1  # continuation lines start with a space
        current += char
        size += width
    out.append(current)
    return "\r\n".join(out)


def build_calendar(
    clinic_name: str, user_id: uuid.UUID, duties: Iterable[Duty], stamp: datetime
) -> str:
    """The VCALENDAR document (CRLF line endings). Without duties: header and footer only."""
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape(f'Gardes {clinic_name}')}",
        f"X-WR-TIMEZONE:{TIMEZONE}",
    ]
    dtstamp = stamp.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    for duty in sorted(duties, key=lambda d: d.day):
        lines += [
            "BEGIN:VEVENT",
            f"UID:{duty.day.isoformat()}-{user_id}@{UID_DOMAIN}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{duty.day:%Y%m%d}",
            f"DTEND;VALUE=DATE:{duty.day + timedelta(days=1):%Y%m%d}",  # exclusive end
            f"SUMMARY:{'Garde (sur place)' if duty.onsite else 'Garde'}",
            f"DESCRIPTION:{_escape(f'Garde planifiée dans le groupe {clinic_name}.')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "".join(_fold(line) + "\r\n" for line in lines)


async def user_by_token(db: AsyncSession, token: str) -> User | None:
    """Active clinic user owning this feed token (None: unknown, inactive or superadmin)."""
    user = (await db.scalars(select(User).where(User.ical_token == token))).one_or_none()
    if user is None or user.clinic_id is None or not is_user_allowed(user):
        return None
    return user


async def calendar_for(db: AsyncSession, user: User) -> str:
    """The user's feed: their saved duties in the clinic's active generation."""
    if user.clinic_id is None or user.clinic is None:  # excluded by user_by_token
        raise ValueError("iCal feeds are for clinic users only")
    generation = await repo.get_active_generation(db, user.clinic_id)
    duties: list[Duty] = []
    stamp = datetime.now(UTC)
    if generation is not None:
        stamp = generation.generated_at
        assignments = await repo.active_assignments(
            db, user.clinic_id, generation.period_from, generation.period_to
        )
        onsite = await repo.list_onsite_days(
            db, user.clinic_id, generation.period_from, generation.period_to
        )
        duties = [
            Duty(day, day in onsite and not a.onsite_removed)
            for day, a in assignments.items()
            if a.user_id == user.id
        ]
        edits = [a.edited_at for a in assignments.values() if a.edited_at is not None]
        stamp = max([stamp, *edits])
    return build_calendar(user.clinic.name, user.id, duties, stamp)


async def issue_token(db: AsyncSession, user: User, request: Request | None = None) -> str:
    """Create (or replace) the feed token; audited. Caller commits."""
    regenerated = user.ical_token is not None
    user.ical_token = secrets.token_urlsafe(32)
    audit.record(
        db,
        "ical_token_regenerated" if regenerated else "ical_token_generated",
        request=request,
        user_id=user.id,
        clinic_id=user.clinic_id,
        target_type="user",
        target_id=user.id,
    )
    return user.ical_token
