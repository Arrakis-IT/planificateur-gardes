# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""On-call duty scheduler — literal port of the standalone app's algorithm.

Origin
------
The algorithm was first written in VBA (Excel), then ported to JavaScript in the
standalone page (`Turn-o-Magic/index.html`, section "CORE ALGORITHM (ported from VBA)",
served today at gardes.arrakis.lu/gratis). This module ports that JavaScript **literally**:
same phases, same heuristics, same tie-breaking, same 100 attempts in the "Optimo" steps,
same order of random draws. Function names are kept in Spanish, identical to the JS, so
both files can be read side by side:

    JS                                   Python (methods of `_Planificador`)
    asignarPrimerFinDeSemana             asignar_primer_fin_de_semana
    asignarFinesDeSemana(Optimo)         asignar_fines_de_semana(_optimo)
    asignarGuardiasOnSiteConCola/Optimo  asignar_guardias_on_site_con_cola / _optimo
    asignarRestoDeDiasConCola/Optimo     asignar_resto_de_dias_con_cola / _optimo
    contarOnSitePorMedico, contarDiasPorMedico, contarFinesDeSemanaPorMedico,
    tieneFinesDeSemanaConsecutivos, calcularPenalizacionContinuidad,
    medicoAsignadoDiaAnteriorOPosterior, obtenerMedicoDisponibleAlAzar,
    inicializarColaPorCargaOnSite, inicializarColaPorCargaTotal, ... (snake_case)

Do not optimise or "improve" this code: identical inputs must give a statistically
equivalent output distribution to the JavaScript version.

Working representation
----------------------
`fechas` is the ordered list of every date of the period; `asign` is a parallel list of
doctor codes where "" means "not assigned yet" and "XX" means "nobody available".

The four phases
---------------
1. **First weekend** (`asignar_primer_fin_de_semana`). The first Saturday of the period
   (with its Sunday if inside the period) gets a random doctor available on both days;
   if nobody is free both days, each day gets its own random available doctor (or XX).

2. **Other weekends** (`asignar_fines_de_semana_optimo`). Every remaining Saturday+Sunday
   pair goes to ONE doctor, drawn at random among those available both days, excluding the
   doctor of the previous weekend when possible. The whole pass is run 100 times; the kept
   run is the best by (a) no doctor on two consecutive weekends, then (b) smallest max–min
   spread of weekends per doctor.

3. **On-site days** (`asignar_guardias_on_site_optimo`). Days the clinic marked "on-site"
   and still free are filled from a queue ordered by on-site load (ties shuffled). For each
   day the least-loaded doctor wins, first among doctors not on duty the day before/after
   (strict pass), else among all available ones (flexible pass); the winner moves to the
   back of the queue. 100 runs, keeping the smallest on-site spread.

4. **Remaining days** (`asignar_resto_de_dias_optimo`). Same queue heuristic for every
   remaining day, balancing the total number of duties per doctor. 100 runs, keeping the
   smallest total spread, then the smallest continuity penalty (runs of ≥3 consecutive
   days by the same doctor).

Balance heuristic: each phase balances its own counter (weekends, on-site days, total
days) via "least loaded first" plus best-of-100 random restarts.

Design decisions
----------------
* **Balance.** The algorithm balances three metrics: weekend pairs, on-site days and the
  total number of duty days. The derived statistics columns "Semaine" (weekdays) and
  "Week-end" (including weekday public holidays) are not balanced explicitly: they are an
  arithmetic consequence of the main distribution, and adding constraints on them would
  break the balance of the primary metrics. This matches the behaviour of the original
  Excel/VBA/JS versions.

Luxembourg public holidays
--------------------------
The JavaScript has no notion of public holidays; the port keeps it that way, which already
gives the behaviour required by the brief (§5.2):

* A holiday on a weekday is never grouped with the adjacent weekend nor with another
  holiday: only Saturday+Sunday pairs are grouped (phases 1–2); weekday holidays are filled
  one by one in phase 3/4, where the strict pass avoids the doctor of the previous/next day.
* A holiday on Saturday or Sunday changes nothing.
* In statistics (`compute_stats`), a weekday holiday counts as a Sunday: it goes into the
  "week-end" bucket instead of "semaine".
"""

import random
from collections.abc import Container, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta

UNASSIGNED = "XX"
ATTEMPTS = 100
_SATURDAY = 6  # VBA Weekday(date, vbMonday): 1=Mon … 6=Sat, 7=Sun
_BIG = 9999


class ScheduleInputError(ValueError):
    """The scheduling request is not well formed."""


@dataclass(frozen=True)
class Doctor:
    """A doctor taking part in the schedule."""

    code: str
    name: str = ""


@dataclass(frozen=True)
class ScheduleRequest:
    """Everything the algorithm needs.

    `holidays` are the doctors' vacations: date → codes of doctors away that day.
    `luxembourg_holidays` are public holidays (official + clinic custom); they only
    affect statistics (see module docstring).
    """

    period_from: date
    period_to: date
    doctors: list[Doctor]
    holidays: Mapping[date, set[str]] = field(default_factory=dict)
    onsite: set[date] = field(default_factory=set)
    luxembourg_holidays: set[date] = field(default_factory=set)

    def validate(self) -> None:
        """Raise ScheduleInputError if the request cannot be scheduled."""
        if not self.doctors:
            raise ScheduleInputError("Au moins un médecin est nécessaire.")
        if self.period_from > self.period_to:
            raise ScheduleInputError("La date de début doit précéder la date de fin.")
        codes = [d.code for d in self.doctors]
        if len(set(codes)) != len(codes):
            raise ScheduleInputError("Les codes des médecins doivent être uniques.")
        if any(not c or c == UNASSIGNED for c in codes):
            raise ScheduleInputError(f"Code médecin invalide (vide ou « {UNASSIGNED} »).")


def weekday_mon_first(d: date) -> int:
    """VBA `Weekday(d, vbMonday)`: 1=Monday … 7=Sunday (JS `weekdayMonFirst`)."""
    return d.isoweekday()


def is_weekend(d: date) -> bool:
    """Saturday or Sunday."""
    return weekday_mon_first(d) >= _SATURDAY


def build_fechas(period_from: date, period_to: date) -> list[date]:
    """Every date of the period, inclusive (JS `buildFechas`)."""
    return [period_from + timedelta(days=i) for i in range((period_to - period_from).days + 1)]


def _max_min_diff(values: list[int]) -> int:
    return max(values) - min(values) if values else 0


class _Planificador:
    """Holds the JS `state` (doctors, vacations, on-site) and the ported functions."""

    def __init__(self, request: ScheduleRequest, rng: random.Random) -> None:
        self.doctors = [d.code for d in request.doctors]
        self.vacaciones = {d: frozenset(codes) for d, codes in request.holidays.items()}
        self.onsite = frozenset(request.onsite)
        self.rng = rng

    # --- Helpers (JS: medicoEstaDeVacaciones … calcularPenalizacionContinuidad) ---

    def rand_int(self, n: int) -> int:
        """JS `randInt`: Math.floor(Math.random() * n)."""
        return int(self.rng.random() * n)

    def es_on_site(self, d: date) -> bool:
        return d in self.onsite

    def medico_esta_de_vacaciones(self, code: str, d: date) -> bool:
        return code in self.vacaciones.get(d, ())

    def obtener_medico_disponible_al_azar(self, d: date) -> str:
        available = [c for c in self.doctors if not self.medico_esta_de_vacaciones(c, d)]
        if not available:
            return ""
        return available[self.rand_int(len(available))]

    def buscar_indice_medico(self, code: str) -> int:
        try:
            return self.doctors.index(code)
        except ValueError:
            return -1

    @staticmethod
    def medico_asignado_dia_anterior_o_posterior(asign: list[str], idx: int, code: str) -> bool:
        if idx > 0 and asign[idx - 1] == code:
            return True
        return idx < len(asign) - 1 and asign[idx + 1] == code

    def contar_on_site_por_medico(self, asign: list[str], fechas: list[date]) -> list[int]:
        c = [0] * len(self.doctors)
        for i in range(len(fechas)):
            if self.es_on_site(fechas[i]) and asign[i] and asign[i] != UNASSIGNED:
                idx = self.buscar_indice_medico(asign[i])
                if idx >= 0:
                    c[idx] += 1
        return c

    def contar_dias_por_medico(self, asign: list[str]) -> list[int]:
        c = [0] * len(self.doctors)
        for a in asign:
            if a and a != UNASSIGNED:
                idx = self.buscar_indice_medico(a)
                if idx >= 0:
                    c[idx] += 1
        return c

    @staticmethod
    def _es_inicio_fin_de_semana(fechas: list[date], i: int) -> bool:
        """Saturday whose next entry is the following Sunday."""
        d = fechas[i]
        return weekday_mon_first(d) == _SATURDAY and fechas[i + 1] == d + timedelta(days=1)

    def contar_fines_de_semana_por_medico(self, asign: list[str], fechas: list[date]) -> list[int]:
        c = [0] * len(self.doctors)
        i = 0
        while i < len(fechas) - 1:
            if self._es_inicio_fin_de_semana(fechas, i) and (
                asign[i] == asign[i + 1] and asign[i] and asign[i] != UNASSIGNED
            ):
                idx = self.buscar_indice_medico(asign[i])
                if idx >= 0:
                    c[idx] += 1
                i += 1  # skip Sunday
            i += 1
        return c

    def tiene_fines_de_semana_consecutivos(self, asign: list[str], fechas: list[date]) -> bool:
        prev = ""
        i = 0
        while i < len(fechas) - 1:
            if self._es_inicio_fin_de_semana(fechas, i):
                cur = asign[i]
                if cur and cur != UNASSIGNED:
                    if cur == prev:
                        return True
                    prev = cur
                else:
                    prev = ""
                i += 1  # skip Sunday
            i += 1
        return False

    @staticmethod
    def calcular_penalizacion_continuidad(asign: list[str]) -> int:
        pen, cons = 0, 1
        for i in range(1, len(asign)):
            if asign[i] and asign[i] == asign[i - 1]:
                cons += 1
            else:
                if cons >= 3:
                    pen += cons - 2
                cons = 1
        if cons >= 3:
            pen += cons - 2
        return pen

    # --- Phase 1: first weekend ----------------------------------------------------

    def asignar_primer_fin_de_semana(self, asign: list[str], fechas: list[date]) -> None:
        sab_idx = dom_idx = -1
        for i in range(len(fechas)):
            if weekday_mon_first(fechas[i]) == _SATURDAY:
                sab_idx = i
                if i < len(fechas) - 1 and fechas[i + 1] == fechas[i] + timedelta(days=1):
                    dom_idx = i + 1
                break
        if sab_idx == -1:
            return
        if dom_idx == -1:
            asign[sab_idx] = self.obtener_medico_disponible_al_azar(fechas[sab_idx]) or UNASSIGNED
            return

        tried: set[str] = set()
        attempts = 0
        while attempts < len(self.doctors):
            med = self.obtener_medico_disponible_al_azar(fechas[sab_idx])
            if not med:
                asign[sab_idx] = UNASSIGNED
                asign[dom_idx] = UNASSIGNED
                return
            if med in tried:
                attempts += 1
                continue
            tried.add(med)
            attempts += 1
            if not self.medico_esta_de_vacaciones(med, fechas[dom_idx]):
                asign[sab_idx] = med
                asign[dom_idx] = med
                return
        # No doctor available for both days
        sab_med = self.obtener_medico_disponible_al_azar(fechas[sab_idx])
        asign[sab_idx] = sab_med or UNASSIGNED
        dom_med = self.obtener_medico_disponible_al_azar(fechas[dom_idx])
        asign[dom_idx] = dom_med or UNASSIGNED

    # --- Phase 2: weekends ------------------------------------------------------------

    def asignar_fines_de_semana(self, asign: list[str], fechas: list[date]) -> None:
        historico: list[str] = []
        i = 0
        while i < len(fechas) - 1:
            if self._es_inicio_fin_de_semana(fechas, i):
                sab_idx, dom_idx = i, i + 1

                # Skip if already assigned (first weekend)
                if asign[sab_idx] or asign[dom_idx]:
                    if asign[sab_idx] and asign[sab_idx] == asign[dom_idx]:
                        historico.append(asign[sab_idx])
                    i += 2
                    continue

                candidates = [
                    c
                    for c in self.doctors
                    if not self.medico_esta_de_vacaciones(c, fechas[sab_idx])
                    and not self.medico_esta_de_vacaciones(c, fechas[dom_idx])
                ]
                prev = historico[-1] if historico else ""
                filtered = [c for c in candidates if c != prev]

                med = ""
                if filtered:
                    med = filtered[self.rand_int(len(filtered))]
                elif candidates:
                    med = candidates[self.rand_int(len(candidates))]

                if not med:
                    asign[sab_idx] = UNASSIGNED
                    asign[dom_idx] = UNASSIGNED
                    historico.append(UNASSIGNED)
                else:
                    asign[sab_idx] = med
                    asign[dom_idx] = med
                    historico.append(med)
                i += 2
            else:
                i += 1

    def asignar_fines_de_semana_optimo(self, asign: list[str], fechas: list[date]) -> None:
        mejor = list(asign)
        mejor_dif = _BIG
        mejor_sin_consec = False

        for _attempt in range(ATTEMPTS):
            copy = list(asign)
            self.asignar_fines_de_semana(copy, fechas)
            conteo = self.contar_fines_de_semana_por_medico(copy, fechas)
            dif = _max_min_diff(conteo)
            tiene_cons = self.tiene_fines_de_semana_consecutivos(copy, fechas)

            if (not tiene_cons and (not mejor_sin_consec or dif < mejor_dif)) or (
                tiene_cons and not mejor_sin_consec and dif < mejor_dif
            ):
                mejor_dif = dif
                mejor_sin_consec = not tiene_cons
                mejor = list(copy)

        asign[:] = mejor

    # --- Queue initialisation ---------------------------------------------------------

    def inicializar_cola_por_carga_on_site(self, asign: list[str], fechas: list[date]) -> list[str]:
        cargas = [
            (c, sum(1 for i in range(len(fechas)) if self.es_on_site(fechas[i]) and asign[i] == c))
            for c in self.doctors
        ]
        cargas.sort(key=lambda x: x[1])  # stable, like Array.prototype.sort (ES2019+)
        # Shuffle equal-load blocks
        start = 0
        while start < len(cargas):
            end = start
            while end + 1 < len(cargas) and cargas[end + 1][1] == cargas[start][1]:
                end += 1
            if end > start:
                block = cargas[start : end + 1]
                for i in range(len(block) - 1, 0, -1):
                    j = self.rand_int(i + 1)
                    block[i], block[j] = block[j], block[i]
                for k in range(len(block)):
                    cargas[start + k] = block[k]
            start = end + 1
        return [c for c, _ in cargas]

    def inicializar_cola_por_carga_total(self, asign: list[str]) -> list[str]:
        cargas = [(c, sum(1 for a in asign if a == c)) for c in self.doctors]
        cargas.sort(key=lambda x: x[1])
        return [c for c, _ in cargas]

    # --- Phases 3 and 4: queue-based filling ----------------------------------------------

    def _elegir_medico(
        self, asign: list[str], idx: int, d: date, cola: list[str], cargas_act: list[int]
    ) -> str:
        """Strict pass then flexible pass, shared verbatim by phases 3 and 4 in the JS."""
        mejor_med, carga_min = "", _BIG
        # Strict pass
        for med in cola:
            if not self.medico_esta_de_vacaciones(
                med, d
            ) and not self.medico_asignado_dia_anterior_o_posterior(asign, idx, med):
                i = self.buscar_indice_medico(med)
                if cargas_act[i] < carga_min:
                    mejor_med = med
                    carga_min = cargas_act[i]
        # Flexible pass
        if not mejor_med:
            carga_min = _BIG
            for med in cola:
                if not self.medico_esta_de_vacaciones(med, d):
                    i = self.buscar_indice_medico(med)
                    if cargas_act[i] < carga_min:
                        mejor_med = med
                        carga_min = cargas_act[i]
        return mejor_med

    def asignar_guardias_on_site_con_cola(self, asign: list[str], fechas: list[date]) -> None:
        cola = self.inicializar_cola_por_carga_on_site(asign, fechas)
        for idx in range(len(fechas)):
            if self.es_on_site(fechas[idx]) and not asign[idx]:
                cargas_act = self.contar_on_site_por_medico(asign, fechas)
                mejor_med = self._elegir_medico(asign, idx, fechas[idx], cola, cargas_act)
                if mejor_med:
                    asign[idx] = mejor_med
                    cola = [c for c in cola if c != mejor_med]
                    cola.append(mejor_med)
                else:
                    asign[idx] = UNASSIGNED

    def asignar_guardias_on_site_optimo(self, asign: list[str], fechas: list[date]) -> None:
        mejor = list(asign)
        mejor_dif = _BIG
        for _attempt in range(ATTEMPTS):
            copy = list(asign)
            self.asignar_guardias_on_site_con_cola(copy, fechas)
            dif = _max_min_diff(self.contar_on_site_por_medico(copy, fechas))
            if dif < mejor_dif:
                mejor_dif = dif
                mejor = list(copy)
        asign[:] = mejor

    def asignar_resto_de_dias_con_cola(self, asign: list[str], fechas: list[date]) -> None:
        cola = self.inicializar_cola_por_carga_total(asign)
        for idx in range(len(fechas)):
            if not asign[idx] and not self.es_on_site(fechas[idx]):
                cargas_act = self.contar_dias_por_medico(asign)
                mejor_med = self._elegir_medico(asign, idx, fechas[idx], cola, cargas_act)
                if mejor_med:
                    asign[idx] = mejor_med
                    cola = [c for c in cola if c != mejor_med]
                    cola.append(mejor_med)
                else:
                    asign[idx] = UNASSIGNED

    def asignar_resto_de_dias_optimo(self, asign: list[str], fechas: list[date]) -> None:
        mejor = list(asign)
        diff_mejor = _BIG
        mejor_pen = _BIG
        for _attempt in range(ATTEMPTS):
            copy = list(asign)
            self.asignar_resto_de_dias_con_cola(copy, fechas)
            diff = _max_min_diff(self.contar_dias_por_medico(copy))
            pen = self.calcular_penalizacion_continuidad(copy)
            if diff < diff_mejor or (diff == diff_mejor and pen < mejor_pen):
                diff_mejor = diff
                mejor_pen = pen
                mejor = list(copy)
        asign[:] = mejor

    # --- Main (JS `generate`) ------------------------------------------------------------

    def generar(self, fechas: list[date]) -> list[str]:
        asign = [""] * len(fechas)
        self.asignar_primer_fin_de_semana(asign, fechas)
        self.asignar_fines_de_semana_optimo(asign, fechas)
        self.asignar_guardias_on_site_optimo(asign, fechas)
        self.asignar_resto_de_dias_optimo(asign, fechas)
        return asign


# --- Public API ---------------------------------------------------------------------------


def generate_schedule(
    request: ScheduleRequest, rng: random.Random | None = None
) -> dict[date, str]:
    """Run the four phases and return date → doctor code ("XX" = nobody available).

    Pass a seeded `random.Random` for reproducible results.
    """
    request.validate()
    planificador = _Planificador(request, rng or random.Random())  # noqa: S311 - not crypto
    fechas = build_fechas(request.period_from, request.period_to)
    asign = planificador.generar(fechas)
    return dict(zip(fechas, asign, strict=True))


@dataclass(frozen=True)
class DoctorStats:
    """Per-doctor counters, as in the standalone app's statistics table."""

    code: str
    week: int
    weekend: int
    onsite: int
    vacation_days: int


@dataclass(frozen=True)
class ScheduleStats:
    """Statistics of a schedule; totals are the denominators of the percentages."""

    doctors: list[DoctorStats]
    total_week: int
    total_weekend: int
    total_onsite: int
    unassigned: int

    def spread(self, attribute: str) -> int:
        """max - min of an attribute ("week", "weekend", "onsite") across doctors."""
        return _max_min_diff([getattr(s, attribute) for s in self.doctors])


def is_weekend_for_stats(d: date, public_holidays: Container[date]) -> bool:
    """Saturday, Sunday or public holiday (a weekday holiday counts as a Sunday)."""
    return is_weekend(d) or d in public_holidays


def compute_stats(
    request: ScheduleRequest,
    assignments: Mapping[date, str | None],
    onsite: set[date] | None = None,
) -> ScheduleStats:
    """Port of the JS statistics, with weekday public holidays counted as week-end.

    `onsite` defaults to the request's on-site days; pass the effective set when some
    days were unmarked in the results (JS `esOnSiteEffectivo`).
    """
    onsite_days = request.onsite if onsite is None else onsite
    public = set(request.luxembourg_holidays)
    codes = [d.code for d in request.doctors]
    week = dict.fromkeys(codes, 0)
    weekend = dict.fromkeys(codes, 0)
    on = dict.fromkeys(codes, 0)
    vacation = {c: sum(1 for d, away in request.holidays.items() if c in away) for c in codes}
    total_week = total_weekend = total_onsite = unassigned = 0
    for d in build_fechas(request.period_from, request.period_to):
        code = assignments.get(d)
        we = is_weekend_for_stats(d, public)
        os = d in onsite_days
        if we:
            total_weekend += 1
        else:
            total_week += 1
        if os:
            total_onsite += 1
        if code and code != UNASSIGNED and code in week:
            if we:
                weekend[code] += 1
            else:
                week[code] += 1
            if os:
                on[code] += 1
        else:
            unassigned += 1
    return ScheduleStats(
        doctors=[DoctorStats(c, week[c], weekend[c], on[c], vacation[c]) for c in codes],
        total_week=total_week,
        total_weekend=total_weekend,
        total_onsite=total_onsite,
        unassigned=unassigned,
    )
