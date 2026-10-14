# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Month locks: the clinic's group admin (or a superadmin) freezes whole months.

While a month is locked, nobody — the group admin included, until they unlock it — can
change its assignments, vacations or on-site days (`MonthLockedError` → 403). A
regeneration keeps the current assignments of locked days and the changelog email skips
them. Only the current and future months can be locked; any locked month can be unlocked.
Each lock / unlock is audited (`month_locked` / `month_unlocked`).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MonthLock
from app.services import audit
from app.services.calendar import add_months, month_key, month_label

if TYPE_CHECKING:
    from app.dependencies import AuthContext

LOCKABLE_MONTHS_AHEAD = 12  # rows offered in the settings list (current month included)


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


@dataclass(frozen=True)
class LockRow:
    """One month of the settings list."""

    month: date
    locked: bool
    lockable: bool  # current or future month

    @property
    def key(self) -> str:
        """Form value, e.g. "2027-04"."""
        return month_key(self.month)


async def lock_rows(db: AsyncSession, clinic_id: uuid.UUID, today: date) -> list[LockRow]:
    """Current month + the next ones, plus any (older) locked month, by date."""
    current = today.replace(day=1)
    locked = await locked_months(db, clinic_id)
    months = {add_months(current, i) for i in range(LOCKABLE_MONTHS_AHEAD)} | locked
    return [LockRow(m, m in locked, m >= current) for m in sorted(months)]


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
