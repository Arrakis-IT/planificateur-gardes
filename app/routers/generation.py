# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Generate a schedule (/generer) and review the active ones (/resultats)."""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.db import get_sessionmaker
from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.models import Generation
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
from app.services.planning import (
    GenerationError,
    Overlap,
    OverlapConfirmationRequired,
    build_days,
    default_generation,
    find_overlap,
    results_stats,
    run_generation,
)
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
    """Doctors with their vacation / desiderata counts, on-site days and the overlap with
    the active generations for the chosen period."""
    valid = period_from <= period_to
    counts = await repo.vacation_counts(db, clinic_id, period_from, period_to) if valid else {}
    wishes = await repo.desiderata_counts(db, clinic_id, period_from, period_to) if valid else {}
    onsite = await repo.list_onsite_days(db, clinic_id, period_from, period_to) if valid else set()
    doctors = await users_repo.list_clinic_users(db, clinic_id)
    overlap = await find_overlap(db, clinic_id, period_from, period_to) if valid else Overlap()
    return {
        "doctors": [(d, counts.get(d.id, 0), wishes.get(d.id, 0)) for d in doctors],
        "onsite_count": len(onsite),
        "period_days": (period_to - period_from).days + 1 if valid else 0,
        "overlap": overlap,
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
        "actives": await repo.active_generations(db, clinic_id),
        **await _doctors_context(db, clinic_id, period_from, period_to),
        **(extra or {}),
    }
    return render(request, "app/generate.html", context, status_code)


def _fragment_period(period_from: str | None, period_to: str | None) -> tuple[date, date]:
    default = cal.default_generation_period(cal.today_local())
    return cal.parse_day(period_from, default.start), cal.parse_day(period_to, default.end)


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
    """HTMX fragment: doctors summary (and overlap notice) refreshed when the period changes."""
    start, end = _fragment_period(period_from, period_to)
    context = await _doctors_context(db, clinic_id, start, end)
    return render(request, "partials/_generate_doctors.html", context)


@router.get("/generer/chevauchement")
async def generate_overlap_fragment(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    period_from: str | None = None,
    period_to: str | None = None,
) -> Response:
    """Fragment: which months of a proposed period active generations already hold (empty
    when none), to warn before generating."""
    start, end = _fragment_period(period_from, period_to)
    overlap = await find_overlap(db, clinic_id, start, end) if start <= end else Overlap()
    return render(request, "partials/_generate_overlap.html", {"overlap": overlap})


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
    """Run the algorithm, store the new active generation, then email the PDF (background).

    A period meeting active generations first answers 409 with the confirmation dialog
    (transfer or recalculate the months they hold).
    """
    try:
        run = await run_generation(
            db,
            clinic_id,
            auth.user,
            form.period_from,
            form.period_to,
            request,
            form.overlap_mode,
        )
    except OverlapConfirmationRequired as exc:
        await db.rollback()
        return await _generate_page(
            request,
            clinic_id,
            db,
            form.period_from,
            form.period_to,
            {"confirm_overlap": exc.overlap},
            409,
        )
    except GenerationError as exc:
        await db.rollback()
        return await _generate_page(
            request, clinic_id, db, form.period_from, form.period_to, {"error": str(exc)}, 400
        )
    await db.commit()
    # Open pages reload their planning (draft cells of recalculated days were dropped).
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
        previous_generation_ids=run.previous_ids,
    )
    response = RedirectResponse(f"/resultats?generation={run.generation.id}", status_code=303)
    message = "Planification générée. Le PDF est envoyé par e-mail à tous les médecins."
    notices = [message, locked_warning(run.locked_months)]
    if run.mode is not None:
        months = months_label({m for p in run.overlap.transfers for m in p.months})
        notices.append(
            f"Mois transférés depuis les planifications existantes : {months}."
            if run.mode == "move"
            else f"Mois recalculés : {months}."
        )
    if run.archived:
        notices.append(f"{len(run.archived)} planification(s) archivée(s) (plus aucun jour).")
    if run.desiderata_conflicts:
        notices.append(
            f"{run.desiderata_conflicts} jour(s) attribué(s) malgré une desiderata, faute "
            "d'alternative : voir « Conflits avec desiderata »."
        )
    if run.released_months:
        released = months_label(set(run.released_months))
        notices.append(f"Verrouillage retiré (hors des planifications actives) : {released}.")
    set_flash(response, " ".join(n for n in notices if n))
    return response


async def _selected_generation(
    db: DbSession, clinic_id: uuid.UUID, value: str | None
) -> tuple[Generation | None, list[Generation]]:
    """The generation chosen with ?generation= (default: see default_generation)."""
    actives = await repo.active_generations(db, clinic_id)
    chosen = None
    if value:
        try:
            wanted = uuid.UUID(value)
        except ValueError:
            wanted = None
        chosen = next((g for g in actives if g.id == wanted), None)
    return chosen or default_generation(actives, cal.today_local()), actives


@router.get("/resultats")
async def results(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    medecin: str | None = None,
    generation: str | None = None,
) -> Response:
    """Statistics and monthly grids of one active generation, filterable by doctor."""
    selected, actives = await _selected_generation(db, clinic_id, generation)
    if selected is None:
        return render(request, "app/results.html", {"generation": None})
    days = await build_days(
        db, clinic_id, selected.period_from, selected.period_to, with_draft=True
    )
    stats = await results_stats(db, clinic_id, selected, with_draft=True)
    draft_count = await drafts_repo.count_drafts(db, clinic_id)
    generated_by = (
        await users_repo.get_user_in_clinic(db, clinic_id, selected.generated_by)
        if selected.generated_by
        else None
    )
    filter_code = medecin if medecin in stats.users else None
    # Edit links return to this very page (with its filter); none in read-only mode.
    current_page = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return_to = None if auth.read_only else current_page
    held = await repo.assignment_dates(db, selected.id)
    conflicts = [
        days[d] for d in held if d in days and days[d].desiderata_conflict and days[d].doctor
    ]
    return render(
        request,
        "app/results.html",
        {
            "generation": selected,
            "actives": actives,
            "generated_by": generated_by,
            "days": days,
            "months": cal.months_between(selected.period_from, selected.period_to),
            "stats": stats.stats,
            "users": stats.users,
            "filter_code": filter_code,
            "today": cal.today_local(),
            "draft_count": draft_count,
            "return_to": return_to,
            "desiderata_conflicts": conflicts,
            "locked_months": await locked_months(
                db, clinic_id, selected.period_from, selected.period_to
            ),
        },
    )


@router.get("/resultats/pdf", responses={404: {"description": NO_ACTIVE_PLANNING}})
async def results_pdf(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    generation: str | None = None,
) -> Response:
    """Download the PDF of one active generation (default: as on the results page)."""
    selected, _ = await _selected_generation(db, clinic_id, generation)
    clinic = auth.clinic
    if selected is None or clinic is None:
        raise HTTPException(404, NO_ACTIVE_PLANNING)
    generated_by = (
        await users_repo.get_user_in_clinic(db, clinic_id, selected.generated_by)
        if selected.generated_by
        else None
    )
    pdf = await build_schedule_pdf(
        db, clinic, selected, generated_by.full_name if generated_by else None
    )
    filename = pdf_filename(clinic, selected)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
