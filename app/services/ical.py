# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Personal iCal feed (RFC 5545) of a doctor's duties, for calendar subscriptions.

`GET /ical/{token}` serves one all-day VEVENT per day assigned to the token's owner, from
the clinic's active generations plus, for the last HISTORY_DAYS days, the archived history
of the days no active generation holds. The token (secrets.token_urlsafe(32)) is created from the
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
from app.services import calendar as cal
from app.services.auth import is_user_allowed
from app.services.planning import build_days

PRODID = "-//Arrakis IT Services//Planificateur des gardes//FR"
UID_DOMAIN = "gardes.arrakis.lu"
TIMEZONE = "Europe/Luxembourg"
ICAL_PATH = "/ical"
SUMMARY = "Garde"
ONSITE_SUMMARY = "Garde sur place"
HISTORY_DAYS = 365


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
            f"SUMMARY:{ONSITE_SUMMARY if duty.onsite else SUMMARY}",
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
    """The user's feed: their saved duties (active generations, recent history)."""
    if user.clinic_id is None or user.clinic is None:  # excluded by user_by_token
        raise ValueError("iCal feeds are for clinic users only")
    generations = await repo.active_generations(db, user.clinic_id)
    today = cal.today_local()
    start = min([today - timedelta(days=HISTORY_DAYS), *(g.period_from for g in generations)])
    end = max([today, *(g.period_to for g in generations)])
    # Same day views as the pages and the PDF: on-site = marked in the clinic settings
    # and not removed in the results (saved values only, no draft).
    days = await build_days(db, user.clinic_id, start, end)
    duties = [
        Duty(day, view.onsite)
        for day, view in days.items()
        if view.doctor is not None and view.doctor.id == user.id
    ]
    stamps = [g.generated_at for g in generations]
    assignments = await repo.active_assignments(db, user.clinic_id, start, end)
    stamps += [a.edited_at for a in assignments.values() if a.edited_at is not None]
    return build_calendar(user.clinic.name, user.id, duties, max(stamps, default=datetime.now(UTC)))


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
