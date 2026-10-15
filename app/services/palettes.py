# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Doctor color palettes per clinic.

Every color keeps a WCAG AA contrast (≥ 4.5:1) with the white initials drawn on top of it.
Ratios against #ffffff (WCAG 2.x relative luminance) are noted next to each color.

- default ("Par défaut"): the 12 colors used since the beginning, unchanged.
- grayscale ("Nuances de gris"): 10 neutral grays from #121212 to #757575 in even steps
  (#757575 is the lightest gray still at 4.5:1).
- blue ("Bleus"): Material Design Blue 700–900, Light Blue 900, Indigo 700–900 and
  Blue Grey 700–900.
- pink_purple ("Rose et violet"): Material Design Pink 700–900, Purple 600–900 and
  Deep Purple 700–900.

Colors are assigned by creation order (index modulo the palette size). A clinic can change
its palette (every doctor then gets the color of their index in the new palette, manual
choices are reset) and a group admin or superadmin can pick any color of the active
palette for one doctor.
"""

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Clinic, User
from app.models.palette import ColorPalette
from app.services import audit


@dataclass(frozen=True)
class Palette:
    """A named list of (hex, short French name) colors."""

    key: ColorPalette
    label: str
    colors: tuple[tuple[str, str], ...]

    @property
    def hexes(self) -> tuple[str, ...]:
        """Colors only, in assignment order."""
        return tuple(color for color, _ in self.colors)

    def name_of(self, color: str) -> str:
        """Short name of a color of this palette ("" if not in it)."""
        return dict(self.colors).get(color.lower(), "")


PALETTES: dict[ColorPalette, Palette] = {
    ColorPalette.DEFAULT: Palette(
        ColorPalette.DEFAULT,
        "Par défaut",
        (
            ("#2d4a3e", "Vert chêne"),  # 9.72:1
            ("#8c5e3a", "Terre cuite"),  # 5.56:1
            ("#5c6ba8", "Bleu poudré"),  # 5.09:1
            ("#a84d4d", "Rouge sourd"),  # 5.48:1
            ("#478062", "Sauge"),  # 4.63:1
            ("#84732b", "Moutarde"),  # 4.70:1
            ("#6b4d8c", "Prune"),  # 6.85:1
            ("#457d7d", "Sarcelle"),  # 4.68:1
            ("#8c4d7a", "Mauve"),  # 6.07:1
            ("#6a7a43", "Olive"),  # 4.69:1
            ("#8c6b4d", "Bronze"),  # 4.86:1
            ("#4d6b8c", "Ardoise"),  # 5.53:1
        ),
    ),
    ColorPalette.GRAYSCALE: Palette(
        ColorPalette.GRAYSCALE,
        "Nuances de gris",
        (
            ("#121212", "Noir"),  # 18.73:1
            ("#1d1d1d", "Gris 1"),  # 16.86:1
            ("#282828", "Gris 2"),  # 14.74:1
            ("#333333", "Gris 3"),  # 12.63:1
            ("#3e3e3e", "Gris 4"),  # 10.70:1
            ("#494949", "Gris 5"),  # 9.00:1
            ("#545454", "Gris 6"),  # 7.57:1
            ("#5f5f5f", "Gris 7"),  # 6.39:1
            ("#6a6a6a", "Gris 8"),  # 5.41:1
            ("#757575", "Gris 9"),  # 4.61:1
        ),
    ),
    ColorPalette.BLUE: Palette(
        ColorPalette.BLUE,
        "Bleus",
        (
            ("#1976d2", "Bleu"),  # 4.60:1  Material Blue 700
            ("#1565c0", "Bleu foncé"),  # 5.75:1  Blue 800
            ("#0d47a1", "Bleu nuit"),  # 8.63:1  Blue 900
            ("#01579b", "Bleu océan"),  # 7.40:1  Light Blue 900
            ("#303f9f", "Indigo"),  # 8.98:1  Indigo 700
            ("#283593", "Indigo foncé"),  # 10.39:1 Indigo 800
            ("#1a237e", "Indigo nuit"),  # 13.24:1 Indigo 900
            ("#455a64", "Bleu-gris"),  # 7.24:1  Blue Grey 700
            ("#37474f", "Bleu-gris foncé"),  # 9.65:1  Blue Grey 800
            ("#263238", "Ardoise nuit"),  # 13.16:1 Blue Grey 900
        ),
    ),
    ColorPalette.PINK_PURPLE: Palette(
        ColorPalette.PINK_PURPLE,
        "Rose et violet",
        (
            ("#c2185b", "Framboise"),  # 5.87:1  Material Pink 700
            ("#ad1457", "Fuchsia foncé"),  # 6.97:1  Pink 800
            ("#880e4f", "Bordeaux rosé"),  # 9.45:1  Pink 900
            ("#8e24aa", "Orchidée"),  # 7.04:1  Purple 600
            ("#7b1fa2", "Violet"),  # 8.20:1  Purple 700
            ("#6a1b9a", "Violet foncé"),  # 9.39:1  Purple 800
            ("#4a148c", "Aubergine"),  # 11.86:1 Purple 900
            ("#512da8", "Pourpre"),  # 9.17:1  Deep Purple 700
            ("#4527a0", "Pourpre foncé"),  # 10.24:1 Deep Purple 800
            ("#311b92", "Indigo violet"),  # 12.34:1 Deep Purple 900
        ),
    ),
}


class PaletteError(ValueError):
    """Invalid palette or color (French message, answered with 400)."""


def palette_of(clinic: Clinic) -> Palette:
    """The clinic's active palette."""
    return PALETTES[ColorPalette(clinic.color_palette)]


def color_for_index(palette: Palette, index: int) -> str:
    """Color of the `index`-th doctor (creation order), cycling through the palette."""
    return palette.hexes[index % len(palette.hexes)]


async def clinic_doctors_by_creation(db: AsyncSession, clinic_id: uuid.UUID) -> list[User]:
    """Every user of the clinic (active or not), oldest first."""
    stmt = select(User).where(User.clinic_id == clinic_id).order_by(User.created_at, User.id)
    return list((await db.scalars(stmt)).all())


async def change_palette(
    db: AsyncSession,
    clinic: Clinic,
    value: str,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> bool:
    """Switch the clinic's palette and recolor every doctor by creation order (manual
    choices are lost). Returns False if the palette was already active. Caller commits."""
    try:
        new = ColorPalette(value)
    except ValueError:
        raise PaletteError("Palette inconnue.") from None
    previous = ColorPalette(clinic.color_palette)
    if new == previous:
        return False
    clinic.color_palette = new
    palette = PALETTES[new]
    doctors = await clinic_doctors_by_creation(db, clinic.id)
    for index, doctor in enumerate(doctors):
        doctor.color = color_for_index(palette, index)
    audit.record(
        db,
        "clinic_palette_changed",
        request=request,
        user_id=actor_id,
        clinic_id=clinic.id,
        target_type="clinic",
        target_id=clinic.id,
        meta={"previous": previous.value, "new": new.value, "doctors": len(doctors)},
    )
    return True


async def set_doctor_color(
    db: AsyncSession,
    clinic: Clinic,
    doctor: User,
    color: str,
    *,
    actor_id: uuid.UUID,
    request: Request | None = None,
) -> bool:
    """Give one doctor a color of the clinic's active palette (manual override). Returns
    False if unchanged. Caller commits."""
    color = color.strip().lower()
    palette = palette_of(clinic)
    if doctor.clinic_id != clinic.id:
        raise PaletteError("Ce médecin n'appartient pas à ce groupe.")
    if color not in palette.hexes:
        raise PaletteError("Cette couleur ne fait pas partie de la palette du groupe.")
    if doctor.color.lower() == color:
        return False
    old = doctor.color
    doctor.color = color
    audit.record(
        db,
        "doctor_color_changed",
        request=request,
        user_id=actor_id,
        clinic_id=clinic.id,
        target_type="user",
        target_id=doctor.id,
        meta={"old": old, "new": color, "palette": palette.key.value},
    )
    return True
