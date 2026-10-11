# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Juan Manuel Suárez – Arrakis IT Services

"""Clinic: the tenant. Every scoped row references a clinic."""

from sqlalchemy import Boolean, Text, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Clinic(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A medical clinic (tenant)."""

    __tablename__ = "clinic"

    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=true())
