# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Manual edits through the clinic's shared draft (brief §6.3).

Cells are addressed by date within the requester's clinic active generation, never by id.
With HTMX the cell modal is a fragment and edits answer 204 + `HX-Trigger` (the page then
refreshes its #planning block); without JavaScript every route degrades to full pages and
redirects.
"""

import json
import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.db import get_sessionmaker
from app.dependencies import ClinicAuth, ClinicScope, DbSession, safe_next_path
from app.repositories import drafts as drafts_repo
from app.repositories import scheduling as repo
from app.repositories import users as users_repo
from app.routers.sse import present_user
from app.schemas.forms import CellDoctorForm, ReturnToForm
from app.services import editing
from app.services.mail import MailSender, get_mail_sender
from app.services.notifications import send_schedule_notification
from app.services.planning import build_days
from app.services.presence import hub
from app.templating import render, set_flash

RESULTS_PATH = "/resultats"
MONTH_PATH = "/mois"
DEFAULT_RETURN = RESULTS_PATH

router = APIRouter(prefix=RESULTS_PATH, tags=["editing"])
NOT_IN_PLANNING = "Ce jour ne fait pas partie de la planification active."
UNKNOWN_DOCTOR_MESSAGE = "Médecin inconnu dans ce groupe."
NOT_ONSITE_MESSAGE = "Ce jour n'est pas un jour sur place."

# Documented error responses (OpenAPI).
CELL_NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": NOT_IN_PLANNING}}
UNKNOWN_DOCTOR: dict[int | str, dict[str, Any]] = {400: {"description": UNKNOWN_DOCTOR_MESSAGE}}
NOT_ONSITE: dict[int | str, dict[str, Any]] = {400: {"description": NOT_ONSITE_MESSAGE}}


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def _return_to(value: str | None) -> str:
    """Only the editable views are valid return targets."""
    target = safe_next_path(value)
    return target if target.startswith((RESULTS_PATH, MONTH_PATH)) else DEFAULT_RETURN


def _after_edit(request: Request, return_to: str) -> Response:
    if _is_htmx(request):
        triggers = {"closeModal": True, "planningChanged": True}
        return Response(status_code=204, headers={"HX-Trigger": json.dumps(triggers)})
    return RedirectResponse(return_to, status_code=303)


async def _broadcast_draft(
    db: DbSession, clinic_id: uuid.UUID, auth: ClinicAuth, day: date
) -> None:
    """Tell every open page of the clinic that the draft changed."""
    count = await drafts_repo.count_drafts(db, clinic_id)
    hub.publish(
        clinic_id,
        "draft_changed" if count else "draft_cleared",
        {"count": count, "date": day.isoformat(), "by": present_user(auth.user).as_dict()},
    )


@router.get("/jour/{day}", responses=CELL_NOT_FOUND)
async def cell_modal(
    request: Request,
    day: date,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    retour: str | None = None,
) -> Response:
    """Edit modal of one day (HTMX fragment, or a full page without JavaScript)."""
    try:
        cell = await editing.get_cell(db, clinic_id, day)
    except editing.CellNotFoundError:
        raise HTTPException(404, NOT_IN_PLANNING) from None
    view = (await build_days(db, clinic_id, day, day, with_draft=True))[day]
    away = (await repo.vacations_by_date(db, clinic_id, day, day)).get(day, set())
    doctors = await users_repo.list_clinic_users(db, clinic_id)
    context = {
        "day": view,
        "cell": cell,
        "doctors": [(d, d.id in away) for d in doctors],
        "return_to": _return_to(retour),
    }
    template = "partials/_day_modal.html" if _is_htmx(request) else "app/day_edit.html"
    return render(request, template, context)


@router.post("/jour/{day}", responses=CELL_NOT_FOUND | UNKNOWN_DOCTOR)
async def cell_set_doctor(
    request: Request,
    day: date,
    form: Annotated[CellDoctorForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Choose the doctor of a day (or "XX") in the shared draft."""
    try:
        await editing.set_cell_doctor(db, clinic_id, day, form.doctor, auth.user)
    except editing.CellNotFoundError:
        raise HTTPException(404, NOT_IN_PLANNING) from None
    except editing.InvalidDoctorError:
        raise HTTPException(400, UNKNOWN_DOCTOR_MESSAGE) from None
    await db.commit()
    await _broadcast_draft(db, clinic_id, auth, day)
    return _after_edit(request, _return_to(form.retour))


@router.post("/jour/{day}/sur-place", responses=CELL_NOT_FOUND | NOT_ONSITE)
async def cell_toggle_onsite(
    request: Request,
    day: date,
    form: Annotated[ReturnToForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Remove / restore the on-site mark of a day, in the results only (draft)."""
    try:
        await editing.toggle_cell_onsite(db, clinic_id, day, auth.user)
    except editing.CellNotFoundError:
        raise HTTPException(404, NOT_IN_PLANNING) from None
    except editing.NotOnsiteError:
        raise HTTPException(400, NOT_ONSITE_MESSAGE) from None
    await db.commit()
    await _broadcast_draft(db, clinic_id, auth, day)
    return _after_edit(request, _return_to(form.retour))


@router.post("/enregistrer")
async def save(
    request: Request,
    form: Annotated[ReturnToForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    background: BackgroundTasks,
    mailer: Annotated[MailSender, Depends(get_mail_sender)],
) -> Response:
    """Persist the draft, audit each cell, email the PDF ("Modifié par") to the clinic."""
    result = await editing.save_draft(db, clinic_id, auth.user, request)
    await db.commit()
    hub.publish(
        clinic_id,
        "draft_cleared",
        {
            "reason": "saved",
            "changed": result.changed_cells,
            "by": present_user(auth.user).as_dict(),
        },
    )
    response = RedirectResponse(_return_to(form.retour), status_code=303)
    if result.generation is not None and result.changed_cells:
        background.add_task(
            send_schedule_notification,
            get_sessionmaker(),
            mailer,
            clinic_id,
            result.generation.id,
            auth.user.id,
            "edited",
        )
        set_flash(
            response,
            f"{result.changed_cells} modification(s) enregistrée(s). "
            "Le PDF est envoyé par e-mail à tous les médecins.",
        )
    else:
        set_flash(response, "Aucune modification à enregistrer.")
    return response


@router.post("/annuler")
async def cancel(
    request: Request,
    form: Annotated[ReturnToForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Discard the whole shared draft, for everybody."""
    discarded = await editing.discard_draft(db, clinic_id, auth.user, request)
    await db.commit()
    hub.publish(
        clinic_id,
        "draft_cleared",
        {"reason": "cancelled", "changed": 0, "by": present_user(auth.user).as_dict()},
    )
    response = RedirectResponse(_return_to(form.retour), status_code=303)
    set_flash(response, f"{discarded} modification(s) annulée(s).")
    return response
