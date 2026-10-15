# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Doctor color palettes a clinic can choose from (colors in app/services/palettes.py)."""

import enum


class ColorPalette(enum.StrEnum):
    """Stored in clinic.color_palette."""

    DEFAULT = "default"
    GRAYSCALE = "grayscale"
    BLUE = "blue"
    PINK_PURPLE = "pink_purple"
