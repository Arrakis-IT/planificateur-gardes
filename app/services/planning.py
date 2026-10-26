# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Glue between the database and the pure scheduler: view models and the generate flow.

Several generations of a clinic can be active at once (archived_at IS NULL), never two with
an assignment on the same day. Calendars show the union of the active generations and, for
the days none of them holds, the most recent archived assignment (read-only history).
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import anyio
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DesiderataConflict, Generation, User
from app.repositories import drafts as drafts_repo
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.services import audit
from app.services import calendar as cal
from app.services.holidays import holidays_between
from app.services.locks import is_locked, locked_months, release_unowned_locks
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
    planned: bool  # covered by an active generation (or by an archived one: `archived`)
    doctor: User | None  # no doctor on a planned day means "unassigned" (XX)
    drafted: bool = False  # shows a not yet saved edit of the shared draft
    onsite_setting: bool = False  # marked on-site in the clinic settings
    locked: bool = False  # in a month locked by the group admin (read-only)
    # The doctor had a desiderata that day (stored flag, see Assignment.desiderata_conflict).
    desiderata_conflict: DesiderataConflict | None = None
    archived: bool = False  # history of an archived generation: read-only

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
    archived: bool = False


async def _effective_cells(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    start: date,
    end: date,
    with_draft: bool,
    generation_id: uuid.UUID | None = None,
) -> tuple[dict[date, _Cell], set[date]]:
    """Cells of the active generations (or of one of them), draft applied if asked.

    Without `generation_id`, days no active generation holds fall back to the most recent
    archived assignment. Also returns the dates overridden by the draft.
    """
    assignments = await repo.active_assignments(db, clinic_id, start, end, generation_id)
    cells = {
        d: _Cell(a.user_id, a.onsite_removed, a.desiderata_conflict) for d, a in assignments.items()
    }
    if generation_id is None:
        for d, a in (await repo.archived_assignments(db, clinic_id, start, end)).items():
            if d not in cells:
                cells[d] = _Cell(a.user_id, a.onsite_removed, a.desiderata_conflict, archived=True)
    drafted: set[date] = set()
    if with_draft and assignments:
        drafts = {
            d: draft
            for d, draft in (await drafts_repo.clinic_drafts(db, clinic_id)).items()
            if d in assignments and draft.generation_id == assignments[d].generation_id
        }
        wishes = await repo.desiderata_by_date(db, clinic_id, start, end) if drafts else {}
        for d, draft in drafts.items():
            saved = cells[d]
            conflict = saved.desiderata_conflict
            if draft.user_id != saved.user_id:  # as "Enregistrer" will store it
                manual = draft.user_id is not None and draft.user_id in wishes.get(d, set())
                conflict = DesiderataConflict.MANUAL if manual else None
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
    """Day views for [start, end] from the clinic's active generations (archived history
    for the days none of them holds).

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
            archived=cell is not None and cell.archived,
        )
    return days


# --- Active generations ---------------------------------------------------------------------


def default_generation(generations: list[Generation], today: date) -> Generation | None:
    """The generation shown by default: the one covering today, else the next one, else the
    latest."""
    if not generations:
        return None
    for generation in generations:
        if generation.period_from <= today <= generation.period_to:
            return generation
    upcoming = [g for g in generations if g.period_from > today]
    if upcoming:
        return min(upcoming, key=lambda g: g.period_from)
    return max(generations, key=lambda g: g.period_to)


async def archive_generation(
    db: AsyncSession,
    generation: Generation,
    *,
    reason: str,
    actor_id: uuid.UUID | None = None,
    request: Request | None = None,
) -> None:
    """Archive one generation (audited with its reason) and drop its draft cells.

    An archived generation is never reactivated: its days may since belong to another
    active generation. Caller commits.
    """
    repo.archive(generation)
    await drafts_repo.clear_drafts(db, generation.clinic_id, generation.id)
    audit.record(
        db,
        "generation_archived",
        request=request,
        user_id=actor_id,
        clinic_id=generation.clinic_id,
        target_type="generation",
        target_id=generation.id,
        meta={
            "period_from": generation.period_from.isoformat(),
            "period_to": generation.period_to.isoformat(),
            "reason": reason,
        },
    )
    await db.flush()


async def archive_expired(
    db: AsyncSession, today: date, clinic_id: uuid.UUID | None = None
) -> list[Generation]:
    """Archive the active generations whose whole period is before `today` (reason
    "expired"). Their days stay visible as history. Caller commits."""
    expired = await repo.expired_generations(db, today, clinic_id)
    for generation in expired:
        await archive_generation(db, generation, reason="expired")
    return expired


async def shrink_or_archive(
    db: AsyncSession, generation: Generation, *, actor_id: uuid.UUID, request: Request | None
) -> bool:
    """Fit the period of a generation to its remaining assignments; archive it when none is
    left (reason "emptied"). Returns True if it was archived. Caller commits."""
    dates = await repo.assignment_dates(db, generation.id)
    if not dates:
        await archive_generation(
            db, generation, reason="emptied", actor_id=actor_id, request=request
        )
        return True
    generation.period_from, generation.period_to = dates[0], dates[-1]
    await db.flush()
    return False


# --- Overlap with the active generations ----------------------------------------------------

OverlapMode = Literal["move", "recalc"]


@dataclass(frozen=True)
class OverlapPart:
    """What a new generation takes from one active generation."""

    generation: Generation
    days: tuple[date, ...]  # days of the new period held by `generation`, months not locked
    kept_days: tuple[date, ...]  # days of the new period in locked months: stay put

    @property
    def months(self) -> list[date]:
        """First days of the months whose days are taken."""
        return sorted({d.replace(day=1) for d in self.days})

    @property
    def kept_months(self) -> list[date]:
        """First days of the locked months that stay in `generation`."""
        return sorted({d.replace(day=1) for d in self.kept_days})


@dataclass(frozen=True)
class Overlap:
    """Active generations met by a proposed period."""

    parts: list[OverlapPart] = field(default_factory=list)

    @property
    def transfers(self) -> list[OverlapPart]:
        """Parts with days to transfer / recalculate (the confirmation is about these)."""
        return [p for p in self.parts if p.days]

    @property
    def kept_days(self) -> set[date]:
        """Days of the new period that stay in another generation (locked months)."""
        return {d for p in self.parts for d in p.kept_days}


async def find_overlap(
    db: AsyncSession, clinic_id: uuid.UUID, period_from: date, period_to: date
) -> Overlap:
    """Days of [period_from, period_to] already held by an active generation, split by
    generation into transferable days and days of locked months."""
    held = await repo.active_assignments(db, clinic_id, period_from, period_to)
    if not held:
        return Overlap()
    locked = await locked_months(db, clinic_id, period_from, period_to)
    by_generation: dict[uuid.UUID, tuple[list[date], list[date]]] = {}
    for day, assignment in sorted(held.items()):
        days, kept = by_generation.setdefault(assignment.generation_id, ([], []))
        (kept if is_locked(day, locked) else days).append(day)
    generations = {g.id: g for g in await repo.active_generations(db, clinic_id)}
    return Overlap(
        [
            OverlapPart(generations[gid], tuple(days), tuple(kept))
            for gid, (days, kept) in sorted(
                by_generation.items(), key=lambda item: generations[item[0]].period_from
            )
        ]
    )


# --- Generate -------------------------------------------------------------------------------


class OverlapConfirmationRequired(Exception):
    """The period meets active generations: the user must choose how to handle it."""

    def __init__(self, overlap: Overlap) -> None:
        self.overlap = overlap
        super().__init__("overlap confirmation required")


@dataclass(frozen=True)
class GenerationRun:
    """The new active generation and what it did to the others (notices, changelog email)."""

    generation: Generation
    # Archived generations holding the replaced assignments (recalculated days): the
    # changelog email compares them with the new one.
    previous_ids: tuple[uuid.UUID, ...] = ()
    locked_months: frozenset[date] = frozenset()  # kept in another generation (first days)
    released_months: frozenset[date] = frozenset()  # locks dropped: no generation left there
    desiderata_conflicts: int = 0  # days given to a doctor despite a desiderata
    overlap: Overlap = field(default_factory=Overlap)
    mode: OverlapMode | None = None
    archived: tuple[Generation, ...] = ()  # active generations emptied by this one


def validate_period(period_from: date, period_to: date) -> None:
    """Raise GenerationError for an unusable period."""
    if period_from >= period_to:
        raise GenerationError("La date de début doit précéder la date de fin.")
    if (period_to - period_from).days + 1 > MAX_PERIOD_DAYS:
        raise GenerationError(f"La période ne peut pas dépasser {MAX_PERIOD_DAYS} jours.")


async def _replace_days(
    db: AsyncSession, part: OverlapPart, *, actor_id: uuid.UUID, request: Request | None
) -> uuid.UUID:
    """Recalculated days: their old assignments leave `part.generation` for an archived
    generation (history, changelog). Returns that archived generation's id."""
    source = part.generation
    remaining = set(await repo.assignment_dates(db, source.id)) - set(part.days)
    if not remaining:  # wholly replaced: archive it as it is
        await archive_generation(db, source, reason="replaced", actor_id=actor_id, request=request)
        return source.id
    split = Generation(
        clinic_id=source.clinic_id,
        period_from=part.days[0],
        period_to=part.days[-1],
        generated_by=source.generated_by,
        generated_at=source.generated_at,
        archived_at=datetime.now(UTC),
        is_active=False,
    )
    db.add(split)
    await db.flush()
    await repo.move_assignments(db, source.id, split.id, list(part.days))
    audit.record(
        db,
        "generation_split",
        request=request,
        user_id=actor_id,
        clinic_id=source.clinic_id,
        target_type="generation",
        target_id=source.id,
        meta={
            "archived_generation": str(split.id),
            "period_from": part.days[0].isoformat(),
            "period_to": part.days[-1].isoformat(),
            "reason": "replaced",
        },
    )
    return split.id


async def run_generation(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    actor: User,
    period_from: date,
    period_to: date,
    request: Request | None = None,
    overlap_mode: OverlapMode | None = None,
) -> GenerationRun:
    """Run the scheduler and store a new active generation. Caller commits.

    Days of the period already held by an active generation A:
    * in a month locked: they stay in A (the new generation has no assignment there);
    * otherwise, `overlap_mode` "move": A's assignments move to the new generation as they
      are (manual edits, flags, draft cells); "recalc": they get the scheduler's choice and
      the old ones go to an archived generation (A itself when it is wholly replaced).
    A is then fitted to its remaining assignments, or archived if none is left. Without
    `overlap_mode`, an overlap raises OverlapConfirmationRequired. Locks of months no active
    generation holds any more are released. Days whose doctor had a desiderata are flagged.
    """
    validate_period(period_from, period_to)
    await repo.lock_clinic(db, clinic_id)
    await archive_expired(db, cal.today_local(), clinic_id)

    doctors = await users_repo.list_clinic_users(db, clinic_id)
    if not doctors:
        raise GenerationError("Le groupe n'a aucun médecin actif.")
    overlap = await find_overlap(db, clinic_id, period_from, period_to)
    if overlap.transfers and overlap_mode is None:
        raise OverlapConfirmationRequired(overlap)

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

    # Days the new generation does not take from the scheduler: kept elsewhere (locked) or
    # moved over as they are.
    moved = {d for p in overlap.transfers for d in p.days} if overlap_mode == "move" else set()
    skipped = overlap.kept_days | moved
    user_of = {u.doctor_code: u.id for u in doctors}
    assignments = {
        day: (None if code == UNASSIGNED else user_of[code])
        for day, code in result.items()
        if day not in skipped
    }
    conflicts = {
        day: DesiderataConflict.GENERATED
        for day in desiderata_conflicts(schedule_request, result)
        if day not in skipped
    }
    generation = await repo.create_generation(
        db, clinic_id, period_from, period_to, actor.id, assignments, None, conflicts
    )

    previous_ids: list[uuid.UUID] = []
    archived: list[Generation] = []
    for part in overlap.transfers:
        days = list(part.days)
        if overlap_mode == "move":
            await repo.move_assignments(db, part.generation.id, generation.id, days)
            await drafts_repo.move_drafts(db, clinic_id, part.generation.id, generation.id, days)
        else:
            await drafts_repo.clear_drafts(db, clinic_id, part.generation.id, days)
            previous_ids.append(await _replace_days(db, part, actor_id=actor.id, request=request))
        if part.generation.active:
            await shrink_or_archive(db, part.generation, actor_id=actor.id, request=request)
        if not part.generation.active and part.generation not in archived:
            archived.append(part.generation)
    await repo.snapshot_onsite(db, clinic_id, generation.id)
    released = await release_unowned_locks(db, clinic_id, actor_id=actor.id, request=request)
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
            "locked_months": sorted(
                m.strftime("%Y-%m") for m in {d.replace(day=1) for d in overlap.kept_days}
            ),
            "overlap_mode": overlap_mode if overlap.transfers else None,
            "overlap": [
                {
                    "generation": str(p.generation.id),
                    "months": [m.strftime("%Y-%m") for m in p.months],
                    "kept_months": [m.strftime("%Y-%m") for m in p.kept_months],
                }
                for p in overlap.parts
            ],
            "archived_generations": [str(g.id) for g in archived],
        },
    )
    return GenerationRun(
        generation,
        tuple(previous_ids),
        frozenset(d.replace(day=1) for d in overlap.kept_days),
        frozenset(released),
        len(conflicts),
        overlap,
        overlap_mode if overlap.transfers else None,
        tuple(archived),
    )


# --- Results statistics -----------------------------------------------------------------------


@dataclass(frozen=True)
class ResultsStats:
    """Statistics of one generation with the users behind each code."""

    stats: ScheduleStats
    users: dict[str, User]


async def results_stats(
    db: AsyncSession, clinic_id: uuid.UUID, generation: Generation, *, with_draft: bool = False
) -> ResultsStats:
    """Port of the standalone app's statistics table for a stored generation: only its own
    assignments count (days of its period held by another generation are left out).

    Vacations and desiderata are loaded for the generation period only ('Congés' and
    'Desiderata' columns scope, see `app.services.scheduler.compute_stats`).
    """
    start, end = generation.period_from, generation.period_to
    cells, _ = await _effective_cells(db, clinic_id, start, end, with_draft, generation.id)
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
        if day in cells
    }
    desiderata = {
        day: {code_of[uid] for uid in uids if uid in code_of}
        for day, uids in (await repo.desiderata_by_date(db, clinic_id, start, end)).items()
        if day in cells
    }
    onsite = await repo.list_onsite_days(db, clinic_id, start, end)
    effective_onsite = {d for d in onsite if d in cells and not cells[d].onsite_removed}
    request = ScheduleRequest(
        period_from=start,
        period_to=end,
        doctors=[Doctor(u.doctor_code, u.full_name) for u in users],
        holidays=vacations,
        onsite=onsite,
        luxembourg_holidays=set(await public_holidays(db, clinic_id, start, end)),
        desiderata=desiderata,
    )
    by_code = {
        day: (code_of.get(cell.user_id, UNASSIGNED) if cell.user_id else UNASSIGNED)
        for day, cell in cells.items()
    }
    stats = compute_stats(request, by_code, onsite=effective_onsite, days=set(cells))
    return ResultsStats(stats=stats, users={u.doctor_code: u for u in users})
