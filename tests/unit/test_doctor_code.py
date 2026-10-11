# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Tests for doctor code generation and color assignment."""

import pytest

from app.services.doctors import (
    DOCTOR_PALETTE,
    DoctorCodeError,
    generate_doctor_code,
    pick_color,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Nicolas Rousseau", "NR"),
        ("Dominique Thill", "DT"),
        ("Jean-Pierre Martin", "JM"),
        ("Élodie Ângelo", "EA"),
        ("  marie   curie ", "MC"),
        ("Madonna", "MA"),
        ("Anne O'Neil", "AN"),
    ],
)
def test_initials(name, expected):
    assert generate_doctor_code(name, []) == expected


def test_collision_uses_third_letter_of_last_name():
    assert generate_doctor_code("Nicolas Rousseau", ["NR"]) == "NRO"
    assert generate_doctor_code("Nicolas Rousseau", ["NR", "NRO"]) == "NRU"


def test_three_word_name_tries_three_initials_after_two():
    assert generate_doctor_code("Anne Marie Dupont", ["AD"]) == "AMD"


def test_collision_is_case_insensitive():
    assert generate_doctor_code("Nicolas Rousseau", ["nr"]) == "NRO"


def test_never_returns_reserved_xx():
    assert generate_doctor_code("Xavier Xu", []) != "XX"


def test_code_is_always_2_or_3_uppercase_letters():
    taken: list[str] = []
    for _ in range(40):
        code = generate_doctor_code("Nicolas Rousseau", taken)
        assert code.isalpha() and code.isupper() and 2 <= len(code) <= 3
        assert code not in taken
        taken.append(code)


@pytest.mark.parametrize("name", ["", "   ", "J", "123 !!"])
def test_unusable_names_raise(name):
    with pytest.raises(DoctorCodeError):
        generate_doctor_code(name, [])


def test_pick_color_round_robin():
    assert pick_color([]) == DOCTOR_PALETTE[0]
    assert pick_color([DOCTOR_PALETTE[0]]) == DOCTOR_PALETTE[1]
    # A freed color is reused first.
    assert pick_color([DOCTOR_PALETTE[1]]) == DOCTOR_PALETTE[0]


def test_pick_color_cycles_when_palette_exhausted():
    used = list(DOCTOR_PALETTE)
    assert pick_color(used) == DOCTOR_PALETTE[0]
    assert pick_color([*used, DOCTOR_PALETTE[0]]) == DOCTOR_PALETTE[1]
