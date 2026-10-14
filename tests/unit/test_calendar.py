# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Calendar helpers used by the views."""

from datetime import UTC, date, datetime

import pytest

from app.services import calendar as cal


@pytest.mark.parametrize(
    ("today", "end"),
    [
        (date(2026, 10, 14), date(2027, 4, 30)),
        (date(2026, 10, 31), date(2027, 4, 30)),
        (date(2027, 1, 1), date(2027, 7, 31)),
        (date(2027, 8, 31), date(2028, 2, 29)),  # leap year
        (date(2027, 12, 1), date(2028, 6, 30)),
    ],
)
def test_default_generation_period(today, end):
    assert cal.default_generation_period(today) == cal.Period(today, end)


def test_month_window_key_and_label():
    window = cal.month_window(date(2026, 10, 1), 8)
    assert window == cal.Period(date(2026, 10, 1), date(2027, 5, 31))
    assert len(cal.months_between(window.start, window.end)) == 8
    assert cal.period_label(window) == "octobre 2026 – mai 2027"
    assert cal.period_label(cal.month_window(date(2027, 3, 1), 8)) == "mars – octobre 2027"
    assert cal.month_key(date(2027, 5, 1)) == "2027-05"
    assert cal.parse_month(cal.month_key(date(2027, 5, 1)), date(2000, 1, 1)) == date(2027, 5, 1)


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


def test_local_datetime_uses_luxembourg_time():
    assert cal.local_datetime(datetime(2026, 10, 11, 18, 30, tzinfo=UTC)) == "11/10/2026 à 20:30"


def test_today_is_luxembourg_local_date():
    # 23:30 UTC on 31 Dec is already 1 Jan in Luxembourg (UTC+1 in winter).
    assert cal.today_local(datetime(2026, 12, 31, 23, 30, tzinfo=UTC)) == date(2027, 1, 1)


def test_period_contains():
    period = cal.Period(date(2027, 1, 1), date(2027, 1, 31))
    assert date(2027, 1, 31) in period and date(2027, 2, 1) not in period
    assert "2027-01-05" not in period
    assert period.days == 31
