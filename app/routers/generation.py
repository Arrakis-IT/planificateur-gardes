# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Generate a schedule (/generer) and review the active one (/resultats)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.db import get_sessionmaker
from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.repositories import drafts as drafts_repo
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.routers.sse import present_user
from app.schemas.forms import GenerateForm
from app.services import calendar as cal
from app.services.locks import locked_months, months_label
from app.services.mail import MailSender, get_mail_sender
from app.services.notifications import send_schedule_notification
from app.services.pdf import build_schedule_pdf, pdf_filename
from app.services.planning import GenerationError, build_days, results_stats, run_generation
from app.services.presence import hub
from app.templating import render, set_flash

router = APIRouter(tags=["generation"])

NO_ACTIVE_PLANNING = "Aucune planification active."


def locked_warning(locked: set[date] | frozenset[date]) -> str | None:
    """Notice shown when the period meets locked months (their days are kept)."""
    if not locked:
        return None
    return f"Les mois verrouillés ({months_label(set(locked))}) ne seront pas modifiés."


async def _doctors_context(
    db: DbSession, clinic_id: uuid.UUID, period_from: date, period_to: date
) -> dict[str, Any]:
    """Doctors with their vacation counts and on-site days for the chosen period."""
    valid = period_from <= period_to
    counts = await repo.vacation_counts(db, clinic_id, period_from, period_to) if valid else {}
    onsite = await repo.list_onsite_days(db, clinic_id, period_from, period_to) if valid else set()
    doctors = await users_repo.list_clinic_users(db, clinic_id)
    locked = await locked_months(db, clinic_id, period_from, period_to) if valid else set()
    return {
        "locked_warning": locked_warning(locked),
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
    """Period picker (default: today → end of the month six months ahead) and doctors."""
    period = cal.default_generation_period(cal.today_local())
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
    default = cal.default_generation_period(cal.today_local())
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
    background: BackgroundTasks,
    mailer: Annotated[MailSender, Depends(get_mail_sender)],
) -> Response:
    """Run the algorithm, store the new active generation, then email the PDF (background)."""
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
        run = await run_generation(
            db, clinic_id, auth.user, form.period_from, form.period_to, request
        )
    except GenerationError as exc:
        await db.rollback()
        return await _generate_page(
            request, clinic_id, db, form.period_from, form.period_to, {"error": str(exc)}, 400
        )
    await db.commit()
    # Open pages reload their planning; any shared draft was dropped with the old generation.
    hub.publish(
        clinic_id,
        "draft_cleared",
        {"reason": "regenerated", "changed": 0, "by": present_user(auth.user).as_dict()},
    )
    background.add_task(
        send_schedule_notification,
        get_sessionmaker(),
        mailer,
        clinic_id,
        run.generation.id,
        auth.user.id,
        "generated",
        previous_generation_id=run.previous_id,
    )
    response = RedirectResponse("/resultats", status_code=303)
    message = "Planification générée. Le PDF est envoyé par e-mail à tous les médecins."
    notices = [message, locked_warning(run.locked_months)]
    if run.desiderata_conflicts:
        notices.append(
            f"{run.desiderata_conflicts} jour(s) attribué(s) malgré une desiderata, faute "
            "d'alternative : voir « Conflits avec desiderata »."
        )
    if run.released_months:
        released = months_label(set(run.released_months))
        notices.append(f"Verrouillage retiré (hors de la nouvelle période) : {released}.")
    set_flash(response, " ".join(n for n in notices if n))
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
    days = await build_days(
        db, clinic_id, generation.period_from, generation.period_to, with_draft=True
    )
    stats = await results_stats(db, clinic_id, generation, with_draft=True)
    draft_count = await drafts_repo.count_drafts(db, clinic_id)
    generated_by = (
        await users_repo.get_user_in_clinic(db, clinic_id, generation.generated_by)
        if generation.generated_by
        else None
    )
    filter_code = medecin if medecin in stats.users else None
    # Edit links return to this very page (with its filter); none in read-only mode.
    current_page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return_to = None if auth.read_only else current_page
    conflicts = [view for view in days.values() if view.desiderata_conflict and view.doctor]
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
            "draft_count": draft_count,
            "return_to": return_to,
            "desiderata_conflicts": conflicts,
            "locked_months": await locked_months(
                db, clinic_id, generation.period_from, generation.period_to
            ),
        },
    )


@router.get("/resultats/pdf", responses={404: {"description": NO_ACTIVE_PLANNING}})
async def results_pdf(
    request: Request, auth: ClinicAuth, clinic_id: ClinicScope, db: DbSession
) -> Response:
    """Download the PDF of the active generation."""
    generation = await repo.get_active_generation(db, clinic_id)
    clinic = auth.clinic
    if generation is None or clinic is None:
        raise HTTPException(404, NO_ACTIVE_PLANNING)
    generated_by = (
        await users_repo.get_user_in_clinic(db, clinic_id, generation.generated_by)
        if generation.generated_by
        else None
    )
    pdf = await build_schedule_pdf(
        db, clinic, generation, generated_by.full_name if generated_by else None
    )
    filename = pdf_filename(clinic, generation)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
