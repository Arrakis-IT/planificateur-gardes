# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Schedule PDF (WeasyPrint: Jinja2 HTML + print CSS → PDF)."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from itertools import groupby
from pathlib import Path

import anyio
from sqlalchemy.ext.asyncio import AsyncSession
from weasyprint import HTML

from app.models import Clinic, Generation
from app.services import calendar as cal
from app.services.planning import DayView, build_days, results_stats
from app.services.scheduler import ScheduleStats
from app.templating import templates

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


@dataclass(frozen=True)
class MonthRows:
    """The days of one calendar month (one PDF page)."""

    first: date
    days: list[DayView]


def pdf_filename(clinic: Clinic, generation: Generation) -> str:
    """planification-gardes-{slug}-{from:%Y%m%d}-{to:%Y%m%d}.pdf (brief §7.2)."""
    return (
        f"planification-gardes-{clinic.slug}-"
        f"{generation.period_from:%Y%m%d}-{generation.period_to:%Y%m%d}.pdf"
    )


def render_schedule_html(
    clinic: Clinic,
    generation: Generation,
    days: dict[date, DayView],
    stats: ScheduleStats,
    users: dict[str, object],
    actor_name: str | None,
    edited: bool,
    when: datetime,
) -> str:
    """HTML source of the PDF (also handy to debug the layout in a browser)."""
    ordered = [days[d] for d in sorted(days)]
    months = [
        MonthRows(first, list(group))
        for first, group in groupby(ordered, key=lambda v: v.day.replace(day=1))
    ]
    template = templates.get_template("pdf/schedule.html")
    return template.render(
        clinic=clinic,
        generation=generation,
        months=months,
        stats=stats,
        users=users,
        actor_name=actor_name,
        edited=edited,
        when=cal.short_date(when.astimezone(cal.CLINIC_TZ).date()),
    )


def html_to_pdf(html: str) -> bytes:
    """Render HTML to PDF bytes; relative URLs (fonts) resolve against /static."""
    pdf: bytes = HTML(string=html, base_url=str(STATIC_DIR) + "/").write_pdf()
    return pdf


async def build_schedule_pdf(
    db: AsyncSession,
    clinic: Clinic,
    generation: Generation,
    actor_name: str | None,
    *,
    edited: bool = False,
    when: datetime | None = None,
) -> bytes:
    """Load the generation data and render its PDF (rendering runs in a worker thread)."""
    clinic_id: uuid.UUID = clinic.id
    days = await build_days(db, clinic_id, generation.period_from, generation.period_to)
    stats = await results_stats(db, clinic_id, generation)
    html = render_schedule_html(
        clinic,
        generation,
        days,
        stats.stats,
        dict(stats.users),
        actor_name,
        edited,
        when or generation.generated_at,
    )
    return await anyio.to_thread.run_sync(html_to_pdf, html)
