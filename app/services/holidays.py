# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Luxembourg public holidays (computed, never stored) and per-clinic custom holidays."""

from collections.abc import Mapping
from datetime import date, timedelta

from dateutil.easter import easter


def lu_public_holidays(year: int) -> dict[date, str]:
    """The 11 official Luxembourg public holidays of `year` (French labels).

    Movable feasts derive from Easter Sunday (Western/Gregorian computus):
    Easter Monday (+1), Ascension (+39), Whit Monday (+50).
    """
    easter_sunday = easter(year)
    return {
        date(year, 1, 1): "Jour de l'An",
        easter_sunday + timedelta(days=1): "Lundi de Pâques",
        date(year, 5, 1): "Fête du Travail",
        date(year, 5, 9): "Journée de l'Europe",
        easter_sunday + timedelta(days=39): "Ascension",
        easter_sunday + timedelta(days=50): "Lundi de Pentecôte",
        date(year, 6, 23): "Fête Nationale",
        date(year, 8, 15): "Assomption",
        date(year, 11, 1): "Toussaint",
        date(year, 12, 25): "Noël",
        date(year, 12, 26): "Saint-Étienne",
    }


def effective_holidays(year: int, custom: Mapping[date, str]) -> dict[date, str]:
    """Official holidays of `year` merged with the clinic's custom ones of that year.

    A custom holiday on an official date keeps the official label.
    """
    merged = {d: label for d, label in custom.items() if d.year == year}
    merged.update(lu_public_holidays(year))
    return dict(sorted(merged.items()))


def holidays_between(start: date, end: date, custom: Mapping[date, str]) -> dict[date, str]:
    """Effective holidays (official + custom) within [start, end], inclusive."""
    result: dict[date, str] = {}
    for year in range(start.year, end.year + 1):
        result.update(
            {d: label for d, label in effective_holidays(year, custom).items() if start <= d <= end}
        )
    return result
