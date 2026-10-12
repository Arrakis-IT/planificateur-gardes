# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic-scoped access to the shared draft (`assignment_draft`)."""

import uuid
from datetime import date

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AssignmentDraft


async def drafts_by_date(
    db: AsyncSession, clinic_id: uuid.UUID, generation_id: uuid.UUID
) -> dict[date, AssignmentDraft]:
    """Draft rows of the clinic for one generation."""
    stmt = select(AssignmentDraft).where(
        AssignmentDraft.clinic_id == clinic_id, AssignmentDraft.generation_id == generation_id
    )
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


async def clear_drafts(db: AsyncSession, clinic_id: uuid.UUID) -> int:
    """Delete the clinic's whole draft. Returns the number of cells discarded."""
    result = await db.execute(
        delete(AssignmentDraft)
        .where(AssignmentDraft.clinic_id == clinic_id)
        .returning(AssignmentDraft.id)
    )
    return len(result.all())
