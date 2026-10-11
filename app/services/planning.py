# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Glue between the database and the pure scheduler: view models and the generate flow."""

import uuid
from dataclasses import dataclass
from datetime import date, timedelta

import anyio
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assignment, Generation, User
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.services import audit
from app.services.holidays import holidays_between
from app.services.scheduler import (
    UNASSIGNED,
    Doctor,
    ScheduleRequest,
    ScheduleStats,
    compute_stats,
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
    doctor: User | None  # None + planned = unassigned ("XX")

    @property
    def unassigned(self) -> bool:
        """Covered by the generation but nobody available."""
        return self.planned and self.doctor is None


async def public_holidays(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, str]:
    """Official LU + clinic custom holidays within [start, end]."""
    return holidays_between(start, end, await repo.custom_holidays(db, clinic_id, start, end))


async def build_days(
    db: AsyncSession, clinic_id: uuid.UUID, start: date, end: date
) -> dict[date, DayView]:
    """Day views for [start, end] from the clinic's active generation."""
    assignments = await repo.active_assignments(db, clinic_id, start, end)
    onsite = await repo.list_onsite_days(db, clinic_id, start, end)
    holidays = await public_holidays(db, clinic_id, start, end)
    users = await repo.users_by_id(db, clinic_id)
    days: dict[date, DayView] = {}
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        assignment = assignments.get(day)
        removed = assignment is not None and assignment.onsite_removed
        days[day] = DayView(
            day=day,
            weekend=is_weekend(day) or day in holidays,
            holiday=holidays.get(day),
            onsite=day in onsite and not removed,
            planned=assignment is not None,
            doctor=users.get(assignment.user_id)
            if assignment is not None and assignment.user_id
            else None,
        )
    return days


# --- Generate -------------------------------------------------------------------------------


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
) -> Generation:
    """Run the scheduler, archive the active generation and store the new one. Caller commits."""
    validate_period(period_from, period_to)
    await repo.lock_clinic(db, clinic_id)

    doctors = await users_repo.list_clinic_users(db, clinic_id)
    if not doctors:
        raise GenerationError("Le cabinet n'a aucun médecin actif.")
    code_of = {u.id: u.doctor_code for u in doctors}
    vacations = {
        day: {code_of[uid] for uid in user_ids if uid in code_of}
        for day, user_ids in (
            await repo.vacations_by_date(db, clinic_id, period_from, period_to)
        ).items()
    }
    schedule_request = ScheduleRequest(
        period_from=period_from,
        period_to=period_to,
        doctors=[Doctor(u.doctor_code, u.full_name) for u in doctors],
        holidays=vacations,
        onsite=await repo.list_onsite_days(db, clinic_id, period_from, period_to),
        luxembourg_holidays=set(await public_holidays(db, clinic_id, period_from, period_to)),
    )
    # CPU-bound (≈0.1–0.5 s): keep the event loop responsive.
    result = await anyio.to_thread.run_sync(generate_schedule, schedule_request)

    user_of = {u.doctor_code: u.id for u in doctors}
    previous_id = await repo.archive_active_generation(db, clinic_id)
    generation = await repo.create_generation(
        db,
        clinic_id,
        period_from,
        period_to,
        actor.id,
        {day: (None if code == UNASSIGNED else user_of[code]) for day, code in result.items()},
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
            "unassigned": sum(1 for code in result.values() if code == UNASSIGNED),
            "archived_generation": str(previous_id) if previous_id else None,
        },
    )
    return generation


# --- Results statistics -----------------------------------------------------------------------


@dataclass(frozen=True)
class ResultsStats:
    """Statistics of the active generation with the users behind each code."""

    stats: ScheduleStats
    users: dict[str, User]


async def results_stats(
    db: AsyncSession, clinic_id: uuid.UUID, generation: Generation
) -> ResultsStats:
    """Port of the standalone app's statistics table for a stored generation."""
    start, end = generation.period_from, generation.period_to
    assignments: dict[date, Assignment] = await repo.active_assignments(db, clinic_id, start, end)
    all_users = await repo.users_by_id(db, clinic_id)
    involved = {a.user_id for a in assignments.values() if a.user_id}
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
    effective_onsite = {
        d for d in onsite if not (d in assignments and assignments[d].onsite_removed)
    }
    request = ScheduleRequest(
        period_from=start,
        period_to=end,
        doctors=[Doctor(u.doctor_code, u.full_name) for u in users],
        holidays=vacations,
        onsite=onsite,
        luxembourg_holidays=set(await public_holidays(db, clinic_id, start, end)),
    )
    by_code = {
        day: (code_of.get(a.user_id, UNASSIGNED) if a.user_id else UNASSIGNED)
        for day, a in assignments.items()
    }
    stats = compute_stats(request, by_code, onsite=effective_onsite)
    return ResultsStats(stats=stats, users={u.doctor_code: u for u in users})
