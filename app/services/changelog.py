# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Day-by-day changes between the previous and the new generation (schedule email).

Only the days held by both sides are compared: the previous side is the archived
generation(s) holding the assignments a new generation recalculated (days transferred as
they are, or kept in a locked month, are not compared). Days that left the period, new days,
days of locked months and vacations are out of scope (the attached PDF shows the full new
schedule).
`build_changelog` is pure; `load_changelog` reads both generations from the database.

Entry format (`describe_change`), "(sur place)" flags and red highlight (any change that
touches an on-site day):

=================  ===============  ======================================  =========
doctor             on-site          entry                                   highlight
=================  ===============  ======================================  =========
A → B              yes → yes        A (sur place) → B                       yes
A → B              no → no          A → B                                   no
A → B              no → yes         A → B (sur place)                       yes
A → B              yes → no         A (sur place) → B                       yes
A → A              no → yes         A (devenu sur place)                    yes
A → A              yes → no         A (n'est plus sur place)                yes
A → A              unchanged        (no entry)
=================  ===============  ======================================  =========

An unassigned day reads "non assigné" in place of a doctor name.
"""

import uuid
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Assignment, Generation
from app.repositories import scheduling as repo
from app.services.calendar import Period, day_month_year
from app.services.locks import locked_months

UNASSIGNED_LABEL = "non assigné"
ONSITE_SUFFIX = " (sur place)"


@dataclass(frozen=True)
class DayState:
    """One day of a generation: doctor's full name (None = unassigned) and on-site flag."""

    doctor: str | None
    onsite: bool


@dataclass(frozen=True)
class ChangeEntry:
    """One line of the changelog; `highlight` marks an on-site status change (red)."""

    day: date
    text: str
    highlight: bool

    @property
    def label(self) -> str:
        """e.g. "4 avril 2027 : Dupont → Richard"."""
        return f"{day_month_year(self.day)} : {self.text}"


@dataclass(frozen=True)
class Changelog:
    """Changes over the days shared by both generations (empty list: none)."""

    previous: Period
    current: Period
    entries: list[ChangeEntry]


def describe_change(before: DayState, after: DayState) -> tuple[str, bool] | None:
    """Entry text and highlight flag for one day, or None when nothing changed."""
    old_name = before.doctor or UNASSIGNED_LABEL
    new_name = after.doctor or UNASSIGNED_LABEL
    if before.doctor == after.doctor:
        if before.onsite == after.onsite:
            return None
        status = "devenu sur place" if after.onsite else "n'est plus sur place"
        return f"{new_name} ({status})", True
    left = old_name + (ONSITE_SUFFIX if before.onsite else "")
    right = new_name + (ONSITE_SUFFIX if after.onsite and not before.onsite else "")
    return f"{left} → {right}", before.onsite or after.onsite


def build_changelog(
    previous: Period,
    before: Mapping[date, DayState],
    current: Period,
    after: Mapping[date, DayState],
    locked: Collection[date] = (),
    days: Collection[date] | None = None,
) -> Changelog | None:
    """Changes on the overlap of both periods, by date; None when they do not overlap.

    Days of `locked` months (first days) are left out; `days`, if given, restricts the
    comparison to those days.
    """
    start, end = max(previous.start, current.start), min(previous.end, current.end)
    if start > end:
        return None
    unassigned = DayState(None, False)
    entries: list[ChangeEntry] = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        if day.replace(day=1) in locked or (days is not None and day not in days):
            continue
        change = describe_change(before.get(day, unassigned), after.get(day, unassigned))
        if change is not None:
            entries.append(ChangeEntry(day, *change))
    return Changelog(previous, current, entries)


def _states(
    assignments: Mapping[date, Assignment], names: Mapping[uuid.UUID, str], onsite: set[date]
) -> dict[date, DayState]:
    """Day states of a generation; cells without snapshot use the current settings."""
    return {
        day: DayState(
            names.get(a.user_id) if a.user_id else None,
            a.onsite if a.onsite is not None else day in onsite and not a.onsite_removed,
        )
        for day, a in assignments.items()
    }


async def load_changelog(
    db: AsyncSession,
    clinic_id: uuid.UUID,
    previous_ids: Sequence[uuid.UUID],
    generation: Generation,
) -> Changelog | None:
    """Changelog between the archived generations `previous_ids` and `generation`, over the
    days both hold."""
    loaded = [await db.get(Generation, pid) for pid in previous_ids]
    previous = [g for g in loaded if g is not None and g.clinic_id == clinic_id]
    if not previous:
        return None
    before_rows: dict[date, Assignment] = {}
    for old in previous:
        before_rows |= await repo.generation_assignments(db, clinic_id, old.id)
    after_rows = await repo.generation_assignments(db, clinic_id, generation.id)
    shared = set(before_rows) & set(after_rows)
    if not shared:
        return None
    previous_period = Period(min(before_rows), max(before_rows))
    current_period = Period(generation.period_from, generation.period_to)
    start = min(previous_period.start, current_period.start)
    end = max(previous_period.end, current_period.end)
    names = {u.id: u.full_name for u in (await repo.users_by_id(db, clinic_id)).values()}
    onsite = await repo.list_onsite_days(db, clinic_id, start, end)
    return build_changelog(
        previous_period,
        _states(before_rows, names, onsite),
        current_period,
        _states(after_rows, names, onsite),
        await locked_months(db, clinic_id, start, end),
        shared,
    )
