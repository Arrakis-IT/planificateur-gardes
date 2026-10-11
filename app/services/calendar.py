# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Calendar helpers for the views: periods, month grids, French labels, local "today"."""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

CLINIC_TZ = ZoneInfo("Europe/Luxembourg")

WEEKDAYS_FR = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")
WEEKDAYS_SHORT_FR = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")
MONTHS_FR = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)  # fmt: skip


def today_local(now: datetime | None = None) -> date:
    """Today's date in Luxembourg (the server runs in UTC)."""
    return (now or datetime.now(CLINIC_TZ)).astimezone(CLINIC_TZ).date()


@dataclass(frozen=True)
class Period:
    """An inclusive date range."""

    start: date
    end: date

    def __contains__(self, d: object) -> bool:
        return isinstance(d, date) and self.start <= d <= self.end

    @property
    def days(self) -> int:
        """Number of days in the period."""
        return (self.end - self.start).days + 1


def semester_of(d: date) -> Period:
    """The semester (Jan–Jun or Jul–Dec) containing `d`."""
    if d.month <= 6:
        return Period(date(d.year, 1, 1), date(d.year, 6, 30))
    return Period(date(d.year, 7, 1), date(d.year, 12, 31))


def next_semester(today: date) -> Period:
    """Default planning period, as in the standalone app (brief §5.3)."""
    return semester_of(semester_of(today).end + timedelta(days=1))


def previous_semester(period: Period) -> Period:
    """The semester before `period`'s semester."""
    return semester_of(period.start - timedelta(days=1))


def parse_semester(value: str | None) -> Period | None:
    """Parse "2027-1" / "2027-2" into a semester period."""
    if not value:
        return None
    try:
        year_s, half_s = value.split("-", 1)
        year, half = int(year_s), int(half_s)
    except ValueError:
        return None
    if half not in (1, 2) or not 2000 <= year <= 2100:
        return None
    return semester_of(date(year, 1 if half == 1 else 7, 1))


def semester_key(period: Period) -> str:
    """Inverse of parse_semester for a semester period."""
    return f"{period.start.year}-{1 if period.start.month == 1 else 2}"


def semester_label(period: Period) -> str:
    """French label, e.g. "janvier – juin 2027"."""
    return (
        f"{MONTHS_FR[period.start.month - 1]} – {MONTHS_FR[period.end.month - 1]} {period.end.year}"
    )


def parse_month(value: str | None, default: date) -> date:
    """Parse "YYYY-MM" into the first day of that month (fallback: month of `default`)."""
    try:
        if value:
            year_s, month_s = value.split("-", 1)
            parsed = date(int(year_s), int(month_s), 1)
            if 2000 <= parsed.year <= 2100:
                return parsed
    except ValueError:
        pass
    return default.replace(day=1)


def parse_day(value: str | None, default: date) -> date:
    """Parse an ISO date, falling back to `default`."""
    try:
        if value:
            parsed = date.fromisoformat(value)
            if 2000 <= parsed.year <= 2100:
                return parsed
    except ValueError:
        pass
    return default


def add_months(first_of_month: date, months: int) -> date:
    """First day of the month `months` away."""
    index = first_of_month.year * 12 + first_of_month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def months_between(start: date, end: date) -> list[date]:
    """First day of every month intersecting [start, end]."""
    months, current = [], start.replace(day=1)
    while current <= end:
        months.append(current)
        current = add_months(current, 1)
    return months


def month_weeks(first_of_month: date) -> list[list[date | None]]:
    """Monday-first weeks of the month; days outside the month are None."""
    return [
        [d if d.month == first_of_month.month else None for d in week]
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(
            first_of_month.year, first_of_month.month
        )
    ]


def week_days(d: date) -> list[date]:
    """Monday → Sunday of the week containing `d`."""
    monday = d - timedelta(days=d.weekday())
    return [monday + timedelta(days=i) for i in range(7)]


def month_label(first_of_month: date) -> str:
    """e.g. "mai 2027"."""
    return f"{MONTHS_FR[first_of_month.month - 1]} {first_of_month.year}"


def long_date(d: date) -> str:
    """e.g. "lundi 17 mai 2027"."""
    return f"{WEEKDAYS_FR[d.weekday()]} {d.day} {MONTHS_FR[d.month - 1]} {d.year}"


def short_date(d: date) -> str:
    """e.g. "17/05/2027"."""
    return d.strftime("%d/%m/%Y")
