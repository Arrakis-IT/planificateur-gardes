# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Luxembourg public holidays."""

from datetime import date

import pytest
from dateutil.easter import easter

from app.services.holidays import effective_holidays, holidays_between, lu_public_holidays

# (Easter Sunday, Easter Monday, Ascension, Whit Monday) — checked against published calendars.
MOVABLE = {
    2026: (date(2026, 4, 5), date(2026, 4, 6), date(2026, 5, 14), date(2026, 5, 25)),
    2027: (date(2027, 3, 28), date(2027, 3, 29), date(2027, 5, 6), date(2027, 5, 17)),
    2030: (date(2030, 4, 21), date(2030, 4, 22), date(2030, 5, 30), date(2030, 6, 10)),
}

FIXED = [
    ((1, 1), "Jour de l'An"),
    ((5, 1), "Fête du Travail"),
    ((5, 9), "Journée de l'Europe"),
    ((6, 23), "Fête Nationale"),
    ((8, 15), "Assomption"),
    ((11, 1), "Toussaint"),
    ((12, 25), "Noël"),
    ((12, 26), "Saint-Étienne"),
]


@pytest.mark.parametrize("year", sorted(MOVABLE))
def test_eleven_holidays_with_fixed_dates(year):
    holidays = lu_public_holidays(year)
    assert len(holidays) == 11
    for (month, day), label in FIXED:
        assert holidays[date(year, month, day)] == label


@pytest.mark.parametrize("year", sorted(MOVABLE))
def test_easter_and_derived_feasts(year):
    easter_sunday, easter_monday, ascension, whit_monday = MOVABLE[year]
    assert easter(year) == easter_sunday
    holidays = lu_public_holidays(year)
    assert holidays[easter_monday] == "Lundi de Pâques"
    assert holidays[ascension] == "Ascension"
    assert holidays[whit_monday] == "Lundi de Pentecôte"
    # Weekday sanity: Mondays and a Thursday.
    assert easter_monday.isoweekday() == 1
    assert whit_monday.isoweekday() == 1
    assert ascension.isoweekday() == 4
    # Easter Sunday itself is not a public holiday in Luxembourg.
    assert easter_sunday not in holidays


def test_effective_holidays_merges_custom_of_same_year_only():
    custom = {
        date(2027, 2, 15): "Fermeture du cabinet",
        date(2028, 2, 15): "Autre année",
        date(2027, 12, 25): "Doublon",
    }
    merged = effective_holidays(2027, custom)
    assert merged[date(2027, 2, 15)] == "Fermeture du cabinet"
    assert date(2028, 2, 15) not in merged
    assert merged[date(2027, 12, 25)] == "Noël"  # official label wins
    assert len(merged) == 12
    assert list(merged) == sorted(merged)


def test_holidays_between_spans_years():
    result = holidays_between(date(2026, 12, 20), date(2027, 1, 5), {})
    assert set(result) == {date(2026, 12, 25), date(2026, 12, 26), date(2027, 1, 1)}
