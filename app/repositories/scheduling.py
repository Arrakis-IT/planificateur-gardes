# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic-scoped data access for vacations, on-site days, custom holidays and generations.

Every function takes the `clinic_id` of the request scope and filters on it.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assignment, Clinic, CustomHoliday, Generation, Holiday, OnsiteDay, User

# --- Vacations (table `holiday`) -------------------------------------------------------


async def list_user_vacations(
    db: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, start: date, end: date
) -> set[date]:
    """Vacation days of one user of the clinic within [start, end]."""
    stmt = select(Holiday.date).where(
        Holiday.clinic_id == clinic_id,
        Holiday.user_id == user_id,
        Holiday.date.between(start, end),
    )
    return set((await db.scalars(stmt)).all())


async def toggle_user_vacation(
    db: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, day: date
) -> bool:
    """Add or remove a vacation day. Returns True if the day is now a vacation day."""
    deleted = await db.execute(
        delete(Holiday)
        .where(Holiday.clinic_id == clinic_id, Holiday.user_id == user_id, Holiday.date == day)
        .returning(Holiday.id)
    )
    if deleted.first() is not None:
        return False
    db.add(Holiday(clinic_id=clinic_id, user_id=user_id, date=day))
    await db.flush()
    return True


async def add_user_vacation_range(
    db: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, start: date, end: date
) -> int:
    """Mark every day of [start, end] as vacation (existing days kept). Returns days added."""
    existing = await list_user_vacations(db, clinic_id, user_id, start, end)
    added = 0
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day not in existing:
            db.add(Holiday(clinic_id=clinic_id, user_id=user_id, date=day))
            added += 1
    await db.flush()
    return added


async def remove_user_vacation_range(
    db: AsyncSession, clinic_id: uuid.UUID, user_id: uuid.UUID, start: date, end: date
) -> int:
    """Remove every vacation day of [start, end]. Returns days removed."""
    result = await db.execute(
        delete(Holiday)
        .where(
            Holiday.clinic_id == clinic_id,
            Holiday.user_id == user_id,
            Holiday.date.between(start, end),
        )
        .returning(Holiday.id)
    )
    return len(result.all())


async def vacations_by_date(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, set[uuid.UUID]]:
    """date → ids of the clinic's users on vacation that day."""
    stmt = select(Holiday.date, Holiday.user_id).where(
        Holiday.clinic_id == clinic_id, Holiday.date.between(start, end)
    )
    result: dict[date, set[uuid.UUID]] = defaultdict(set)
    for day, user_id in (await db.execute(stmt)).all():
        result[day].add(user_id)
    return dict(result)


async def vacation_counts(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[uuid.UUID, int]:
    """user id → number of vacation days within [start, end]."""
    stmt = (
        select(Holiday.user_id, func.count(Holiday.id))
        .where(Holiday.clinic_id == clinic_id, Holiday.date.between(start, end))
        .group_by(Holiday.user_id)
    )
    return {user_id: int(count) for user_id, count in (await db.execute(stmt)).all()}


# --- On-site days ------------------------------------------------------------------------


async def list_onsite_days(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> set[date]:
    """On-site days of the clinic within [start, end]."""
    stmt = select(OnsiteDay.date).where(
        OnsiteDay.clinic_id == clinic_id, OnsiteDay.date.between(start, end)
    )
    return set((await db.scalars(stmt)).all())


async def toggle_onsite_day(
    db: AsyncSession, clinic_id: uuid.UUID, day: date, user_id: uuid.UUID
) -> bool:
    """Add or remove an on-site day. Returns True if the day is now on-site."""
    deleted = await db.execute(
        delete(OnsiteDay)
        .where(OnsiteDay.clinic_id == clinic_id, OnsiteDay.date == day)
        .returning(OnsiteDay.id)
    )
    if deleted.first() is not None:
        return False
    db.add(OnsiteDay(clinic_id=clinic_id, date=day, created_by=user_id))
    await db.flush()
    return True


# --- Custom holidays ---------------------------------------------------------------------


async def custom_holidays(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    """The clinic's custom holidays within [start, end]."""
    stmt = select(CustomHoliday.date, CustomHoliday.label).where(
        CustomHoliday.clinic_id == clinic_id, CustomHoliday.date.between(start, end)
    )
    return dict((await db.execute(stmt)).all())


# --- Generations / assignments ----------------------------------------------------------------


async def lock_clinic(db: AsyncSession, clinic_id: uuid.UUID) -> None:
    """Serialise concurrent generations of the same clinic (row lock until commit)."""
    await db.execute(select(Clinic.id).where(Clinic.id == clinic_id).with_for_update())


async def get_active_generation(db: AsyncSession, clinic_id: uuid.UUID) -> Generation | None:
    """The clinic's active generation, if any."""
    stmt = select(Generation).where(
        Generation.clinic_id == clinic_id, Generation.is_active.is_(True)
    )
    return (await db.scalars(stmt)).one_or_none()


async def archive_active_generation(db: AsyncSession, clinic_id: uuid.UUID) -> uuid.UUID | None:
    """Soft-archive the active generation. Returns its id, if there was one."""
    result = await db.execute(
        update(Generation)
        .where(Generation.clinic_id == clinic_id, Generation.is_active.is_(True))
        .values(is_active=False, archived_at=datetime.now(UTC))
        .returning(Generation.id)
    )
    return result.scalar_one_or_none()


async def create_generation(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    period_from: date,
    period_to: date,
    generated_by: uuid.UUID,
    assignments: dict[date, uuid.UUID | None],
) -> Generation:
    """Insert an active generation and its assignments (the caller archived the previous one)."""
    generation = Generation(
        clinic_id=clinic_id,
        period_from=period_from,
        period_to=period_to,
        generated_by=generated_by,
    )
    db.add(generation)
    await db.flush()
    db.add_all(
        Assignment(generation_id=generation.id, date=day, user_id=user_id)
        for day, user_id in assignments.items()
    )
    await db.flush()
    return generation


async def active_assignments(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, Assignment]:
    """Assignments of the clinic's active generation within [start, end]."""
    stmt = (
        select(Assignment)
        .join(Generation, Generation.id == Assignment.generation_id)
        .where(
            Generation.clinic_id == clinic_id,
            Generation.is_active.is_(True),
            Assignment.date.between(start, end),
        )
    )
    return {a.date: a for a in (await db.scalars(stmt)).all()}


async def users_by_id(db: AsyncSession, clinic_id: uuid.UUID) -> dict[uuid.UUID, User]:
    """Every user of the clinic (active or not), to resolve assignment owners."""
    stmt = select(User).where(User.clinic_id == clinic_id)
    return {u.id: u for u in (await db.scalars(stmt)).all()}
