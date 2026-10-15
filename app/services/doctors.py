# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Doctor identity helpers: initials code and color assignment within a clinic."""

import itertools
import string
import unicodedata
from collections.abc import Iterable, Iterator

# "XX" marks an unassigned day in schedules; never give it to a doctor.
RESERVED_CODES = frozenset({"XX"})

# 12 muted colors, assigned round-robin within a clinic (see brief §6.2). Each one keeps
# a WCAG AA contrast (≥ 4.5:1) with the white initials drawn on top of it.
DOCTOR_PALETTE: tuple[str, ...] = (
    "#2d4a3e",  # oak green
    "#8c5e3a",  # terracotta
    "#5c6ba8",  # dusty blue
    "#a84d4d",  # muted red
    "#478062",  # sage
    "#84732b",  # mustard
    "#6b4d8c",  # plum
    "#457d7d",  # teal
    "#8c4d7a",  # mauve
    "#6a7a43",  # olive
    "#8c6b4d",  # bronze
    "#4d6b8c",  # slate
)


class DoctorCodeError(ValueError):
    """No usable code could be derived from the name."""


def _name_words(full_name: str) -> list[str]:
    """Uppercase ASCII letters of each word (accents stripped, punctuation dropped)."""
    normalized = unicodedata.normalize("NFKD", full_name)
    ascii_name = "".join(c for c in normalized if not unicodedata.combining(c))
    words = []
    for raw in ascii_name.replace("-", " ").replace("'", " ").split():
        letters = "".join(c for c in raw.upper() if c in string.ascii_uppercase)
        if letters:
            words.append(letters)
    return words


def _candidates(words: list[str]) -> Iterator[str]:
    """Candidate codes, most natural first."""
    first, last = words[0], words[-1]
    if len(words) == 1:
        yield first[:2]
        yield first[:3]
        for c in first[2:]:
            yield first[:2] + c
    else:
        yield first[0] + last[0]
        if len(words) >= 3:
            yield "".join(w[0] for w in words[:3])
        for c in last[1:]:
            yield first[0] + last[0] + c
        for c in first[1:]:
            yield first[0] + c + last[0]
    # Last resort: first initial + last initial + any letter.
    for c in string.ascii_uppercase:
        yield first[0] + last[0] + c
    for a, b in itertools.product(string.ascii_uppercase, repeat=2):
        yield first[0] + a + b


def generate_doctor_code(full_name: str, taken: Iterable[str]) -> str:
    """Derive a 2–3 letter code from `full_name`, unique among `taken` (case-insensitive).

    Examples: "Nicolas Rousseau" -> "NR"; if taken -> "NRO"; "Jean-Pierre Martin" -> "JM".
    """
    words = _name_words(full_name)
    if not words or (len(words) == 1 and len(words[0]) < 2):
        raise DoctorCodeError("Le nom doit contenir au moins deux lettres.")
    used = {code.upper() for code in taken} | RESERVED_CODES
    for code in _candidates(words):
        if 2 <= len(code) <= 3 and code not in used:
            return code
    raise DoctorCodeError("Aucun code disponible pour ce nom.")  # pragma: no cover


def pick_color(used_colors: Iterable[str], palette: tuple[str, ...] = DOCTOR_PALETTE) -> str:
    """First palette color not yet used in the clinic; cycles once the palette is exhausted."""
    used = [c.lower() for c in used_colors]
    for color in palette:
        if color not in used:
            return color
    return palette[len(used) % len(palette)]
