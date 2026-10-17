# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""WCAG AA of the desiderata patterns, read from the stylesheet tokens.

The stripes and dots are translucent amber over the cell background; text drawn on them
(day number, ink) must keep 4.5:1 at the most opaque point, and the amber marks (border,
asterisk) 3:1 against their background (non-text contrast, WCAG 1.4.11). Doctor chips are
opaque, so the palettes' own white-text ratios (tests/integration/test_palettes.py) still
hold on striped cells.
"""

import re
from pathlib import Path

import pytest

CSS = (Path(__file__).parents[2] / "app/static/css/app.css").read_text()


def _token(name: str) -> str:
    match = re.search(rf"--{name}:\s*([^;]+);", CSS)
    assert match, name
    return match.group(1).strip()


def _rgb(value: str) -> tuple[float, float, float]:
    value = value.lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _rgba(value: str) -> tuple[tuple[float, float, float], float]:
    r, g, b, a = (float(x) for x in re.findall(r"[\d.]+", value))
    return (r, g, b), a


def _blend(top: str, base: str) -> tuple[float, float, float]:
    color, alpha = _rgba(top)
    under = _rgb(base)
    return tuple(alpha * c + (1 - alpha) * u for c, u in zip(color, under, strict=True))  # type: ignore[return-value]


def _luminance(rgb: tuple[float, float, float]) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


@pytest.mark.parametrize("background", ["surface", "weekend", "onsite-soft", "desiderata-soft"])
def test_ink_readable_on_amber_marks(background):
    striped = _blend(_token("desiderata-stripe"), _token(background))
    assert _ratio(_rgb(_token("ink")), striped) >= 4.5


@pytest.mark.parametrize("background", ["surface", "weekend", "onsite-soft", "desiderata-soft"])
def test_amber_marks_visible(background):
    """Border / asterisk of the desiderata against the (striped) background."""
    striped = _blend(_token("desiderata-stripe"), _token(background))
    assert _ratio(_rgb(_token("desiderata")), striped) >= 3
    assert _ratio(_rgb(_token("desiderata")), _rgb(_token(background))) >= 3
