# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""DEVELOPMENT ONLY — JSON preview of the scheduler, to try it with curl before the UI exists.

Mounted at /_dev by `app.main.create_app` only when APP_ENV=development; it does not exist
in production or tests. It is a separate sub-application (no session, no CSRF, no DB) and
the dev server only listens on 127.0.0.1. Temporary: remove once the generation UI ships.

    curl -s localhost:8000/_dev/schedule-preview -H 'content-type: application/json' -d '{
      "period_from": "2027-01-01", "period_to": "2027-01-31", "seed": 1,
      "doctors": [{"code": "NR"}, {"code": "DT"}, {"code": "MC"}],
      "holidays": {"2027-01-18": ["MC"]}, "onsite": ["2027-01-12"]
    }'
"""

import random
import secrets
import time
from datetime import date

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.services.holidays import holidays_between
from app.services.scheduler import (
    Doctor,
    ScheduleInputError,
    ScheduleRequest,
    compute_stats,
    generate_schedule,
)

MAX_PERIOD_DAYS = 400


class DoctorIn(BaseModel):
    """A doctor of the preview request."""

    code: str = Field(min_length=1, max_length=3)
    name: str = Field(default="", max_length=200)


class PreviewIn(BaseModel):
    """Scheduler input. LU holidays are computed unless given explicitly."""

    period_from: date
    period_to: date
    doctors: list[DoctorIn] = Field(min_length=1, max_length=30)
    holidays: dict[date, list[str]] = Field(default_factory=dict, description="Vacations")
    onsite: list[date] = Field(default_factory=list)
    custom_holidays: dict[date, str] = Field(default_factory=dict)
    luxembourg_holidays: list[date] | None = None
    seed: int | None = None


class DoctorStatsOut(BaseModel):
    """Per-doctor statistics."""

    code: str
    week: int
    weekend: int
    onsite: int
    vacation_days: int


class PreviewOut(BaseModel):
    """Scheduler output with statistics."""

    seed: int
    elapsed_ms: int
    public_holidays: dict[date, str]
    assignments: dict[date, str]
    stats: list[DoctorStatsOut]
    totals: dict[str, int]
    spread: dict[str, int]


def build_dev_app() -> FastAPI:
    """Sub-application with the dev-only endpoints."""
    dev = FastAPI(title="Planificateur — dev only", docs_url="/docs", openapi_url="/openapi.json")

    @dev.post("/schedule-preview")
    def schedule_preview(payload: PreviewIn) -> PreviewOut:
        """Run the scheduler on the given input and return assignments + statistics."""
        if (payload.period_to - payload.period_from).days > MAX_PERIOD_DAYS:
            raise HTTPException(422, f"Période limitée à {MAX_PERIOD_DAYS} jours.")
        public = holidays_between(payload.period_from, payload.period_to, payload.custom_holidays)
        lu_dates = set(payload.luxembourg_holidays) if payload.luxembourg_holidays else set(public)
        request = ScheduleRequest(
            period_from=payload.period_from,
            period_to=payload.period_to,
            doctors=[Doctor(d.code, d.name) for d in payload.doctors],
            holidays={d: set(codes) for d, codes in payload.holidays.items()},
            onsite=set(payload.onsite),
            luxembourg_holidays=lu_dates,
        )
        seed = payload.seed if payload.seed is not None else secrets.randbelow(2**31)
        started = time.perf_counter()
        try:
            assignments = generate_schedule(request, random.Random(seed))  # noqa: S311
        except ScheduleInputError as exc:
            raise HTTPException(422, str(exc)) from exc
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        stats = compute_stats(request, assignments)
        return PreviewOut(
            seed=seed,
            elapsed_ms=elapsed_ms,
            public_holidays=public,
            assignments=assignments,
            stats=[DoctorStatsOut(**vars(s)) for s in stats.doctors],
            totals={
                "week": stats.total_week,
                "weekend": stats.total_weekend,
                "onsite": stats.total_onsite,
                "unassigned": stats.unassigned,
            },
            spread={k: stats.spread(k) for k in ("week", "weekend", "onsite")},
        )

    return dev
