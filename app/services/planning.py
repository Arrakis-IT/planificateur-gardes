# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Glue between the database and the pure scheduler: view models and the generate flow."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import anyio
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DesiderataConflict, Generation, User
from app.repositories import drafts as drafts_repo
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.services import audit
from app.services.calendar import Period
from app.services.holidays import holidays_between
from app.services.locks import is_locked, locked_months, release_locks_outside
from app.services.scheduler import (
    UNASSIGNED,
    Doctor,
    ScheduleRequest,
    ScheduleStats,
    compute_stats,
    desiderata_conflicts,
    generate_schedule,
    is_weekend,
)

MAX_PERIOD_DAYS = 400


class GenerationError(ValueError):
    """The generation request is invalid (French message for the user)."""


@dataclass(frozen=True)
class DayView:
    """Everything a calendar cell / list row needs to know about one day."""

    day: date
    weekend: bool  # Saturday, Sunday or effective public holiday (grey background, §6.6)
    holiday: str | None  # public / custom holiday label
    onsite: bool  # effective on-site (settings minus "removed in results")
    planned: bool  # covered by the active generation
    doctor: User | None  # no doctor on a planned day means "unassigned" (XX)
    drafted: bool = False  # shows a not yet saved edit of the shared draft
    onsite_setting: bool = False  # marked on-site in the clinic settings
    locked: bool = False  # in a month locked by the group admin (read-only)
    # The doctor had a desiderata that day (stored flag, see Assignment.desiderata_conflict).
    desiderata_conflict: DesiderataConflict | None = None

    @property
    def unassigned(self) -> bool:
        """Covered by the generation but nobody available."""
        return self.planned and self.doctor is None


async def public_holidays(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    """Official LU + clinic custom holidays within [start, end]."""
    return holidays_between(start, end, await repo.custom_holidays(db, clinic_id, start, end))


@dataclass(frozen=True)
class _Cell:
    user_id: uuid.UUID | None
    onsite_removed: bool
    desiderata_conflict: DesiderataConflict | None


async def _effective_cells(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date, with_draft: bool
) -> tuple[dict[date, _Cell], set[date]]:
    """Cells of the active generation, draft applied if asked.

    Also returns the dates overridden by the draft.
    """
    assignments = await repo.active_assignments(db, clinic_id, start, end)
    cells = {
        d: _Cell(a.user_id, a.onsite_removed, a.desiderata_conflict) for d, a in assignments.items()
    }
    drafted: set[date] = set()
    if with_draft and assignments:
        generation_id = next(iter(assignments.values())).generation_id
        for d, draft in (await drafts_repo.drafts_by_date(db, clinic_id, generation_id)).items():
            if start <= d <= end:
                saved = cells[d]
                conflict = saved.desiderata_conflict if draft.user_id == saved.user_id else None
                cells[d] = _Cell(draft.user_id, draft.onsite_removed, conflict)
                drafted.add(d)
    return cells, drafted


async def build_days(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    start: date,
    end: date,
    *,
    with_draft: bool = False,
) -> dict[date, DayView]:
    """Day views for [start, end] from the clinic's active generation.

    `with_draft` overlays the shared draft (editable views); otherwise saved values only.
    """
    cells, drafted = await _effective_cells(db, clinic_id, start, end, with_draft)
    onsite = await repo.list_onsite_days(db, clinic_id, start, end)
    holidays = await public_holidays(db, clinic_id, start, end)
    users = await repo.users_by_id(db, clinic_id)
    locked = await locked_months(db, clinic_id, start, end)
    days: dict[date, DayView] = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        cell = cells.get(day)
        removed = cell is not None and cell.onsite_removed
        days[day] = DayView(
            day=day,
            weekend=is_weekend(day) or day in holidays,
            holiday=holidays.get(day),
            onsite=day in onsite and not removed,
            planned=cell is not None,
            doctor=users.get(cell.user_id) if cell is not None and cell.user_id else None,
            drafted=day in drafted,
            onsite_setting=day in onsite,
            locked=is_locked(day, locked),
            desiderata_conflict=cell.desiderata_conflict if cell is not None else None,
        )
    return days


# --- Generate -------------------------------------------------------------------------------


@dataclass(frozen=True)
class GenerationRun:
    """The new active generation and the one it archived (for the changelog email)."""

    generation: Generation
    previous_id: uuid.UUID | None
    locked_months: frozenset[date] = frozenset()  # kept as they were (first days)
    released_months: frozenset[date] = frozenset()  # locks dropped: outside the new period
    desiderata_conflicts: int = 0  # days given to a doctor despite a desiderata


def validate_period(period_from: date, period_to: date) -> None:
    """Raise GenerationError for an unusable period."""
    if period_from >= period_to:
        raise GenerationError("La date de début doit précéder la date de fin.")
    if (period_to - period_from).days + 1 > MAX_PERIOD_DAYS:
        raise GenerationError(f"La période ne peut pas dépasser {MAX_PERIOD_DAYS} jours.")


async def run_generation(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    actor: User,
    period_from: date,
    period_to: date,
    request: Request | None = None,
) -> GenerationRun:
    """Run the scheduler, archive the active generation and store the new one. Caller commits.

    Days of locked months keep the active generation's assignment (doctor and on-site
    removal); a locked day the active generation did not cover takes the scheduler's choice.
    Locks of months outside the new period are released. Days whose doctor had a desiderata
    are flagged (`assignment.desiderata_conflict`); kept locked days keep their flag.
    """
    validate_period(period_from, period_to)
    await repo.lock_clinic(db, clinic_id)

    doctors = await users_repo.list_clinic_users(db, clinic_id)
    if not doctors:
        raise GenerationError("Le groupe n'a aucun médecin actif.")
    code_of = {u.id: u.doctor_code for u in doctors}
    vacations = {
        day: {code_of[uid] for uid in user_ids if uid in code_of}
        for day, user_ids in (
            await repo.vacations_by_date(db, clinic_id, period_from, period_to)
        ).items()
    }
    desiderata = {
        day: {code_of[uid] for uid in user_ids if uid in code_of}
        for day, user_ids in (
            await repo.desiderata_by_date(db, clinic_id, period_from, period_to)
        ).items()
    }
    schedule_request = ScheduleRequest(
        period_from=period_from,
        period_to=period_to,
        doctors=[Doctor(u.doctor_code, u.full_name) for u in doctors],
        holidays=vacations,
        onsite=await repo.list_onsite_days(db, clinic_id, period_from, period_to),
        luxembourg_holidays=set(await public_holidays(db, clinic_id, period_from, period_to)),
        desiderata=desiderata,
    )
    # CPU-bound (≈0.1–0.5 s): keep the event loop responsive.
    result = await anyio.to_thread.run_sync(generate_schedule, schedule_request)

    user_of = {u.doctor_code: u.id for u in doctors}
    assignments = {
        day: (None if code == UNASSIGNED else user_of[code]) for day, code in result.items()
    }
    conflicts = dict.fromkeys(
        desiderata_conflicts(schedule_request, result), DesiderataConflict.GENERATED
    )
    locked = await locked_months(db, clinic_id, period_from, period_to)
    onsite_removed: set[date] = set()
    if locked:
        current = await repo.active_assignments(db, clinic_id, period_from, period_to)
        for day, kept in current.items():
            if is_locked(day, locked):
                assignments[day] = kept.user_id
                if kept.onsite_removed:
                    onsite_removed.add(day)
                conflicts.pop(day, None)
                if kept.desiderata_conflict is not None:
                    conflicts[day] = kept.desiderata_conflict
    await drafts_repo.clear_drafts(db, clinic_id)  # edits of the old generation are moot
    previous_id = await repo.archive_active_generation(db, clinic_id)
    generation = await repo.create_generation(
        db, clinic_id, period_from, period_to, actor.id, assignments, onsite_removed, conflicts
    )
    await repo.snapshot_onsite(db, clinic_id, generation.id)
    released = await release_locks_outside(
        db, clinic_id, Period(period_from, period_to), actor_id=actor.id, request=request
    )
    audit.record(
        db,
        "generation_created",
        request=request,
        user_id=actor.id,
        clinic_id=clinic_id,
        target_type="generation",
        target_id=generation.id,
        meta={
            "period_from": period_from.isoformat(),
            "period_to": period_to.isoformat(),
            "doctors": len(doctors),
            "unassigned": sum(1 for user_id in assignments.values() if user_id is None),
            "desiderata_conflicts": len(conflicts),
            "locked_months": sorted(m.strftime("%Y-%m") for m in locked),
            "archived_generation": str(previous_id) if previous_id else None,
        },
    )
    return GenerationRun(
        generation, previous_id, frozenset(locked), frozenset(released), len(conflicts)
    )


# --- Results statistics -----------------------------------------------------------------------


@dataclass(frozen=True)
class ResultsStats:
    """Statistics of the active generation with the users behind each code."""

    stats: ScheduleStats
    users: dict[str, User]


async def results_stats(
    db: AsyncSession, clinic_id: uuid.UUID, generation: Generation, *, with_draft: bool = False
) -> ResultsStats:
    """Port of the standalone app's statistics table for a stored generation.

    Vacations are loaded for the generation period only ('Congés' column scope, see
    `app.services.scheduler.compute_stats`).
    """
    start, end = generation.period_from, generation.period_to
    cells, _ = await _effective_cells(db, clinic_id, start, end, with_draft)
    all_users = await repo.users_by_id(db, clinic_id)
    involved = {cell.user_id for cell in cells.values() if cell.user_id}
    users = sorted(
        (u for u in all_users.values() if u.active or u.id in involved),
        key=lambda u: u.full_name.lower(),
    )
    code_of = {u.id: u.doctor_code for u in users}
    vacations = {
        day: {code_of[uid] for uid in uids if uid in code_of}
        for day, uids in (await repo.vacations_by_date(db, clinic_id, start, end)).items()
    }
    onsite = await repo.list_onsite_days(db, clinic_id, start, end)
    effective_onsite = {d for d in onsite if not (d in cells and cells[d].onsite_removed)}
    request = ScheduleRequest(
        period_from=start,
        period_to=end,
        doctors=[Doctor(u.doctor_code, u.full_name) for u in users],
        holidays=vacations,
        onsite=onsite,
        luxembourg_holidays=set(await public_holidays(db, clinic_id, start, end)),
    )
    by_code = {
        day: (code_of.get(cell.user_id, UNASSIGNED) if cell.user_id else UNASSIGNED)
        for day, cell in cells.items()
    }
    stats = compute_stats(request, by_code, onsite=effective_onsite)
    return ResultsStats(stats=stats, users={u.doctor_code: u for u in users})
