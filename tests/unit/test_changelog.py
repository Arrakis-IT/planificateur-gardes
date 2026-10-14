# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Changelog between two generations (pure functions)."""

from datetime import date

import pytest

from app.services.calendar import Period, day_month_year
from app.services.changelog import ChangeEntry, DayState, build_changelog, describe_change

A, B = "Dr. Jean Dupont", "Dr. Marie Richard"


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        # Rows of the confirmed format table.
        (DayState(A, True), DayState(B, True), (f"{A} (sur place) → {B}", True)),
        (DayState(A, False), DayState(B, False), (f"{A} → {B}", False)),
        (DayState(A, False), DayState(B, True), (f"{A} → {B} (sur place)", True)),
        (DayState(A, True), DayState(B, False), (f"{A} (sur place) → {B}", True)),
        (DayState(A, False), DayState(A, True), (f"{A} (devenu sur place)", True)),
        (DayState(A, True), DayState(A, False), (f"{A} (n'est plus sur place)", True)),
        (DayState(A, True), DayState(A, True), None),
        (DayState(A, False), DayState(A, False), None),
        # Unassigned on one side.
        (DayState(None, False), DayState(A, False), (f"non assigné → {A}", False)),
        (DayState(A, False), DayState(None, False), (f"{A} → non assigné", False)),
        (DayState(None, False), DayState(A, True), (f"non assigné → {A} (sur place)", True)),
        (DayState(A, True), DayState(None, True), (f"{A} (sur place) → non assigné", True)),
        (DayState(None, False), DayState(None, True), ("non assigné (devenu sur place)", True)),
        (DayState(None, False), DayState(None, False), None),
    ],
)
def test_describe_change(before, after, expected):
    assert describe_change(before, after) == expected


def test_only_shared_days_are_compared_in_date_order():
    previous = Period(date(2027, 4, 1), date(2027, 4, 10))
    current = Period(date(2027, 4, 5), date(2027, 4, 20))
    before = {date(2027, 4, d): DayState(A, False) for d in range(1, 11)}
    after = {date(2027, 4, d): DayState(B, False) for d in range(5, 21)}
    changelog = build_changelog(previous, before, current, after)
    assert changelog is not None
    assert changelog.previous == previous and changelog.current == current
    assert [e.day for e in changelog.entries] == [date(2027, 4, d) for d in range(5, 11)]


def test_no_overlap_gives_no_changelog():
    previous = Period(date(2027, 4, 1), date(2027, 4, 30))
    current = Period(date(2027, 5, 1), date(2027, 5, 31))
    assert build_changelog(previous, {}, current, {}) is None


def test_overlap_without_changes_gives_empty_entries():
    period = Period(date(2027, 4, 1), date(2027, 4, 3))
    states = {date(2027, 4, d): DayState(A, d == 2) for d in range(1, 4)}
    changelog = build_changelog(period, states, period, dict(states))
    assert changelog is not None and changelog.entries == []


def test_single_shared_day_and_missing_cells_count_as_unassigned():
    previous = Period(date(2027, 4, 1), date(2027, 4, 5))
    current = Period(date(2027, 4, 5), date(2027, 4, 9))
    changelog = build_changelog(previous, {}, current, {date(2027, 4, 5): DayState(A, False)})
    assert changelog.entries == [ChangeEntry(date(2027, 4, 5), f"non assigné → {A}", False)]


def test_entry_label_uses_french_date():
    assert ChangeEntry(date(2026, 4, 4), "x", False).label == "4 avril 2026 : x"
    assert day_month_year(date(2027, 5, 1)) == "1er mai 2027"
