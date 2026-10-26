# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic-scoped access to the shared draft (`assignment_draft`)."""

import uuid
from datetime import date

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssignmentDraft


async def clinic_drafts(db: AsyncSession, clinic_id: uuid.UUID) -> dict[date, AssignmentDraft]:
    """The clinic's whole draft (cells of any active generation), by date."""
    stmt = select(AssignmentDraft).where(AssignmentDraft.clinic_id == clinic_id)
    return {d.date: d for d in (await db.scalars(stmt)).all()}


async def get_draft(
    db: AsyncSession, clinic_id: uuid.UUID, generation_id: uuid.UUID, day: date
) -> AssignmentDraft | None:
    """The draft row of one cell, if any."""
    stmt = select(AssignmentDraft).where(
        AssignmentDraft.clinic_id == clinic_id,
        AssignmentDraft.generation_id == generation_id,
        AssignmentDraft.date == day,
    )
    return (await db.scalars(stmt)).one_or_none()


async def count_drafts(db: AsyncSession, clinic_id: uuid.UUID) -> int:
    """Number of edited cells in the clinic's draft."""
    stmt = select(func.count(AssignmentDraft.id)).where(AssignmentDraft.clinic_id == clinic_id)
    return int(await db.scalar(stmt) or 0)


async def clear_drafts(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    generation_id: uuid.UUID | None = None,
    days: list[date] | None = None,
) -> int:
    """Delete the clinic's draft (or the cells of one generation / of some days). Returns the
    number of cells discarded."""
    stmt = delete(AssignmentDraft).where(AssignmentDraft.clinic_id == clinic_id)
    if generation_id is not None:
        stmt = stmt.where(AssignmentDraft.generation_id == generation_id)
    if days is not None:
        stmt = stmt.where(AssignmentDraft.date.in_(days))
    result = await db.execute(stmt.returning(AssignmentDraft.id))
    return len(result.all())


async def move_drafts(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    days: list[date],
) -> None:
    """Re-attach draft cells to the generation their assignment moved to."""
    if days:
        await db.execute(
            update(AssignmentDraft)
            .where(
                AssignmentDraft.clinic_id == clinic_id,
                AssignmentDraft.generation_id == source_id,
                AssignmentDraft.date.in_(days),
            )
            .values(generation_id=target_id)
            .execution_options(synchronize_session=False)
        )
