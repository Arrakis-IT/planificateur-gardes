# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Manual edits of the active generation through the clinic's shared draft (brief §6.3).

A cell edit never touches `assignment` directly: it upserts the desired final state of the
cell into `assignment_draft`. An edit that brings a cell back to its saved state removes
the draft row. "Enregistrer" applies every draft row to `assignment` in one transaction and
writes one audit entry per changed cell; "Annuler" deletes the draft.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assignment, AssignmentDraft, Generation, User
from app.repositories import drafts as drafts_repo
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.services import audit
from app.services.scheduler import UNASSIGNED


class CellNotFoundError(LookupError):
    """No active generation, or the date is outside its period."""


class InvalidDoctorError(ValueError):
    """The chosen doctor is not an active user of the clinic."""


class NotOnsiteError(ValueError):
    """On-site can only be removed/restored on days marked on-site in the settings."""


@dataclass(frozen=True)
class Cell:
    """Saved assignment of one day plus its draft row, if any."""

    generation: Generation
    assignment: Assignment
    draft: AssignmentDraft | None

    @property
    def user_id(self) -> uuid.UUID | None:
        """Effective doctor (draft wins)."""
        return self.draft.user_id if self.draft else self.assignment.user_id

    @property
    def onsite_removed(self) -> bool:
        """Effective "on-site removed in results" flag (draft wins)."""
        return self.draft.onsite_removed if self.draft else self.assignment.onsite_removed


async def get_cell(db: AsyncSession, clinic_id: uuid.UUID, day: date) -> Cell:
    """Load one cell of the clinic's active generation (raises CellNotFoundError)."""
    generation = await repo.get_active_generation(db, clinic_id)
    if generation is None:
        raise CellNotFoundError
    assignment = await db.scalar(
        select(Assignment).where(Assignment.generation_id == generation.id, Assignment.date == day)
    )
    if assignment is None:
        raise CellNotFoundError
    draft = await drafts_repo.get_draft(db, clinic_id, generation.id, day)
    return Cell(generation, assignment, draft)


async def _store(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    cell: Cell,
    user_id: uuid.UUID | None,
    onsite_removed: bool,
    actor: User,
) -> None:
    unchanged = (
        user_id == cell.assignment.user_id and onsite_removed == cell.assignment.onsite_removed
    )
    if unchanged:
        if cell.draft is not None:
            await db.delete(cell.draft)
        await db.flush()
        return
    draft = cell.draft or AssignmentDraft(
        clinic_id=clinic_id, generation_id=cell.generation.id, date=cell.assignment.date
    )
    draft.user_id = user_id
    draft.onsite_removed = onsite_removed
    draft.edited_by = actor.id
    draft.edited_at = datetime.now(UTC)
    db.add(draft)
    await db.flush()


async def set_cell_doctor(
    db: AsyncSession, clinic_id: uuid.UUID, day: date, code: str, actor: User
) -> Cell:
    """Put a doctor (by code) or nobody ("XX") on a day, in the draft."""
    cell = await get_cell(db, clinic_id, day)
    user_id: uuid.UUID | None = None
    if code != UNASSIGNED:
        doctors = {u.doctor_code: u for u in await users_repo.list_clinic_users(db, clinic_id)}
        if code not in doctors:
            raise InvalidDoctorError
        user_id = doctors[code].id
    await _store(db, clinic_id, cell, user_id, cell.onsite_removed, actor)
    return cell


async def toggle_cell_onsite(
    db: AsyncSession, clinic_id: uuid.UUID, day: date, actor: User
) -> Cell:
    """Remove / restore the on-site mark of a day in the results only (draft)."""
    cell = await get_cell(db, clinic_id, day)
    if day not in await repo.list_onsite_days(db, clinic_id, day, day):
        raise NotOnsiteError
    await _store(db, clinic_id, cell, cell.user_id, not cell.onsite_removed, actor)
    return cell


@dataclass(frozen=True)
class SaveResult:
    """Outcome of "Enregistrer les modifications"."""

    generation: Generation | None
    changed_cells: int


async def save_draft(
    db: AsyncSession, clinic_id: uuid.UUID, actor: User, request: Request | None = None
) -> SaveResult:
    """Apply the draft to the active generation, one audit entry per changed cell.

    Caller commits (then broadcasts `draft_cleared` and emails the PDF).
    """
    await repo.lock_clinic(db, clinic_id)
    generation = await repo.get_active_generation(db, clinic_id)
    if generation is None:
        await drafts_repo.clear_drafts(db, clinic_id)
        return SaveResult(None, 0)
    drafts = await drafts_repo.drafts_by_date(db, clinic_id, generation.id)
    assignments = {
        a.date: a
        for a in (
            await db.scalars(
                select(Assignment).where(
                    Assignment.generation_id == generation.id,
                    Assignment.date.in_(list(drafts)),
                )
            )
        ).all()
    }
    codes = {u.id: u.doctor_code for u in (await repo.users_by_id(db, clinic_id)).values()}
    now = datetime.now(UTC)
    changed = 0
    for day, draft in sorted(drafts.items()):
        assignment = assignments.get(day)
        if assignment is None:
            continue
        doctor_changed = draft.user_id != assignment.user_id
        onsite_changed = draft.onsite_removed != assignment.onsite_removed
        if not (doctor_changed or onsite_changed):
            continue
        meta: dict[str, object] = {"date": day.isoformat()}
        if doctor_changed:
            meta |= {
                "old": codes.get(assignment.user_id, UNASSIGNED)
                if assignment.user_id
                else UNASSIGNED,
                "new": codes.get(draft.user_id, UNASSIGNED) if draft.user_id else UNASSIGNED,
            }
        if onsite_changed:
            meta["onsite_removed"] = draft.onsite_removed
        audit.record(
            db,
            "assignment_edited" if doctor_changed else "onsite_toggled_result",
            request=request,
            user_id=actor.id,
            clinic_id=clinic_id,
            target_type="assignment",
            target_id=assignment.id,
            meta=meta,
        )
        assignment.user_id = draft.user_id
        assignment.onsite_removed = draft.onsite_removed
        assignment.edited_by = draft.edited_by or actor.id
        assignment.edited_at = now
        changed += 1
    await drafts_repo.clear_drafts(db, clinic_id)
    await db.flush()
    return SaveResult(generation, changed)


async def discard_draft(
    db: AsyncSession, clinic_id: uuid.UUID, actor: User, request: Request | None = None
) -> int:
    """Delete the clinic's draft ("Annuler"). Caller commits and broadcasts."""
    discarded = await drafts_repo.clear_drafts(db, clinic_id)
    if discarded:
        audit.record(
            db,
            "draft_discarded",
            request=request,
            user_id=actor.id,
            clinic_id=clinic_id,
            meta={"cells": discarded},
        )
    return discarded
