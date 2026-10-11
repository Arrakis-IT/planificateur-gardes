# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Vacations of the current user (/conges) and on-site days of the clinic (/sur-place).

Calendars are forms whose day buttons submit `date`; HTMX swaps the calendar fragment,
and without JavaScript the same form falls back to POST + redirect.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.repositories import scheduling as repo
from app.schemas.forms import DayForm, RangeForm
from app.services import audit
from app.services import calendar as cal
from app.services.planning import public_holidays
from app.templating import render, set_flash

router = APIRouter(tags=["holidays"])


def _semester(value: str | None) -> cal.Period:
    return cal.parse_semester(value) or cal.next_semester(cal.today_local())


def _is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


async def _calendar_context(
    db: DbSession, clinic_id: uuid.UUID, period: cal.Period, marked: set[date]
) -> dict[str, Any]:
    return {
        "period": period,
        "semester": cal.semester_key(period),
        "semester_label": cal.semester_label(period),
        "prev_semester": cal.semester_key(cal.previous_semester(period)),
        "next_semester": cal.semester_key(cal.semester_of(period.end + timedelta(days=1))),
        "months": cal.months_between(period.start, period.end),
        "marked": marked,
        "public_holidays": await public_holidays(db, clinic_id, period.start, period.end),
    }


# --- Vacations ----------------------------------------------------------------------------


async def _vacations_page(
    request: Request, auth: ClinicAuth, clinic_id: uuid.UUID, db: DbSession, period: cal.Period
) -> Response:
    marked = await repo.list_user_vacations(db, clinic_id, auth.user.id, period.start, period.end)
    context = await _calendar_context(db, clinic_id, period, marked)
    template = "partials/_vacation_calendar.html" if _is_htmx(request) else "app/vacations.html"
    return render(request, template, context)


@router.get("/conges")
async def vacations(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    semestre: str | None = None,
) -> Response:
    """The current user's vacation days, one semester at a time."""
    return await _vacations_page(request, auth, clinic_id, db, _semester(semestre))


@router.post("/conges/toggle")
async def vacation_toggle(
    request: Request,
    form: Annotated[DayForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Add / remove one vacation day of the current user."""
    added = await repo.toggle_user_vacation(db, clinic_id, auth.user.id, form.date)
    audit.record(
        db,
        "vacation_added" if added else "vacation_removed",
        request=request,
        user_id=auth.user.id,
        clinic_id=clinic_id,
        target_type="user",
        target_id=auth.user.id,
        meta={"date": form.date.isoformat()},
    )
    await db.commit()
    period = _semester(form.semestre)
    if _is_htmx(request):
        return await _vacations_page(request, auth, clinic_id, db, period)
    return RedirectResponse(f"/conges?semestre={cal.semester_key(period)}", status_code=303)


@router.post("/conges/periode")
async def vacation_range(
    request: Request,
    form: Annotated[RangeForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Add (or remove) a whole range of vacation days."""
    period = _semester(form.semestre)
    target = f"/conges?semestre={cal.semester_key(period)}"
    if form.start > form.end:
        response = RedirectResponse(target, status_code=303)
        set_flash(response, "La date de début doit précéder la date de fin.", "error")
        return response
    if form.action == "remove":
        count = await repo.remove_user_vacation_range(
            db, clinic_id, auth.user.id, form.start, form.end
        )
        message = f"{count} jour(s) de congés retiré(s)."
    else:
        count = await repo.add_user_vacation_range(
            db, clinic_id, auth.user.id, form.start, form.end
        )
        message = f"{count} jour(s) de congés ajouté(s)."
    audit.record(
        db,
        "vacation_range_removed" if form.action == "remove" else "vacation_range_added",
        request=request,
        user_id=auth.user.id,
        clinic_id=clinic_id,
        target_type="user",
        target_id=auth.user.id,
        meta={"start": form.start.isoformat(), "end": form.end.isoformat(), "days": count},
    )
    await db.commit()
    response = RedirectResponse(target, status_code=303)
    set_flash(response, message)
    return response


# --- On-site days ------------------------------------------------------------------------------


async def _onsite_page(
    request: Request, clinic_id: uuid.UUID, db: DbSession, period: cal.Period
) -> Response:
    marked = await repo.list_onsite_days(db, clinic_id, period.start, period.end)
    context = await _calendar_context(db, clinic_id, period, marked)
    template = "partials/_onsite_calendar.html" if _is_htmx(request) else "app/onsite.html"
    return render(request, template, context)


@router.get("/sur-place")
async def onsite(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    semestre: str | None = None,
) -> Response:
    """The clinic's on-site days (editable by every user of the clinic)."""
    return await _onsite_page(request, clinic_id, db, _semester(semestre))


@router.post("/sur-place/toggle")
async def onsite_toggle(
    request: Request,
    form: Annotated[DayForm, Form()],
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
) -> Response:
    """Mark / unmark one on-site day for the clinic."""
    added = await repo.toggle_onsite_day(db, clinic_id, form.date, auth.user.id)
    audit.record(
        db,
        "onsite_added" if added else "onsite_removed",
        request=request,
        user_id=auth.user.id,
        clinic_id=clinic_id,
        target_type="onsite_day",
        meta={"date": form.date.isoformat()},
    )
    await db.commit()
    period = _semester(form.semestre)
    if _is_htmx(request):
        return await _onsite_page(request, clinic_id, db, period)
    return RedirectResponse(f"/sur-place?semestre={cal.semester_key(period)}", status_code=303)
