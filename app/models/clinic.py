# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic: the tenant. Every scoped row references a clinic."""

from sqlalchemy import Boolean, Enum, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.palette import ColorPalette


class Clinic(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A medical clinic (tenant)."""

    __tablename__ = "clinic"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
    # Doctor colors of the clinic come from this palette (app/services/palettes.py).
    color_palette: Mapped[ColorPalette] = mapped_column(
        Enum(
            ColorPalette,
            name="color_palette",
            native_enum=False,
            length=20,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ColorPalette.DEFAULT,
        server_default=ColorPalette.DEFAULT.value,
    )
