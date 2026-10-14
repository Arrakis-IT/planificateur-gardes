# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Month locks: the clinic's group admin (or a superadmin) freezes whole months.

While a month is locked, nobody — the group admin included, until they unlock it — can
change its assignments, vacations or on-site days (`MonthLockedError` → 403). A
regeneration keeps the current assignments of locked days and the changelog email skips
them. Only current and future months covered by the active generation can be locked; any
locked month can be unlocked, and a regeneration releases the locks of months left outside
its period. Each lock / unlock is audited (`month_locked` / `month_unlocked`).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonthLock
from app.repositories import scheduling as scheduling_repo
from app.services import audit
from app.services.calendar import Period, add_months, month_key, month_label, months_between

if TYPE_CHECKING:
    from app.dependencies import AuthContext

OUT_OF_RANGE = "Mois en dehors de la planification active, impossible à verrouiller."
REGENERATION_OUT_OF_RANGE = "regeneration_out_of_range"


class MonthLockedError(Exception):
    """A change touches a locked month (rendered as a 403 page)."""

    def __init__(self, month: date) -> None:
        self.month = month
        super().__init__(f"Mois {month_label(month)} verrouillé.")


class LockError(ValueError):
    """The lock request is invalid (French message for the user)."""


def can_manage_locks(auth: "AuthContext") -> bool:
    """Superadmins (any clinic) and the group admin of the clinic in context."""
    if auth.is_superadmin:
        return True
    clinic = auth.clinic
    return clinic is not None and auth.user.is_group_admin and auth.user.clinic_id == clinic.id


async def locked_months(
    db: AsyncSession, clinic_id: uuid.UUID, start: date | None = None, end: date | None = None
) -> set[date]:
    """First days of the clinic's locked months, optionally only those meeting [start, end]."""
    stmt = select(MonthLock.month).where(MonthLock.clinic_id == clinic_id)
    if start is not None:
        stmt = stmt.where(MonthLock.month >= start.replace(day=1))
    if end is not None:
        stmt = stmt.where(MonthLock.month <= end)
    return set((await db.scalars(stmt)).all())


async def ensure_unlocked(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date | None = None
) -> None:
    """Raise MonthLockedError if a day of [start, end] falls in a locked month."""
    locked = await locked_months(db, clinic_id, start, end or start)
    if locked:
        raise MonthLockedError(min(locked))


def is_locked(day: date, locked: set[date]) -> bool:
    """Does `day` fall in one of the `locked` months (first days)?"""
    return day.replace(day=1) in locked


def months_label(months: set[date]) -> str:
    """e.g. "avril 2027, mai 2027"."""
    return ", ".join(month_label(m) for m in sorted(months))


async def active_period(db: AsyncSession, clinic_id: uuid.UUID) -> Period | None:
    """Period of the clinic's active generation, if any."""
    generation = await scheduling_repo.get_active_generation(db, clinic_id)
    return Period(generation.period_from, generation.period_to) if generation else None


def covers(period: Period | None, month: date) -> bool:
    """Does `period` meet the month starting on `month` (partially or totally)?"""
    return period is not None and month <= period.end and add_months(month, 1) > period.start


@dataclass(frozen=True)
class LockRow:
    """One month of the settings list."""

    month: date
    locked: bool
    lockable: bool  # current or future month of the active generation

    @property
    def key(self) -> str:
        """Form value, e.g. "2027-04"."""
        return month_key(self.month)


async def lock_rows(db: AsyncSession, clinic_id: uuid.UUID, today: date) -> list[LockRow]:
    """Current and future months of the active generation, plus any locked month (so that
    it can always be unlocked), by date. Empty without an active generation and locks."""
    current = today.replace(day=1)
    locked = await locked_months(db, clinic_id)
    period = await active_period(db, clinic_id)
    covered = set(months_between(period.start, period.end)) if period else set()
    months = {m for m in covered if m >= current} | locked
    return [LockRow(m, m in locked, m >= current and m in covered) for m in sorted(months)]


async def lock_month(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    month: date,
    today: date,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> bool:
    """Lock a month (first day). Returns False if it already was. Caller commits."""
    month = month.replace(day=1)
    if month < today.replace(day=1):
        raise LockError("Seuls le mois en cours et les mois à venir peuvent être verrouillés.")
    if not covers(await active_period(db, clinic_id), month):
        raise LockError(OUT_OF_RANGE)
    if month in await locked_months(db, clinic_id, month, month):
        return False
    db.add(MonthLock(clinic_id=clinic_id, month=month, locked_by=actor_id))
    audit.record(
        db,
        "month_locked",
        request=request,
        user_id=actor_id,
        clinic_id=clinic_id,
        target_type="month",
        meta={"month": month_key(month)},
    )
    await db.flush()
    return True


async def apply_lock_form(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    month_value: str,
    action: str,
    today: date,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> str:
    """Lock or unlock "YYYY-MM"; returns the French confirmation. Caller commits."""
    try:
        year, month_number = (int(part) for part in month_value.split("-", 1))
        month = date(year, month_number, 1)
    except ValueError:
        raise LockError("Mois invalide.") from None
    label = month_label(month)
    if action == "lock":
        changed = await lock_month(db, clinic_id, month, today, actor_id=actor_id, request=request)
        return f"Mois {label} verrouillé." if changed else f"Le mois {label} était déjà verrouillé."
    changed = await unlock_month(db, clinic_id, month, actor_id=actor_id, request=request)
    return f"Mois {label} déverrouillé." if changed else f"Le mois {label} n'était pas verrouillé."


async def unlock_month(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    month: date,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> bool:
    """Unlock a month. Returns False if it was not locked. Caller commits."""
    month = month.replace(day=1)
    deleted = await db.execute(
        delete(MonthLock)
        .where(MonthLock.clinic_id == clinic_id, MonthLock.month == month)
        .returning(MonthLock.id)
    )
    if deleted.first() is None:
        return False
    audit.record(
        db,
        "month_unlocked",
        request=request,
        user_id=actor_id,
        clinic_id=clinic_id,
        target_type="month",
        meta={"month": month_key(month)},
    )
    return True


async def release_locks_outside(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    period: Period,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> set[date]:
    """Unlock the months a new generation leaves outside its period (audited with the reason
    "regeneration_out_of_range"). Returns those months. Caller commits."""
    released = {m for m in await locked_months(db, clinic_id) if not covers(period, m)}
    if not released:
        return released
    await db.execute(
        delete(MonthLock).where(MonthLock.clinic_id == clinic_id, MonthLock.month.in_(released))
    )
    for month in sorted(released):
        audit.record(
            db,
            "month_unlocked",
            request=request,
            user_id=actor_id,
            clinic_id=clinic_id,
            target_type="month",
            meta={"month": month_key(month), "reason": REGENERATION_OUT_OF_RANGE},
        )
    return released
