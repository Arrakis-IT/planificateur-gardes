# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""User-facing calendar views: today/tomorrow dashboard, week list, month grid."""

from datetime import timedelta

from fastapi import APIRouter, Request
from starlette.responses import Response

from app.dependencies import ClinicAuth, ClinicScope, DbSession
from app.repositories import drafts as drafts_repo
from app.services import calendar as cal
from app.services.planning import build_days
from app.templating import render

router = APIRouter(tags=["app"])


@router.get("/")
async def dashboard(
    request: Request, auth: ClinicAuth, clinic_id: ClinicScope, db: DbSession
) -> Response:
    """Two large cards: today's and tomorrow's doctor on duty."""
    today = cal.today_local()
    days = await build_days(db, clinic_id, today, today + timedelta(days=1))
    return render(
        request,
        "app/dashboard.html",
        {"cards": [("Aujourd'hui", days[today]), ("Demain", days[today + timedelta(days=1)])]},
    )


@router.get("/semaine")
async def week(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    date: str | None = None,
) -> Response:
    """The 7 days (Mon–Sun) of a week, today highlighted."""
    today = cal.today_local()
    week_dates = cal.week_days(cal.parse_day(date, today))
    days = await build_days(db, clinic_id, week_dates[0], week_dates[-1])
    return render(
        request,
        "app/week.html",
        {
            "days": [days[d] for d in week_dates],
            "today": today,
            "prev": week_dates[0] - timedelta(days=7),
            "next": week_dates[0] + timedelta(days=7),
            "this_week": week_dates[0] <= today <= week_dates[-1],
        },
    )


@router.get("/mois")
async def month(
    request: Request,
    auth: ClinicAuth,
    clinic_id: ClinicScope,
    db: DbSession,
    mois: str | None = None,
) -> Response:
    """7-column grid of a month with the doctor code of each day."""
    today = cal.today_local()
    first = cal.parse_month(mois, today)
    last = cal.add_months(first, 1) - timedelta(days=1)
    days = await build_days(db, clinic_id, first, last, with_draft=True)
    return render(
        request,
        "app/month.html",
        {
            "draft_count": await drafts_repo.count_drafts(db, clinic_id),
            "return_to": f"/mois?mois={first:%Y-%m}",
            "first": first,
            "weeks": cal.month_weeks(first),
            "days": days,
            "today": today,
            "prev": cal.add_months(first, -1),
            "next": cal.add_months(first, 1),
        },
    )
