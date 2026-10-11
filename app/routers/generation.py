# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Generate a schedule (/generer) and review the active one (/resultats)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.schemas.forms import GenerateForm
from app.services import calendar as cal
from app.services.planning import GenerationError, build_days, results_stats, run_generation
from app.templating import render, set_flash

router = APIRouter(tags=["generation"])


async def _doctors_context(
    db: DbSession, clinic_id: uuid.UUID, period_from: date, period_to: date
) -> dict[str, Any]:
    """Doctors with their vacation counts and on-site days for the chosen period."""
    valid = period_from <= period_to
    counts = await repo.vacation_counts(db, clinic_id, period_from, period_to) if valid else {}
    onsite = await repo.list_onsite_days(db, clinic_id, period_from, period_to) if valid else set()
    doctors = await users_repo.list_clinic_users(db, clinic_id)
    return {
        "doctors": [(d, counts.get(d.id, 0)) for d in doctors],
        "onsite_count": len(onsite),
        "period_days": (period_to - period_from).days + 1 if valid else 0,
    }


async def _generate_page(
    request: Request,
    clinic_id: uuid.UUID,
    db: DbSession,
    period_from: date,
    period_to: date,
    extra: dict[str, Any] | None = None,
    status_code: int = 200,
) -> Response:
    context = {
        "period_from": period_from,
        "period_to": period_to,
        "active": await repo.get_active_generation(db, clinic_id),
        **await _doctors_context(db, clinic_id, period_from, period_to),
        **(extra or {}),
    }
    return render(request, "app/generate.html", context, status_code)


@router.get("/generer")
async def generate_page(
    request: Request, auth: ClinicAuth, clinic_id: ClinicScope, db: DbSession
) -> Response:
    """Period picker (default: next semester) and doctors summary."""
    period = cal.next_semester(cal.today_local())
    return await _generate_page(request, clinic_id, db, period.start, period.end)


@router.get("/generer/medecins")
async def generate_doctors_fragment(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    period_from: str | None = None,
    period_to: str | None = None,
) -> Response:
    """HTMX fragment: doctors summary refreshed when the period changes."""
    default = cal.next_semester(cal.today_local())
    start = cal.parse_day(period_from, default.start)
    end = cal.parse_day(period_to, default.end)
    context = await _doctors_context(db, clinic_id, start, end)
    return render(request, "partials/_generate_doctors.html", context)


@router.post("/generer")
async def generate(
    request: Request,
    form: Annotated[GenerateForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Run the algorithm and store the new active generation."""
    active = await repo.get_active_generation(db, clinic_id)
    if active is not None and not form.confirm_overwrite:
        return await _generate_page(
            request,
            clinic_id,
            db,
            form.period_from,
            form.period_to,
            {"error": "Confirmez le remplacement de la planification active."},
            400,
        )
    try:
        await run_generation(db, clinic_id, auth.user, form.period_from, form.period_to, request)
    except GenerationError as exc:
        await db.rollback()
        return await _generate_page(
            request, clinic_id, db, form.period_from, form.period_to, {"error": str(exc)}, 400
        )
    await db.commit()
    response = RedirectResponse("/resultats", status_code=303)
    set_flash(response, "Planification générée.")
    return response


@router.get("/resultats")
async def results(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    medecin: str | None = None,
) -> Response:
    """Statistics and monthly grids of the active generation, filterable by doctor."""
    generation = await repo.get_active_generation(db, clinic_id)
    if generation is None:
        return render(request, "app/results.html", {"generation": None})
    days = await build_days(db, clinic_id, generation.period_from, generation.period_to)
    stats = await results_stats(db, clinic_id, generation)
    generated_by = (
        await users_repo.get_user_in_clinic(db, clinic_id, generation.generated_by)
        if generation.generated_by
        else None
    )
    filter_code = medecin if medecin in stats.users else None
    return render(
        request,
        "app/results.html",
        {
            "generation": generation,
            "generated_by": generated_by,
            "days": days,
            "months": cal.months_between(generation.period_from, generation.period_to),
            "stats": stats.stats,
            "users": stats.users,
            "filter_code": filter_code,
            "today": cal.today_local(),
        },
    )
