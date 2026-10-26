# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Default generation of the results page, schedule of the nightly archiving job."""

from datetime import date, datetime

from app.main import _seconds_until_next_run
from app.models import Generation
from app.services.calendar import CLINIC_TZ
from app.services.planning import default_generation


def _generation(start: date, end: date) -> Generation:
    return Generation(period_from=start, period_to=end)


def test_default_generation_prefers_today_then_next_then_latest():
    autumn = _generation(date(2026, 10, 1), date(2026, 12, 31))
    spring = _generation(date(2027, 1, 1), date(2027, 6, 30))
    summer = _generation(date(2027, 7, 1), date(2027, 9, 30))
    generations = [autumn, spring, summer]
    assert default_generation(generations, date(2026, 11, 5)) is autumn
    assert default_generation([spring, summer], date(2026, 11, 5)) is spring
    assert default_generation(generations, date(2028, 1, 1)) is summer
    assert default_generation([], date(2026, 11, 5)) is None


def test_archiving_job_runs_just_after_midnight_luxembourg():
    evening = datetime(2026, 10, 26, 23, 0, tzinfo=CLINIC_TZ)
    assert _seconds_until_next_run(evening) == 65 * 60
    just_after = datetime(2026, 10, 27, 0, 5, tzinfo=CLINIC_TZ)
    assert _seconds_until_next_run(just_after) == 24 * 3600
