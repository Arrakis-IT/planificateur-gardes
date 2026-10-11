# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Scheduler port: correctness, balance, public holidays, reproducibility."""

import random
from collections import defaultdict
from datetime import date, timedelta

import pytest

from app.services.holidays import holidays_between
from app.services.scheduler import (
    UNASSIGNED,
    Doctor,
    ScheduleInputError,
    ScheduleRequest,
    _Planificador,
    build_fechas,
    compute_stats,
    generate_schedule,
)

SEEDS = range(12)
START, END = date(2027, 1, 1), date(2027, 6, 30)
DOCTORS = [
    Doctor("NR", "Nicolas Rousseau"),
    Doctor("DT", "Dominique Thill"),
    Doctor("MC", "Marie Curie"),
]


def _vacations(blocks: list[tuple[str, date, int]]) -> dict[date, set[str]]:
    vac: dict[date, set[str]] = defaultdict(set)
    for code, first, days in blocks:
        for i in range(days):
            vac[first + timedelta(days=i)].add(code)
    return dict(vac)


def well_formed_request() -> ScheduleRequest:
    """3 doctors, 6 months, 10 vacation days each, 25 on-site days, LU holidays."""
    vacations = _vacations(
        [
            ("NR", date(2027, 2, 15), 5),
            ("NR", date(2027, 4, 12), 5),
            ("DT", date(2027, 3, 1), 5),
            ("DT", date(2027, 5, 24), 5),
            ("MC", date(2027, 1, 18), 5),
            ("MC", date(2027, 6, 7), 5),
        ]
    )
    weekdays = [d for d in build_fechas(START, END) if d.isoweekday() < 6]
    onsite = set(weekdays[3::5][:25])
    return ScheduleRequest(
        period_from=START,
        period_to=END,
        doctors=DOCTORS,
        holidays=vacations,
        onsite=onsite,
        luxembourg_holidays=set(holidays_between(START, END, {})),
    )


@pytest.fixture(scope="module")
def runs():
    """One schedule per seed for the well-formed input (computed once)."""
    request = well_formed_request()
    return request, [generate_schedule(request, random.Random(seed)) for seed in SEEDS]


def _algorithm_counters(request, result):
    """The counters the algorithm itself balances (JS contar* helpers)."""
    p = _Planificador(request, random.Random(0))
    fechas = build_fechas(request.period_from, request.period_to)
    asign = [result[d] for d in fechas]
    return p, fechas, asign


def _spread(values):
    return max(values) - min(values)


# --- Shape and constraints -------------------------------------------------------------


def test_every_day_of_the_period_is_returned(runs):
    request, results = runs
    for result in results:
        assert list(result) == build_fechas(request.period_from, request.period_to)
        assert all(code in {"NR", "DT", "MC", UNASSIGNED} for code in result.values())


def test_no_doctor_assigned_during_vacation(runs):
    request, results = runs
    for result in results:
        conflicts = [d for d, code in result.items() if code in request.holidays.get(d, set())]
        assert conflicts == []


def test_weekend_is_same_doctor_saturday_and_sunday(runs):
    _, results = runs
    for result in results:
        for d, code in result.items():
            if d.isoweekday() == 6 and d + timedelta(days=1) in result:
                assert result[d + timedelta(days=1)] == code, d


def test_no_consecutive_weekends_when_avoidable(runs):
    request, results = runs
    for result in results:
        p, fechas, asign = _algorithm_counters(request, result)
        assert not p.tiene_fines_de_semana_consecutivos(asign, fechas)


def test_consecutive_weekends_only_when_unavoidable():
    """Single available doctor: consecutive weekends cannot be avoided."""
    start, end = date(2027, 1, 2), date(2027, 1, 17)  # Sat … Sun, 3 weekends
    away = {d: {"DT"} for d in build_fechas(start, end) if d.isoweekday() >= 6}
    request = ScheduleRequest(start, end, [Doctor("NR"), Doctor("DT")], away)
    result = generate_schedule(request, random.Random(1))
    assert {result[d] for d in away} == {"NR"}


def test_balance_on_well_formed_input(runs):
    """The algorithm balances weekends (pairs), on-site days and total duties.

    Bounds requested: weekend ≤ 1, on-site ≤ 1, days ≤ 2 (see module notes on the
    "Semaine" statistics column, which the original algorithm does not balance).
    """
    request, results = runs
    for result in results:
        p, fechas, asign = _algorithm_counters(request, result)
        assert _spread(p.contar_fines_de_semana_por_medico(asign, fechas)) <= 1
        assert _spread(p.contar_on_site_por_medico(asign, fechas)) <= 1
        assert _spread(p.contar_dias_por_medico(asign)) <= 2
        stats = compute_stats(request, result)
        assert stats.spread("onsite") <= 1


def test_xx_when_nobody_available():
    start, end = date(2027, 3, 1), date(2027, 3, 14)  # Mon … Sun, 2 weekends
    everybody_away = {date(2027, 3, 3), date(2027, 3, 13), date(2027, 3, 14)}
    request = ScheduleRequest(
        start,
        end,
        [Doctor("NR"), Doctor("DT")],
        holidays={d: {"NR", "DT"} for d in everybody_away},
        onsite={date(2027, 3, 3)},
    )
    result = generate_schedule(request, random.Random(3))
    assert {d for d, code in result.items() if code == UNASSIGNED} == everybody_away
    stats = compute_stats(request, result)
    assert stats.unassigned == 3


def test_first_weekend_split_when_nobody_free_both_days():
    start, end = date(2027, 1, 2), date(2027, 1, 3)  # Sat, Sun
    request = ScheduleRequest(
        start,
        end,
        [Doctor("NR"), Doctor("DT")],
        holidays={date(2027, 1, 2): {"DT"}, date(2027, 1, 3): {"NR"}},
    )
    assert generate_schedule(request, random.Random(0)) == {
        date(2027, 1, 2): "NR",
        date(2027, 1, 3): "DT",
    }


def test_period_starting_on_sunday_and_lone_saturday_at_end():
    start, end = date(2027, 1, 3), date(2027, 1, 9)  # Sun … Sat
    request = ScheduleRequest(start, end, DOCTORS)
    result = generate_schedule(request, random.Random(0))
    assert len(result) == 7 and UNASSIGNED not in result.values()


# --- Luxembourg public holidays ---------------------------------------------------------


def _pentecost_request(**overrides) -> ScheduleRequest:
    # Whit Monday 2027-05-17 follows the weekend of 15–16 May.
    start, end = date(2027, 5, 1), date(2027, 5, 31)
    base = {
        "period_from": start,
        "period_to": end,
        "doctors": DOCTORS,
        "luxembourg_holidays": set(holidays_between(start, end, {})),
    }
    return ScheduleRequest(**(base | overrides))


@pytest.mark.parametrize("seed", SEEDS)
def test_weekday_holiday_not_grouped_with_adjacent_weekend(seed):
    request = _pentecost_request()
    result = generate_schedule(request, random.Random(seed))
    sunday, whit_monday = date(2027, 5, 16), date(2027, 5, 17)
    assert result[whit_monday] != result[sunday]
    # Ascension (Thu 6 May) is not glued to Friday/Saturday either.
    assert result[date(2027, 5, 6)] != result[date(2027, 5, 7)]


def test_weekday_holiday_left_to_daily_phases():
    """After the weekend phases, a weekday holiday is still unassigned (not grouped)."""
    request = _pentecost_request()
    p = _Planificador(request, random.Random(0))
    fechas = build_fechas(request.period_from, request.period_to)
    asign = [""] * len(fechas)
    p.asignar_primer_fin_de_semana(asign, fechas)
    p.asignar_fines_de_semana_optimo(asign, fechas)
    by_date = dict(zip(fechas, asign, strict=True))
    assert by_date[date(2027, 5, 16)] != ""  # Sunday assigned with its Saturday
    assert by_date[date(2027, 5, 17)] == ""  # Whit Monday waits for phase 4


@pytest.mark.parametrize("seed", SEEDS)
def test_consecutive_holidays_assigned_independently(seed):
    # 2030: Christmas (Wed 25) and Saint-Étienne (Thu 26) are two consecutive weekday holidays.
    start, end = date(2030, 12, 20), date(2030, 12, 31)
    request = ScheduleRequest(
        start, end, DOCTORS, luxembourg_holidays=set(holidays_between(start, end, {}))
    )
    result = generate_schedule(request, random.Random(seed))
    assert result[date(2030, 12, 25)] != result[date(2030, 12, 26)]


def test_weekday_holiday_counts_as_weekend_in_stats():
    request = _pentecost_request()
    result = generate_schedule(request, random.Random(5))
    stats = compute_stats(request, result)
    # May 2027: 10 weekend days + weekday holidays Ascension (Thu 6) and Whit Monday (Mon 17).
    # May 1 (Sat) and May 9 (Sun) are holidays on weekends: no change.
    assert stats.total_weekend == 12
    assert stats.total_week == 31 - 12
    whit_monday_doctor = result[date(2027, 5, 17)]
    without_holidays = compute_stats(
        ScheduleRequest(request.period_from, request.period_to, DOCTORS), result
    )
    with_h = {s.code: s for s in stats.doctors}
    without = {s.code: s for s in without_holidays.doctors}
    assert with_h[whit_monday_doctor].weekend > without[whit_monday_doctor].weekend
    assert sum(s.weekend for s in stats.doctors) == 12
    assert sum(s.week for s in stats.doctors) == 19


def test_stats_respect_effective_onsite_and_vacation_counts():
    request = well_formed_request()
    result = generate_schedule(request, random.Random(0))
    stats = compute_stats(request, result)
    assert stats.total_onsite == 25
    assert {s.code: s.vacation_days for s in stats.doctors} == {"NR": 10, "DT": 10, "MC": 10}
    removed_one = set(sorted(request.onsite)[1:])
    assert compute_stats(request, result, onsite=removed_one).total_onsite == 24


# --- Reproducibility and validation -----------------------------------------------------


def test_same_seed_same_output():
    request = well_formed_request()
    first = generate_schedule(request, random.Random(42))
    second = generate_schedule(request, random.Random(42))
    assert first == second
    assert generate_schedule(request, random.Random(43)) != first


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"doctors": []}, "Au moins un médecin"),
        ({"period_from": date(2027, 2, 1), "period_to": date(2027, 1, 1)}, "précéder"),
        ({"doctors": [Doctor("NR"), Doctor("NR")]}, "uniques"),
        ({"doctors": [Doctor("XX")]}, "invalide"),
    ],
)
def test_invalid_requests(overrides, message):
    base = {"period_from": START, "period_to": END, "doctors": DOCTORS}
    with pytest.raises(ScheduleInputError, match=message):
        generate_schedule(ScheduleRequest(**(base | overrides)))


def test_single_day_period():
    request = ScheduleRequest(date(2027, 1, 4), date(2027, 1, 4), DOCTORS)
    assert len(generate_schedule(request, random.Random(0))) == 1


# --- Dev-only preview endpoint ------------------------------------------------------------


@pytest.fixture
def dev_app(monkeypatch):
    from app import main
    from app.config import get_settings

    settings = get_settings().model_copy(update={"app_env": "development"})
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    return main.create_app()


async def test_dev_preview_endpoint(dev_app):
    from httpx import ASGITransport, AsyncClient

    payload = {
        "period_from": "2027-05-01",
        "period_to": "2027-05-31",
        "doctors": [{"code": "NR"}, {"code": "DT"}, {"code": "MC"}],
        "holidays": {"2027-05-10": ["NR"]},
        "onsite": ["2027-05-12"],
        "seed": 7,
    }
    async with AsyncClient(transport=ASGITransport(app=dev_app), base_url="http://t") as client:
        first = await client.post("/_dev/schedule-preview", json=payload)
        second = await client.post("/_dev/schedule-preview", json=payload)
        invalid = await client.post(
            "/_dev/schedule-preview", json=payload | {"doctors": [{"code": "XX"}]}
        )
        too_long = await client.post(
            "/_dev/schedule-preview", json=payload | {"period_to": "2029-01-01"}
        )
    assert first.status_code == 200
    body = first.json()
    assert body["assignments"] == second.json()["assignments"]
    assert len(body["assignments"]) == 31 and body["seed"] == 7
    assert body["public_holidays"]["2027-05-17"] == "Lundi de Pentecôte"
    assert body["totals"]["weekend"] == 12
    assert body["assignments"]["2027-05-10"] != "NR"
    assert invalid.status_code == 422 and too_long.status_code == 422


async def test_dev_preview_absent_outside_development(client):
    response = await client.post("/_dev/schedule-preview", json={})
    assert response.status_code == 404
