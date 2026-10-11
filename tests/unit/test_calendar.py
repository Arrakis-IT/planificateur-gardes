# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Calendar helpers used by the views."""

from datetime import UTC, date, datetime

import pytest

from app.services import calendar as cal


@pytest.mark.parametrize(
    ("today", "start", "end"),
    [
        (date(2027, 1, 1), date(2027, 7, 1), date(2027, 12, 31)),
        (date(2027, 6, 30), date(2027, 7, 1), date(2027, 12, 31)),
        (date(2027, 7, 1), date(2028, 1, 1), date(2028, 6, 30)),
        (date(2027, 12, 31), date(2028, 1, 1), date(2028, 6, 30)),
    ],
)
def test_next_semester_default_period(today, start, end):
    assert cal.next_semester(today) == cal.Period(start, end)


def test_semester_keys_roundtrip_and_navigation():
    period = cal.parse_semester("2027-2")
    assert period == cal.Period(date(2027, 7, 1), date(2027, 12, 31))
    assert cal.semester_key(period) == "2027-2"
    assert cal.semester_key(cal.previous_semester(period)) == "2027-1"
    assert cal.semester_label(period) == "juillet – décembre 2027"
    for bad in (None, "", "2027", "2027-3", "abcd-1", "1900-1"):
        assert cal.parse_semester(bad) is None


def test_parse_month_and_day_fallbacks():
    default = date(2027, 5, 17)
    assert cal.parse_month("2027-02", default) == date(2027, 2, 1)
    assert cal.parse_month("2027-13", default) == date(2027, 5, 1)
    assert cal.parse_month(None, default) == date(2027, 5, 1)
    assert cal.parse_day("2027-02-03", default) == date(2027, 2, 3)
    assert cal.parse_day("garbage", default) == default


def test_month_weeks_monday_first():
    weeks = cal.month_weeks(date(2027, 5, 1))  # 1 May 2027 is a Saturday
    assert weeks[0][:5] == [None] * 5 and weeks[0][5] == date(2027, 5, 1)
    assert all(len(w) == 7 for w in weeks)
    assert weeks[-1][0] == date(2027, 5, 31)


def test_week_days_and_months_between():
    days = cal.week_days(date(2027, 5, 19))  # Wednesday
    assert days[0] == date(2027, 5, 17) and days[-1] == date(2027, 5, 23)
    assert cal.months_between(date(2026, 11, 15), date(2027, 2, 1)) == [
        date(2026, 11, 1),
        date(2026, 12, 1),
        date(2027, 1, 1),
        date(2027, 2, 1),
    ]
    assert cal.add_months(date(2027, 12, 1), 1) == date(2028, 1, 1)
    assert cal.add_months(date(2027, 1, 1), -1) == date(2026, 12, 1)


def test_french_labels():
    assert cal.long_date(date(2027, 5, 17)) == "lundi 17 mai 2027"
    assert cal.month_label(date(2027, 8, 1)) == "août 2027"
    assert cal.short_date(date(2027, 5, 7)) == "07/05/2027"


def test_today_is_luxembourg_local_date():
    # 23:30 UTC on 31 Dec is already 1 Jan in Luxembourg (UTC+1 in winter).
    assert cal.today_local(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == date(2027, 1, 1)


def test_period_contains():
    period = cal.Period(date(2027, 1, 1), date(2027, 1, 31))
    assert date(2027, 1, 31) in period and date(2027, 2, 1) not in period
    assert "2027-01-05" not in period
    assert period.days == 31
